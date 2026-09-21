from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from smearglepaper.harness import ConversationHarness
from smearglepaper.harness_events import HarnessEvent
from smearglepaper.harness_projection import HarnessProjection
from smearglepaper.llm import stream_chat


class _StreamResponse:
    def __init__(self, rows: list[bytes], request_id: str = "req-stage2") -> None:
        self.rows = rows
        self.headers = {"x-request-id": request_id}
        self.closed = False

    def __iter__(self):
        return iter(self.rows)

    def close(self) -> None:
        self.closed = True


class Stage2StreamingTests(unittest.TestCase):
    def test_provider_events_include_connection_first_token_usage_and_request_id(self) -> None:
        response = _StreamResponse([
            b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":2,"completion_tokens":1}}\n\n',
            b"data: [DONE]\n\n",
        ])
        provider_events: list[tuple[str, dict[str, object]]] = []
        with patch.dict("os.environ", {"OPENAI_BASE_URL": "https://example.test/v1", "OPENAI_API_KEY": "test", "OPENAI_MODEL": "test-model"}):
            with patch("smearglepaper.llm.urllib.request.urlopen", return_value=response):
                result = stream_chat(
                    [{"role": "user", "content": "hi"}],
                    on_provider_event=lambda kind, payload: provider_events.append((kind, payload)),
                )
        self.assertEqual(result["request_id"], "req-stage2")
        self.assertEqual(result["usage"], {"prompt_tokens": 2, "completion_tokens": 1})
        self.assertEqual(result["content"], "hello")
        self.assertEqual([kind for kind, _ in provider_events], ["connected", "event", "first_token", "event"])
        self.assertIn("latency_ms", provider_events[2][1])
        self.assertTrue(response.closed)

    def test_projection_tracks_first_token_usage_and_stalled_state(self) -> None:
        projection = HarnessProjection()
        base = {"session_id": "s", "turn_id": "t", "source": "harness"}
        events = [
            HarnessEvent.create("turn.started", sequence=1, payload={}, **base),
            HarnessEvent.create("model.first_token", sequence=2, payload={"latency_ms": 123}, **base),
            HarnessEvent.create("model.stalled", sequence=3, payload={}, **base),
            HarnessEvent.create("model.completed", sequence=4, payload={"usage": {"completion_tokens": 4}, "request_id": "r1"}, **base),
        ]
        for event in events:
            projection.apply(event)
        self.assertEqual(projection.state.first_token_ms, 123.0)
        self.assertEqual(projection.state.usage["completion_tokens"], 4)
        self.assertEqual(projection.state.request_id, "r1")
        self.assertFalse(projection.state.stalled)

    def test_harness_emits_tool_progress_and_stalled_once(self) -> None:
        events = []
        response = {"content": "", "finish_reason": "tool_calls", "usage": {}, "tool_calls": [
            {"id": "c1", "name": "slow", "arguments": {}},
        ]}

        def fake_stream(messages, on_delta, **kwargs):
            return response if len(messages) < 3 else {"content": "done", "tool_calls": [], "usage": {}}

        def slow_tool(name: str, arguments: dict[str, object]) -> str:
            time.sleep(0.12)
            return "done"

        with patch("smearglepaper.harness.stream_chat", side_effect=fake_stream):
            ConversationHarness("s").run(
                "run",
                system_prompt="system",
                on_event=events.append,
                execute_tool=slow_tool,
                max_rounds=2,
                stalled_after_seconds=0.03,
                heartbeat_interval_seconds=0.02,
            )
        types = [event.type for event in events]
        self.assertIn("tool.progress", types)
        self.assertEqual(types.count("tool.stalled"), 1)

    def test_harness_passes_cancel_boundary_to_provider_and_does_not_continue_tools(self) -> None:
        events = []
        harness = ConversationHarness("s")
        captured: dict[str, object] = {}

        def fake_stream(messages, on_delta, **kwargs):
            captured.update(kwargs)
            harness.cancel()
            return {"content": "partial", "finish_reason": "stop", "usage": {}, "tool_calls": []}

        with patch("smearglepaper.harness.stream_chat", side_effect=fake_stream):
            result = harness.run("cancel", system_prompt="system", on_event=events.append)
        self.assertEqual(result, "")
        self.assertIsInstance(captured["cancel_event"], threading.Event)
        self.assertEqual(events[-1].type, "turn.cancelled")


if __name__ == "__main__":
    unittest.main()
