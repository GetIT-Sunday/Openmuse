from __future__ import annotations

import asyncio
import io
import json
import threading
import time
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from smearglepaper.cancellation import CancellationToken, Cancelled, cancellation_scope, current_token
from smearglepaper.harness import ConversationHarness
from smearglepaper.harness_events import EventJournal, HarnessEvent
from smearglepaper.harness_projection import HarnessProjection
from smearglepaper.providers import AnthropicAdapter, OpenAIAdapter, ProviderError, iter_sse, stream
from smearglepaper.runtime import AgentRuntime, RunRequest, ToolSpec, WorkflowDefinition, WorkflowStep
from smearglepaper.runtime.models import ToolOutcome
from smearglepaper.runtime.registry import ToolRegistry


class Response(io.BytesIO):
    headers = {"x-request-id": "r-test"}


def sse(*events):
    return Response("".join("data: " + json.dumps(item) + "\n\n" for item in events).encode())


def chat_events(text="你好"):
    return [{"choices": [{"delta": {"content": text}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"completion_tokens": 2}}]


def run_provider(response, *, adapter=None, token=None, notify=None, **kwargs):
    with patch("urllib.request.urlopen", return_value=response):
        return stream(adapter or OpenAIAdapter("https://example.test/v1", {}), [], {
            "model": "test", "max_tokens": 128, "temperature": 0.3,
        }, token=token or CancellationToken(), notify=notify or (lambda *_: None), on_delta=None, **kwargs)


def test_anthropic_stream_converts_tool_round_and_usage():
    adapter = AnthropicAdapter("https://example.test/v1", "secret")
    request = adapter.request([
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "find", "arguments": '{"query":"x"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "found"},
    ], {"model": "claude", "max_tokens": 10, "temperature": 0.3})
    assert request.full_url == "https://example.test/v1/messages"
    wire = json.loads(request.data)
    assert wire["messages"][1]["content"][0]["type"] == "tool_result"
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 8}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "c2", "name": "find", "input": {}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"q":'}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '"论文"}'}},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 6}},
    ]
    result = run_provider(sse(*events), adapter=adapter)
    assert result["tool_calls"][0]["arguments"] == {"q": "论文"}
    assert result["usage"] == {"input_tokens": 8, "output_tokens": 6}
    result = run_provider(sse(
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "文章"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}},
    ), adapter=adapter)
    assert result["content"] == "文章"


def test_sse_multiline_and_eof():
    assert list(iter_sse(io.BytesIO(b'data: {"choices":\ndata: []}\n\ndata: [DONE]\n\n'))) == [{"choices": []}]
    assert list(iter_sse(io.BytesIO(b'data: {"usage": {}}'))) == [{"usage": {}}]


def test_retry_before_output_and_auth_not_retried():
    events = []
    with patch("urllib.request.urlopen", side_effect=[
        HTTPError("https://example.test", 429, "limit", {}, None), sse(*chat_events())
    ]) as opened:
        result = stream(OpenAIAdapter("https://example.test/v1", {}), [], {}, token=CancellationToken(),
                        notify=lambda kind, data: events.append((kind, data)), on_delta=None, retry_base=0)
    assert opened.call_count == 2 and result["content"] == "你好"
    assert any(kind == "retrying" for kind, _ in events)
    with patch("urllib.request.urlopen", side_effect=HTTPError("https://example.test", 401, "secret", {}, None)) as opened:
        with pytest.raises(ProviderError, match="认证"):
            stream(OpenAIAdapter("https://example.test/v1", {}), [], {}, token=CancellationToken(), notify=lambda *_: None, on_delta=None)
    assert opened.call_count == 1


def test_partial_output_never_retries_and_bad_tool_arguments_never_execute():
    response = sse(chat_events()[0])
    with patch("urllib.request.urlopen", return_value=response) as opened:
        with pytest.raises(ProviderError) as failure:
            stream(OpenAIAdapter("https://example.test/v1", {}), [], {}, token=CancellationToken(), notify=lambda *_: None, on_delta=None)
    assert failure.value.code == "incomplete_stream" and opened.call_count == 1
    with pytest.raises(ProviderError) as failure:
        run_provider(sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c", "function": {"name": "x", "arguments": "{"}}]},
                                       "finish_reason": "tool_calls"}]}))
    assert failure.value.code == "tool_arguments"


def test_cancel_during_connect_returns_without_late_callbacks():
    token = CancellationToken()
    connecting, release = threading.Event(), threading.Event()
    response = sse(*chat_events())
    def connect(*args, **kwargs):
        connecting.set()
        release.wait(2)
        return response
    def cancel():
        assert connecting.wait(1)
        token.cancel()
    thread = threading.Thread(target=cancel)
    thread.start()
    events = []
    try:
        started = time.monotonic()
        with patch("urllib.request.urlopen", side_effect=connect):
            result = stream(OpenAIAdapter("https://example.test/v1", {}), [], {}, token=token,
                            notify=lambda *item: events.append(item), on_delta=None)
        assert time.monotonic() - started < 1
        assert result["cancelled"] and events == []
    finally:
        release.set()
        thread.join()


def test_cancel_during_retry_does_not_open_second_request():
    token = CancellationToken()
    def notify(kind, data):
        if kind == "retrying":
            token.cancel()
    with patch("urllib.request.urlopen", side_effect=HTTPError("x", 503, "busy", {}, None)) as opened:
        result = stream(OpenAIAdapter("https://example.test/v1", {}), [], {}, token=token, notify=notify, on_delta=None)
    assert result["cancelled"] and opened.call_count == 1


def test_cancel_blocked_read_closes_eventually_and_discards_late_output():
    token = CancellationToken()
    reading, release, closed = threading.Event(), threading.Event(), threading.Event()
    class Blocked:
        headers = {}
        def __iter__(self):
            reading.set()
            release.wait(2)
            yield b'data: {"choices":[{"delta":{"content":"late"},"finish_reason":"stop"}]}\n\n'
        def close(self):
            closed.set()
    def cancel():
        reading.wait(1)
        token.cancel()
    thread = threading.Thread(target=cancel)
    thread.start()
    deltas = []
    try:
        with patch("urllib.request.urlopen", return_value=Blocked()):
            result = stream(OpenAIAdapter("https://example.test/v1", {}), [], {}, token=token,
                            notify=lambda *_: None, on_delta=deltas.append)
        assert result["cancelled"] and deltas == []
    finally:
        release.set()
        thread.join()
        assert closed.wait(1)


def test_model_stall_recovers_and_terminal_is_last():
    events = []
    stalled = threading.Event()
    def emit(event):
        events.append(event)
        if event.type == "model.stalled":
            stalled.set()
    def model(messages, on_delta, **kwargs):
        assert stalled.wait(1)
        on_delta("恢复")
        return {"content": "恢复"}
    with patch("smearglepaper.harness.stream_chat", side_effect=model):
        ConversationHarness("s").run("go", system_prompt="s", on_event=emit,
                                     stalled_after_seconds=0.02, heartbeat_interval_seconds=0.01)
    assert sum(e.type == "model.stalled" for e in events) == 1
    assert events[-1].type == "turn.completed"
    projection = HarnessProjection()
    projection.replay(events)
    assert not projection.state.stalled and projection.state.assistant_text == "恢复"


def test_noncooperative_tool_cancels_at_safe_boundary_without_next_tool():
    harness = ConversationHarness("s")
    entered, finish = threading.Event(), threading.Event()
    events, executed = [], []
    def emit(event):
        events.append(event)
        if event.type == "turn.cancelling":
            finish.set()
    def cancel():
        entered.wait(1)
        harness.cancel()
    def tool(name, arguments):
        executed.append(name)
        entered.set()
        assert finish.wait(1)
        return "done"
    thread = threading.Thread(target=cancel)
    thread.start()
    try:
        with patch("smearglepaper.harness.stream_chat", return_value={"tool_calls": [
            {"id": "1", "name": "one", "arguments": {}}, {"id": "2", "name": "two", "arguments": {}}]}):
            harness.run("go", system_prompt="s", on_event=emit, execute_tool=tool)
    finally:
        thread.join()
    assert executed == ["one"]
    assert events[-1].type == "turn.cancelled"
    assert not any(e.type == "tool.completed" for e in events)


def test_round_limit_is_failure_not_success():
    events = []
    harness = ConversationHarness("s")
    with patch("smearglepaper.harness.stream_chat", return_value={"tool_calls": [{"id": "c", "name": "x", "arguments": {}}]}):
        with pytest.raises(RuntimeError, match="上限"):
            harness.run("go", system_prompt="s", on_event=events.append, execute_tool=lambda *_: "ok", max_rounds=1)
    assert events[-1].type == "turn.failed"
    assert len(harness.memory.turns) == 1


def test_delta_batching_preserves_text_and_terminal_journal_order(tmp_path):
    journal = EventJournal(tmp_path / "events.jsonl")
    events = []
    def model(messages, on_delta, **kwargs):
        for _ in range(1000):
            on_delta("字")
        return {"content": "字" * 1000, "usage": {}}
    with patch("smearglepaper.harness.stream_chat", side_effect=model):
        ConversationHarness("s", event_journal=journal).run("go", system_prompt="s", on_event=events.append)
    deltas = [e for e in events if e.type == "model.delta"]
    assert "".join(e.payload["text"] for e in deltas) == "字" * 1000
    assert len(deltas) < 50
    assert events[-1].type == "turn.completed"
    assert [e.id for e in journal.events()] == [e.id for e in events]
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))
    assert HarnessProjection().replay(events).assistant_text == "字" * 1000


def test_child_tool_cancel_and_failure_do_not_emit_false_completion():
    harness = ConversationHarness("s")
    events = []
    calls = [{"id": "c", "name": "slow", "arguments": {}}, {"id": "later", "name": "later", "arguments": {}}]
    def tool(name, arguments):
        assert name == "slow"
        token = current_token()
        assert token is not None and token.parent is not None
        harness.cancel()
        token.check()
        return ""
    with patch("smearglepaper.harness.stream_chat", return_value={"tool_calls": calls}):
        assert harness.run("go", system_prompt="s", on_event=events.append, execute_tool=tool) == ""
    assert events[-1].type == "turn.cancelled"
    assert not any(e.type in {"tool.completed", "turn.failed"} for e in events)
    assert len(harness.memory.turns) == 1


def test_failed_tool_keeps_failed_state():
    events = []
    responses = iter([{"tool_calls": [{"id": "c", "name": "bad", "arguments": {}}]}, {"content": "failed"}])
    with patch("smearglepaper.harness.stream_chat", side_effect=lambda *a, **kw: next(responses)):
        ConversationHarness("s").run("go", system_prompt="s", on_event=events.append,
                                     execute_tool=lambda *_: (_ for _ in ()).throw(ValueError("bad")))
    assert any(e.type == "tool.failed" for e in events)
    assert not any(e.type == "tool.completed" for e in events)
    assert HarnessProjection().replay(events).tool_states["bad"] == "failed"


def test_deadline_and_cancel_are_not_local_template_fallbacks():
    from smearglepaper.llm import ArticleWriter
    from smearglepaper.models import PaperMeta
    with cancellation_scope(CancellationToken()):
        with patch("smearglepaper.llm._detect_api_provider", return_value="openai"):
            with patch.object(ArticleWriter, "_call_model", side_effect=Cancelled()):
                with pytest.raises(Cancelled):
                    ArticleWriter().write(PaperMeta.from_dict({"paper_id": "x", "title": "x", "url": "x"}))
    token = CancellationToken(timeout=0)
    with pytest.raises(Cancelled, match="deadline"):
        token.check()


def test_runtime_cancellation_reaches_tool_without_failed_checkpoint(tmp_path):
    registry = ToolRegistry()
    def handler(context):
        assert context.cancellation is current_token()
        runtime.cancel(context.run_id)
        context.cancellation.check()
        return ToolOutcome()
    registry.register(ToolSpec("one"), handler)
    runtime = AgentRuntime(tmp_path, workflow=object(), registry=registry,
                           workflows={"test": WorkflowDefinition("test", "test", (WorkflowStep("one", "one", "writer"),))})
    result = runtime.run(RunRequest("test"))
    assert result.status == "cancelled"
    assert not any(e.type == "run.failed" for e in runtime.events(result.run_id))
    assert not runtime.store.load_checkpoint(result.run_id, "one")


def test_runtime_persists_cancel_request_before_signalling_worker(tmp_path):
    runtime = AgentRuntime(tmp_path, workflow=object())
    created = runtime.create_run(RunRequest("paper-research"))
    token = CancellationToken()
    runtime._cancellations[created.run_id] = token

    def finish_cancel():
        manifest = runtime.store.load(created.run_id)
        assert manifest["status"] == "cancelling"
        assert runtime.events(created.run_id)[-1].payload["status"] == "cancelling"
        manifest["status"] = "cancelled"
        runtime.store.save(created.run_id, manifest)

    with patch.object(token, "cancel", side_effect=finish_cancel):
        result = runtime.cancel(created.run_id)
    assert result.status == "cancelled"


def test_tui_uses_projection_for_retry_cancel_and_terminal():
    from smearglepaper.tui import SmearglePaperApp
    async def exercise():
        app = SmearglePaperApp()
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            for i, (kind, payload) in enumerate([
                ("turn.started", {}), ("provider.retrying", {"delay_seconds": 1, "attempt": 2}),
                ("turn.cancelling", {}), ("heartbeat", {"elapsed_seconds": 20, "operation": "model"}),
                ("turn.cancelled", {}), ("model.stalled", {}),
            ], 1):
                event = HarnessEvent.create(kind, session_id="s", turn_id="t", sequence=i, source="harness", payload=payload)
                screen._handle_conversation_event(event)
                assert screen._activity_message.content == screen._harness_projection.state.activity
            assert screen._harness_projection.state.status == "cancelled"
            await pilot.pause()
    asyncio.run(exercise())
