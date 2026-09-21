from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from test_runtime_workflows import FakePaperWorkflow

from smearglepaper.context_budget import ContextBudget, ContextBudgetExceeded, estimate_tokens
from smearglepaper.conversation import ConversationController
from smearglepaper.harness import ConversationHarness, ConversationMemory
from smearglepaper.harness_events import EventJournal, HarnessEvent
from smearglepaper.runtime import AgentRuntime, RunRequest, ToolSpec, WorkflowDefinition, WorkflowStep
from smearglepaper.runtime.models import ToolOutcome
from smearglepaper.runtime.registry import ToolRegistry
from smearglepaper.turn_store import RecoveryRequired, TurnStore


class Crash(BaseException):
    pass


def harness_at(root, *, budget=None):
    return ConversationHarness("s", ConversationMemory(root / "memory.json"),
                               event_journal=EventJournal(root / "events.jsonl"), context_budget=budget)


def call_response(name="read_article", args=None):
    return {"content": "", "tool_calls": [{"id": "call-1", "name": name, "arguments": args or {}}]}


def test_reuses_saved_tool_result_after_crash_before_next_model(tmp_path):
    harness = harness_at(tmp_path)
    executed = Mock(return_value="saved result")
    def event(event):
        if event.type == "tool.completed":
            raise Crash()
    with patch("smearglepaper.harness.stream_chat", return_value=call_response()):
        with pytest.raises(Crash):
            harness.run("read", system_prompt="sys", on_event=event, execute_tool=executed)
    restored = harness_at(tmp_path)
    captured = []
    def final(messages, **kwargs):
        captured.extend(messages)
        return {"content": "final"}
    with patch("smearglepaper.harness.stream_chat", side_effect=final):
        assert restored.run("", system_prompt="sys", on_event=lambda e: None, execute_tool=executed, resume=True) == "final"
    assert executed.call_count == 1
    assert any(m.get("role") == "tool" and m["content"] == "saved result" for m in captured)
    assert [t["role"] for t in restored.memory.turns] == ["user", "assistant"]
    assert restored.turn_store.load()["status"] == "completed"


def test_full_answer_saved_before_memory_commit_resumes_without_model(tmp_path):
    harness = harness_at(tmp_path)
    original = harness.memory.append
    def append(role, content, **kwargs):
        original(role, content, **kwargs)
        if role == "assistant":
            raise Crash()
    with patch.object(harness.memory, "append", side_effect=append), patch("smearglepaper.harness.stream_chat", return_value={"content": "answer"}):
        with pytest.raises(Crash):
            harness.run("hello", system_prompt="sys", on_event=lambda e: None)
    restored = harness_at(tmp_path)
    with patch("smearglepaper.harness.stream_chat") as model:
        assert restored.run("", system_prompt="sys", on_event=lambda e: None, resume=True) == "answer"
    model.assert_not_called()
    assert len([m for m in restored.memory.turns if m["role"] == "assistant"]) == 1


def test_unknown_generic_tool_never_replays_without_recovery_adapter(tmp_path):
    harness = harness_at(tmp_path)
    execute = Mock(side_effect=Crash())
    with patch("smearglepaper.harness.stream_chat", return_value=call_response()):
        with pytest.raises(Crash):
            harness.run("read", system_prompt="sys", on_event=lambda e: None, execute_tool=execute)
    restored = harness_at(tmp_path)
    with pytest.raises(RecoveryRequired), patch("smearglepaper.harness.stream_chat") as provider:
        restored.run("", system_prompt="sys", on_event=lambda e: None, execute_tool=execute, resume=True)
    assert execute.call_count == 1
    provider.assert_not_called()
    with pytest.raises(RecoveryRequired):
        restored.run("new request", system_prompt="sys", on_event=lambda e: None)


def test_controller_recovers_existing_runtime_without_repeating_research(tmp_path):
    workflow = FakePaperWorkflow(tmp_path)
    workflow.collector = Mock(fetch_by_id=Mock(return_value=workflow._resolve_paper()))
    runtime = AgentRuntime(tmp_path / "runtime", workflow=workflow)
    controller = ConversationController(runtime, harness_at(tmp_path))
    with patch.object(workflow, "read", wraps=workflow.read) as read:
        with patch.object(workflow, "run_writing_agent", side_effect=Crash()), \
             patch("smearglepaper.harness.stream_chat", return_value=call_response("start_research", {"workflow": "paper-to-article", "paper_id": "1706.03762"})):
            with pytest.raises(Crash):
                controller.run("解读论文", on_event=lambda e: None)
        restored = ConversationController(AgentRuntime(tmp_path / "runtime", workflow=workflow), harness_at(tmp_path))
        events = []
        with patch("smearglepaper.harness.stream_chat", return_value={"content": "文章已恢复"}):
            assert restored.run("", on_event=events.append, resume=True) == "文章已恢复"
        assert not any(e.type == "tool.failed" for e in events)
        assert read.call_count == 1
        assert len(runtime.list_runs()) == 1
        assert restored.active_run_id == controller.active_run_id
        assert runtime.get_run(restored.active_run_id)["status"] == "completed"


def test_pending_candidate_is_not_chosen_by_resume(tmp_path):
    workflow = FakePaperWorkflow(tmp_path)
    runtime = AgentRuntime(tmp_path / "runtime", workflow=workflow)
    controller = ConversationController(runtime, harness_at(tmp_path))
    def crash_after_result(event):
        if event.type == "tool.completed":
            raise Crash()
    with patch("smearglepaper.harness.stream_chat", return_value=call_response("start_research", {"workflow": "paper-research", "topic": "agents"})):
        with pytest.raises(Crash):
            controller.run("找论文", on_event=crash_after_result)
    restored = ConversationController(runtime, harness_at(tmp_path), active_run_id=controller.active_run_id)
    with patch("smearglepaper.harness.stream_chat", return_value={"content": "请选择论文"}):
        restored.run("", on_event=lambda e: None, resume=True)
    assert runtime.get_run(controller.active_run_id)["status"] == "waiting_input"
    assert not runtime.store.load_checkpoint(controller.active_run_id, "select")


def test_revision_recovers_without_incrementing_version_or_research_again(tmp_path):
    workflow = FakePaperWorkflow(tmp_path)
    runtime = AgentRuntime(tmp_path / "runtime", workflow=workflow)
    created = runtime.run(RunRequest("paper-to-article", {"offline_example": True}), session_id="s")
    controller = ConversationController(runtime, harness_at(tmp_path), active_run_id=created.run_id)
    with patch.object(workflow, "read", wraps=workflow.read) as read:
        with patch.object(workflow, "run_writing_agent", side_effect=Crash()), patch(
            "smearglepaper.harness.stream_chat", return_value=call_response("revise_article", {"instruction": "标题克制一点"})
        ), pytest.raises(Crash):
            controller.run("标题克制一点", on_event=lambda e: None)
        restored = ConversationController(runtime, harness_at(tmp_path))
        with patch("smearglepaper.harness.stream_chat", return_value={"content": "修订完成"}):
            restored.run("", on_event=lambda e: None, resume=True)
        read.assert_not_called()
    manifest = runtime.get_run(created.run_id)
    assert restored.active_run_id == created.run_id
    assert manifest["request"]["inputs"]["revision_number"] == 2
    assert manifest["status"] == "completed"


def test_creation_before_target_binding_is_found_by_operation_id(tmp_path):
    workflow = FakePaperWorkflow(tmp_path)
    runtime = AgentRuntime(tmp_path / "runtime", workflow=workflow)
    controller = ConversationController(runtime, harness_at(tmp_path))
    def bind(run_id):
        if run_id:
            raise Crash()
    with patch("smearglepaper.conversation.bind_target", side_effect=bind), patch(
        "smearglepaper.harness.stream_chat", return_value=call_response("start_research", {"workflow": "paper-research", "topic": "agents"})
    ), pytest.raises(Crash):
        controller.run("找论文", on_event=lambda e: None)
    restored = ConversationController(runtime, harness_at(tmp_path))
    with patch("smearglepaper.harness.stream_chat", return_value={"content": "请选择"}):
        restored.run("", on_event=lambda e: None, resume=True)
    assert len(runtime.list_runs()) == 1
    assert runtime.get_run(restored.active_run_id)["status"] == "waiting_input"


def test_completed_receipt_restores_task_link_and_keeps_approval_pending(tmp_path):
    workflow = FakePaperWorkflow(tmp_path)
    runtime = AgentRuntime(tmp_path / "runtime", workflow=workflow)
    created = runtime.run(RunRequest("paper-to-article", {"offline_example": True}), session_id="s")
    controller = ConversationController(runtime, harness_at(tmp_path), active_run_id=created.run_id)
    def crash_after_result(event):
        if event.type == "tool.completed":
            raise Crash()
    with patch("smearglepaper.harness.stream_chat", return_value=call_response("request_draft")), pytest.raises(Crash):
        controller.run("创建草稿", on_event=crash_after_result)
    restored = ConversationController(runtime, harness_at(tmp_path))
    with patch("smearglepaper.harness.stream_chat", return_value={"content": "请确认审批"}):
        restored.run("", on_event=lambda e: None, resume=True)
    assert restored.active_run_id == controller.active_run_id
    assert runtime.get_run(restored.active_run_id)["status"] == "waiting_approval"
    assert workflow.publish_calls == 0


def test_crash_between_candidate_checkpoint_and_wait_state_preserves_selection(tmp_path):
    runtime = AgentRuntime(tmp_path / "runtime", workflow=FakePaperWorkflow(tmp_path))
    def crash(event):
        if event.type == "step.completed" and event.payload.get("step") == "choose":
            raise Crash()
    with pytest.raises(Crash):
        runtime.run(RunRequest("paper-research", {"topic": "agents"}), event_handler=crash)
    run_id = runtime.list_runs()[0]["run_id"]
    restored = AgentRuntime(tmp_path / "runtime", workflow=FakePaperWorkflow(tmp_path))
    assert restored.resume(run_id).status == "waiting_input"
    assert len(restored.get_run(run_id)["interaction"]["options"]) == 3
    assert not restored.store.load_checkpoint(run_id, "select")


def test_runtime_resume_and_retry_do_not_write_without_execution_lease(tmp_path):
    runtime = external_runtime(tmp_path, Mock(return_value=ToolOutcome()))
    run_id = runtime.create_run(RunRequest("test")).run_id
    manifest = runtime.get_run(run_id)
    manifest["cancel_requested"] = True
    runtime.store.save(run_id, manifest)
    before = runtime.store.run_dir(run_id).joinpath("manifest.json").read_bytes()
    with runtime.store.execution_lock(run_id):
        with pytest.raises(RuntimeError, match="already executing"):
            runtime.resume(run_id)
        with pytest.raises(RuntimeError, match="already executing"):
            runtime.retry(run_id, "publish")
    assert runtime.store.run_dir(run_id).joinpath("manifest.json").read_bytes() == before


def external_runtime(root, handler):
    registry = ToolRegistry()
    registry.register(ToolSpec("external", side_effect=True), handler)
    return AgentRuntime(root, workflow=object(), registry=registry, workflows={
        "test": WorkflowDefinition("test", "test", (WorkflowStep("publish", "external", "publisher"),))})


def test_real_process_exit_after_external_write_blocks_replay(tmp_path):
    script = """
import os
from pathlib import Path
from smearglepaper.runtime import AgentRuntime, RunRequest, ToolSpec, WorkflowDefinition, WorkflowStep
from smearglepaper.runtime.registry import ToolRegistry
root = Path(os.environ['CRASH_WORKSPACE'])
def handler(ctx):
    (root / 'remote-receipt').write_text('created-once')
    os._exit(17)
registry = ToolRegistry()
registry.register(ToolSpec('external', side_effect=True), handler)
runtime = AgentRuntime(root, workflow=object(), registry=registry, workflows={
    'test': WorkflowDefinition('test', 'test', (WorkflowStep('publish', 'external', 'publisher'),))})
runtime.run(RunRequest('test'))
"""
    env = {**os.environ, "CRASH_WORKSPACE": str(tmp_path), "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, timeout=15)
    assert result.returncode == 17, result.stderr
    execute = Mock(return_value=ToolOutcome())
    runtime = external_runtime(tmp_path, execute)
    run_id = runtime.list_runs()[0]["run_id"]
    assert runtime.resume(run_id).status == "recovery_required"
    assert runtime.retry(run_id).status == "recovery_required"
    execute.assert_not_called()
    assert (tmp_path / "remote-receipt").read_text() == "created-once"
    runtime.reconcile_external(run_id, executed=True, external_id="remote-123")
    assert runtime.resume(run_id).status == "completed"
    execute.assert_not_called()


def test_external_exception_never_retries_and_not_executed_needs_fresh_approval(tmp_path):
    registry = ToolRegistry()
    handler = Mock(side_effect=TimeoutError("unknown remote response"))
    registry.register(ToolSpec("publish", side_effect=True, approval="real", retryable=True), handler)
    runtime = AgentRuntime(tmp_path, workflow=object(), registry=registry, workflows={
        "test": WorkflowDefinition("test", "test", (WorkflowStep("publish", "publish", "publisher", approval="real", max_retries=2),))})
    created = runtime.run(RunRequest("test", dry_run=False))
    runtime.resolve_approval(created.run_id, "approve-publish", True)
    assert runtime.resume(created.run_id).status == "recovery_required"
    assert handler.call_count == 1
    runtime.reconcile_external(created.run_id, executed=False)
    assert runtime.resume(created.run_id).status == "waiting_approval"
    assert handler.call_count == 1


def test_completed_external_write_is_not_replayed_when_receipt_is_damaged(tmp_path):
    handler = Mock(return_value=ToolOutcome(output={"remote_id": "one"}))
    runtime = external_runtime(tmp_path, handler)
    run_id = runtime.run(RunRequest("test")).run_id
    with pytest.raises(RuntimeError, match="不能重放"):
        runtime.retry(run_id, "publish")
    manifest = runtime.get_run(run_id)
    manifest["status"] = "running"  # interruption before the final run commit
    runtime.store.save(run_id, manifest)
    runtime.store.run_dir(run_id).joinpath("checkpoints/publish.json").unlink()
    assert runtime.resume(run_id).status == "recovery_required"
    assert handler.call_count == 1
    runtime.reconcile_external(run_id, executed=True, external_id="one")
    assert runtime.resume(run_id).status == "completed"
    assert handler.call_count == 1


def test_rolling_summary_survives_restore_and_keeps_early_constraints(tmp_path):
    memory = ConversationMemory(tmp_path / "memory.json", token_budget=600)
    memory.append("user", "面向工程师，不要标题党，保持证据来源")
    for i in range(30):
        memory.append("assistant", f"历史结果 {i}" + "材料" * 50)
        memory.append("user", f"后续问题 {i}" + "解释" * 30)
    restored = ConversationMemory(memory.path, token_budget=600)
    assert "不要标题党" in restored.summary
    assert len(restored.turns) < 10
    assert "后续问题 29" in restored.turns[-1]["content"]
    assert restored.context("sys")[1]["role"] == "user"


def test_context_budget_preserves_tool_pairs_and_current_request():
    budget = ContextBudget(5000, 500, 256)
    messages = [{"role": "system", "content": "当前文章版本 2，面向工程师"},
                {"role": "user", "content": "旧历史" * 1000},
                {"role": "assistant", "content": "旧回答" * 1000},
                {"role": "user", "content": "本次只改标题"},
                {"role": "assistant", "tool_calls": [{"id": "c", "function": {"name": "read", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "c", "content": "result" * 10000}]
    prepared, info = budget.prepare(messages, [])
    assert estimate_tokens(prepared) + estimate_tokens([]) <= budget.input_tokens
    assert prepared[-1]["tool_call_id"] == prepared[-2]["tool_calls"][0]["id"]
    assert any(m.get("content") == "本次只改标题" for m in prepared)
    assert prepared[0] == messages[0]
    assert info["compacted_tool_results"]
    assert len(messages[-1]["content"]) == 60000


def test_oversized_request_fails_before_provider(tmp_path):
    harness = harness_at(tmp_path, budget=ContextBudget(2000, 256, 256))
    with patch("smearglepaper.harness.stream_chat") as provider, pytest.raises(ContextBudgetExceeded):
        harness.run("不能丢失" * 2000, system_prompt="sys", on_event=lambda e: None)
    provider.assert_not_called()


def test_journal_recovers_torn_tail_and_does_not_reread_on_every_append(tmp_path):
    path = tmp_path / "events.jsonl"
    journal = EventJournal(path)
    event = HarnessEvent.create("turn.started", session_id="s", turn_id="t", sequence=1, source="harness")
    journal.append(event)
    with path.open("ab") as handle:
        handle.write(b'{"type":"model.del')
    restored = EventJournal(path)
    assert len(restored.events()) == 1
    next_event = HarnessEvent.create("turn.failed", session_id="s", turn_id="t", sequence=2, source="harness")
    restored.append(next_event)
    assert len(list(tmp_path.glob("*.torn-*"))) == 1
    assert [e.sequence for e in EventJournal(path).events()] == [1, 2]
    with patch.object(Path, "read_bytes", side_effect=AssertionError("unexpected full read")):
        restored.append(next_event)  # same ID, no duplicate
        restored.events()
        restored.append(HarnessEvent.create("turn.started", session_id="s", turn_id="t2", sequence=1, source="harness"))


def test_corrupt_middle_journal_is_not_silently_skipped(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{invalid}\n{}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="中间损坏"):
        EventJournal(path).events()


def test_runtime_journal_torn_tail_does_not_block_safe_resume(tmp_path):
    runtime = AgentRuntime(tmp_path, workflow=FakePaperWorkflow(tmp_path))
    created = runtime.create_run(RunRequest("paper-research", {"topic": "agents"}))
    path = runtime.store.run_dir(created.run_id) / "events.jsonl"
    path.write_bytes(b'{"type":"run.sta')
    assert runtime.resume(created.run_id).status == "waiting_input"
    assert len(list(path.parent.glob("events.jsonl.torn-*"))) == 1
    assert all(json.loads(line) for line in path.read_text().splitlines())


def test_turn_lease_blocks_other_instances_and_processes(tmp_path):
    first = TurnStore(tmp_path / "turn.json", "s")
    second = TurnStore(first.path, "s")
    with first.lease():
        with pytest.raises(RecoveryRequired), second.lease():
            pytest.fail("lease must be exclusive")
        code = """
import sys
from pathlib import Path
from smearglepaper.turn_store import RecoveryRequired, TurnStore
try:
    with TurnStore(Path(sys.argv[1]), 's').lease():
        sys.exit(1)
except RecoveryRequired:
    sys.exit(0)
"""
        result = subprocess.run([sys.executable, "-c", code, str(first.path)], capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr
    with second.lease():
        pass


@pytest.mark.parametrize("damage", ["json", "calls", "round", "session_id"])
def test_corrupt_receipt_fails_closed(tmp_path, damage):
    harness = harness_at(tmp_path)
    with patch("smearglepaper.harness.stream_chat", side_effect=Crash()), pytest.raises(Crash):
        harness.run("hello", system_prompt="sys", on_event=lambda e: None)
    path = harness.turn_store.path
    if damage == "json":
        path.write_text("{broken", encoding="utf-8")
    else:
        saved = json.loads(path.read_text())
        saved[damage] = "invalid"
        path.write_text(json.dumps(saved), encoding="utf-8")
    with patch("smearglepaper.harness.stream_chat") as provider, pytest.raises(RecoveryRequired):
        harness_at(tmp_path).run("", resume=True, system_prompt="sys", on_event=lambda e: None)
    provider.assert_not_called()


def test_budget_applied_each_round_and_full_tool_receipt_is_retained(tmp_path):
    budget = ContextBudget(6000, 500, 256)
    harness = harness_at(tmp_path, budget=budget)
    output = "完整材料" * 10000
    requests = []
    def model(messages, **kwargs):
        requests.append(messages)
        assert estimate_tokens(messages) + estimate_tokens(kwargs.get("tools")) <= budget.input_tokens
        assert kwargs["max_tokens"] == 500
        return call_response() if len(requests) == 1 else {"content": "已读取"}
    with patch("smearglepaper.harness.stream_chat", side_effect=model):
        harness.run("read", system_prompt="sys", on_event=lambda e: None, execute_tool=lambda n, a: output)
    assert len(requests) == 2
    assert harness.turn_store.load()["calls"][0]["output"] == output
    assert len(requests[-1][-1]["content"]) < len(output)


def test_reconcile_cli_requires_human_confirmation_and_does_not_resume(tmp_path, capsys):
    from smearglepaper.cli import main
    with pytest.raises(SystemExit) as missing:
        main(["runs", "reconcile", "test", "--not-executed"])
    assert missing.value.code == 2
    runtime = external_runtime(tmp_path, Mock(side_effect=TimeoutError("uncertain")))
    created = runtime.run(RunRequest("test"))
    with patch("smearglepaper.runtime.AgentRuntime", return_value=runtime), patch.object(runtime, "resume") as resume:
        assert main(["runs", "reconcile", created.run_id, "--executed", "--external-id", "remote-1", "--confirmed"]) == 0
        resume.assert_not_called()
    assert runtime.get_run(created.run_id)["status"] == "pending"


@pytest.mark.parametrize("size", [(80, 24), (136, 51), (140, 48)])
def test_tui_session_restore_offers_resume_without_duplicate_user_message(tmp_path, size):
    from smearglepaper.tui import SmearglePaperApp
    async def exercise():
        with patch("smearglepaper.tui.DATA_DIR", tmp_path), patch("smearglepaper.tui.save_session"):
            app = SmearglePaperApp()
            async with app.run_test(size=size) as pilot:
                screen = app.screen
                session = screen.session
                session.messages.append({"role": "user", "content": "你好"})
                def partial(messages, **kwargs):
                    kwargs["on_delta"]("已收到的部分回答")
                    raise Crash()
                with patch("smearglepaper.harness.stream_chat", side_effect=partial):
                    with pytest.raises(Crash):
                        screen._conversation_harness.run("你好", system_prompt="sys", on_event=lambda e: None, delta_interval_seconds=0)
                screen._reload_session(session)
                await pilot.pause()
                assert screen.query_one("#resume-turn").display
                assert screen.query_one("#resume-turn").region.bottom <= size[1]
                assert screen.query_one("#input-bar").region.bottom <= size[1]
                assert any("上次回答未完成" in m.content and "已收到的部分回答" in m.content for m in screen._trace_messages)
                assert not any(m["role"] == "assistant" for m in session.messages)
                assert screen._harness_projection.state.status == "interrupted"
                with patch("smearglepaper.harness.stream_chat", return_value={"content": "已恢复"}) as provider:
                    screen.action_resume_turn()
                    for _ in range(100):
                        await pilot.pause(0.01)
                        if not screen._busy:
                            break
                    assert not screen._busy
                    assert provider.call_count == 1
                assert len([m for m in session.messages if m["role"] == "user"]) == 1
                assert session.messages[-1]["content"] == "已恢复"
                assert not screen.query_one("#resume-turn").display
    asyncio.run(exercise())
