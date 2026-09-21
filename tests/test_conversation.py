from __future__ import annotations

import asyncio
from unittest.mock import Mock, patch

import pytest
from test_runtime_workflows import FakePaperWorkflow

from smearglepaper.conversation import ConversationController
from smearglepaper.harness import ConversationHarness
from smearglepaper.harness_events import EventJournal
from smearglepaper.harness_projection import HarnessProjection
from smearglepaper.runtime import AgentRuntime, RunRequest


@pytest.fixture
def controller(tmp_path):
    workflow = FakePaperWorkflow(tmp_path)
    workflow.collector = Mock(fetch_by_id=Mock(return_value=workflow._resolve_paper()))
    runtime = AgentRuntime(tmp_path / "runtime", workflow=workflow)
    harness = ConversationHarness("session", event_journal=EventJournal(tmp_path / "events.jsonl"))
    runtime.attach_event_journal("session", harness.event_journal)
    return ConversationController(runtime, harness, preview=lambda: True)


def run_turn(controller, text, name=None, arguments=None, *, failed_tool=False):
    """Script only the provider decision; execute real Harness + Runtime."""
    events = []
    prompts = []
    def provider(messages, **kwargs):
        prompts.append(messages[0]["content"])
        if name and len(prompts) == 1:
            return {"content": "", "tool_calls": [{"id": "call-1", "name": name, "arguments": arguments or {}}]}
        kwargs["on_delta"]("已根据当前材料处理。")
        return {"content": "已根据当前材料处理。", "tool_calls": []}
    with patch("smearglepaper.harness.stream_chat", side_effect=provider):
        controller.run(text, on_event=events.append)
    assert events[-1].type == "turn.completed"
    assert any(e.type == "tool.failed" for e in events) == failed_tool
    return events, prompts


def make_article(controller):
    run_turn(controller, "解读论文", "start_research", {
        "workflow": "paper-to-article", "paper_id": "1706.03762"})


def test_core_journey_uses_one_controller_and_reuses_research(controller):
    workflow = controller.runtime.workflow
    with patch.object(workflow, "collect", wraps=workflow.collect) as collect, \
         patch.object(workflow, "rank", wraps=workflow.rank) as rank, \
         patch.object(workflow, "read", wraps=workflow.read) as ingest:
        events, _ = run_turn(controller, "找三篇 agents 论文", "start_research", {
            "workflow": "paper-research", "topic": "agents"})
        run_id = controller.active_run_id
        assert controller.runtime.get_run(run_id)["status"] == "waiting_input"
        assert events[-1].payload["task_status"] == "waiting_input"
        assert not workflow.writing_calls and not ingest.called

        # A question containing a candidate number is not a selection.
        _, prompts = run_turn(controller, "第二篇与第一篇有什么区别？")
        assert "Efficient Agents" in prompts[0]
        assert controller.runtime.get_run(run_id)["status"] == "waiting_input"

        run_turn(controller, "选第二篇", "select_candidate", {"paper_id": "2607.10002"})
        assert controller.runtime.get_run(run_id)["status"] == "completed"
        assert not workflow.writing_calls
        run_turn(controller, "把它写成中文解读", "write_article", {
            "target_audience": "工程师", "style_mode": "rigorous", "writing_brief": "2000 字，重点解释方法"})
        assert controller.active_run_id == run_id
        assert controller.runtime.get_run(run_id)["workflow"] == "paper-to-article"
        assert len(workflow.writing_calls) == 1
        assert workflow.writing_calls[-1]["target_audience"] == "工程师"
        assert workflow.writing_calls[-1]["writing_brief"] == "2000 字，重点解释方法"
        assert collect.call_count == rank.call_count == ingest.call_count == 1

        before = controller.runtime.get_run(run_id)
        _, prompts = run_turn(controller, "这篇文章有什么不足？", "read_article")
        assert '"article_available": true' in prompts[0]
        assert "把它写成中文解读" in [t["content"] for t in controller.harness.memory.turns]
        assert controller.runtime.get_run(run_id) == before
        assert len(workflow.writing_calls) == 1

        controller.model = "new-model"
        run_turn(controller, "只改标题，克制一点", "revise_article", {"instruction": "只改标题，克制一点"})
        after = controller.runtime.get_run(run_id)
        assert after["request"]["inputs"]["revision_number"] == 2
        assert after["request"]["model"] == "new-model"
        assert len(workflow.writing_calls) == 2
        assert workflow.writing_calls[-1]["revision_instruction"] == "只改标题，克制一点"
        assert workflow.writing_calls[-1]["article_path"].is_file()
        assert collect.call_count == rank.call_count == ingest.call_count == 1
        assert any(a["status"] == "stale" for a in after["artifacts"] if a["producer"] == "write")

        controller.preview = Mock(return_value=True)
        run_turn(controller, "打开手机预览", "preview_article")
        controller.preview.assert_called_once()
        assert workflow.publish_calls == 0


def test_direct_link_skips_discovery(controller):
    with patch.object(controller.runtime.workflow, "collect") as collect:
        run_turn(controller, "解读链接", "start_research", {
            "workflow": "paper-to-article", "paper_url": "https://arxiv.org/abs/1706.03762"})
    collect.assert_not_called()
    assert controller.runtime.get_run(controller.active_run_id)["status"] == "completed"


def test_new_candidates_cannot_be_autoselected_in_same_turn(controller):
    results = [
        {"tool_calls": [{"id": "1", "name": "start_research", "arguments": {"workflow": "paper-research", "topic": "agents"}}]},
        {"tool_calls": [{"id": "2", "name": "select_candidate", "arguments": {"paper_id": "2607.10001"}}]},
        {"content": "请选择一篇"},
    ]
    events = []
    with patch("smearglepaper.harness.stream_chat", side_effect=results):
        controller.run("找几篇论文", on_event=events.append)
    assert any(e.type == "tool.failed" for e in events)
    assert controller.runtime.get_run(controller.active_run_id)["status"] == "waiting_input"
    assert not controller.runtime.store.load_checkpoint(controller.active_run_id, "select")


def test_draft_requires_real_runtime_approval(controller):
    make_article(controller)
    events, _ = run_turn(controller, "创建微信草稿", "request_draft")
    manifest = controller.runtime.get_run(controller.active_run_id)
    assert manifest["status"] == "waiting_approval"
    assert manifest["request"]["dry_run"] is False
    assert controller.runtime.workflow.publish_calls == 0
    projection = HarnessProjection()
    projection.replay(controller.harness.event_journal.events())
    assert projection.state.status == "waiting_approval"
    assert events[-1].payload["pending_request"]["status"] == "pending"
    with pytest.raises(ValueError):
        controller.execute_tool("resolve_approval", {"approve": True})
    assert controller.task_context()["article_available"]
    controller.resolve_approval("approve-publish", False)
    assert controller.runtime.workflow.publish_calls == 0
    run_turn(controller, "不发布了，修改标题", "revise_article", {"instruction": "修改标题"})
    assert controller.task_context()["workflow"] == "paper-to-article"


@pytest.mark.parametrize("name,args", [
    ("read_article", {"path": "/etc/passwd"}),
    ("start_research", {"workflow": "paper-to-wechat", "topic": "agents"}),
    ("start_research", {"workflow": "paper-research", "paper_url": "http://localhost/secret"}),
    ("start_research", {"workflow": "paper-research", "topic": "agents", "days": -1}),
    ("select_candidate", {"paper_id": "x", "run_id": "other-session"}),
    ("execute_tool", {}),
])
def test_model_tools_cannot_escape_bounded_parameters(controller, name, args):
    with pytest.raises(ValueError):
        controller.execute_tool(name, args)
    assert not controller.runtime.list_runs()


def test_failed_revision_keeps_previous_article(controller):
    make_article(controller)
    original = controller.task_context()["article_excerpt"]
    with patch.object(controller.runtime.workflow, "run_writing_agent", side_effect=RuntimeError("writing failed")):
        run_turn(controller, "重写标题", "revise_article", {"instruction": "重写标题"}, failed_tool=True)
    context = controller.task_context()
    assert context["status"] == "failed"
    assert context["previous_version_preserved"]
    assert context["article_excerpt"] == original


def test_context_after_session_restore_contains_current_article(controller):
    make_article(controller)
    restored = ConversationController(controller.runtime, ConversationHarness("session"), active_run_id=controller.active_run_id)
    assert restored.task_context()["article_available"]
    assert "Article" in restored.task_context()["article_excerpt"]
    foreign = ConversationController(controller.runtime, ConversationHarness("other"), active_run_id=controller.active_run_id)
    with pytest.raises(ValueError):
        foreign.task_context()


def test_cancel_controller_reaches_nested_runtime_without_writing(controller):
    from smearglepaper.cancellation import current_token

    def cancel_read(paper):
        controller.harness.cancel()
        current_token().check()

    events = []
    with patch.object(controller.runtime.workflow, "read", side_effect=cancel_read), \
         patch("smearglepaper.harness.stream_chat", return_value={"tool_calls": [{
             "id": "start", "name": "start_research", "arguments": {
                 "workflow": "paper-to-article", "paper_id": "1706.03762"}}]}) as provider:
        reply = controller.run("解读论文", on_event=events.append)
    assert reply == ""
    assert provider.call_count == 1
    assert events[-1].type == "turn.cancelled"
    assert controller.runtime.get_run(controller.active_run_id)["status"] == "cancelled"
    assert not controller.runtime.workflow.writing_calls
    assert not any(t["role"] == "assistant" for t in controller.harness.memory.turns)


def test_provider_failure_does_not_start_offline_or_keyword_workflow(controller):
    with patch("smearglepaper.harness.stream_chat", side_effect=RuntimeError("provider failed")), \
         patch.object(controller.runtime, "create_run") as create:
        with pytest.raises(RuntimeError, match="provider failed"):
            controller.run("帮我解读论文", on_event=lambda event: None)
        create.assert_not_called()


def test_revision_of_legacy_real_run_does_not_reuse_publication_approval(controller):
    result = controller.runtime.run(RunRequest("paper-to-wechat", {
        "paper_url": "https://arxiv.org/abs/1706.03762"}, dry_run=False), session_id="session")
    controller.active_run_id = result.run_id
    controller.resolve_approval("approve-publish", True)
    workflow = controller.runtime.workflow
    with patch.object(workflow, "publish_existing_article", wraps=workflow.publish_existing_article) as publish:
        run_turn(controller, "修改标题", "revise_article", {"instruction": "修改标题"})
    assert controller.runtime.get_run(result.run_id)["request"]["dry_run"] is True
    assert all(call.kwargs.get("real_wechat") is False for call in publish.call_args_list)


def test_tui_all_natural_language_uses_same_entry_and_preserves_busy_input(tmp_path):
    from smearglepaper.tui import SmearglePaperApp

    async def exercise():
        with patch("smearglepaper.tui.DATA_DIR", tmp_path), patch("smearglepaper.tui.save_session"), \
             patch("smearglepaper.tui.runtime_settings", return_value={"llm": {"provider": "openai"}}):
            app = SmearglePaperApp()
            async with app.run_test(size=(80, 24)) as pilot:
                screen = app.screen
                for text in ("你好", "找论文", "https://arxiv.org/abs/1706.03762", "文章有什么不足？", "选第二篇", "标题克制一点"):
                    screen._busy = False
                    with patch.object(screen, "_run_chat_turn") as run:
                        screen._handle_user_input(text)
                        run.assert_called_once_with(text)
                with patch.object(screen.runtime, "update_inputs") as update:
                    screen._handle_user_input("面向工程师")
                    update.assert_not_called()
                    assert screen.query_one("#chat-input").value == "面向工程师"
                await pilot.pause()
    asyncio.run(exercise())


def test_tui_no_provider_does_not_silently_start_workflow(tmp_path):
    from smearglepaper.tui import SmearglePaperApp

    async def exercise():
        with patch("smearglepaper.tui.DATA_DIR", tmp_path), \
             patch("smearglepaper.tui.runtime_settings", return_value={"llm": {"provider": "none"}}):
            app = SmearglePaperApp()
            async with app.run_test() as pilot:
                screen = app.screen
                with patch.object(screen, "_run_chat_turn") as run, patch.object(screen.runtime, "create_run") as create:
                    screen._handle_user_input("写篇论文解读")
                    run.assert_not_called()
                    create.assert_not_called()
                    assert screen.query_one("#chat-input").value == "写篇论文解读"
                    assert any("/connect" in m.content for m in screen._trace_messages)
                await pilot.pause()
    asyncio.run(exercise())


@pytest.mark.parametrize("size", [(80, 24), (136, 51), (140, 48)])
def test_tui_controller_journey_and_approval_stays_responsive(tmp_path, size):
    from smearglepaper.tui import ApprovalModal, SmearglePaperApp

    decisions = {
        "找论文": ("start_research", {"workflow": "paper-research", "topic": "agents"}),
        "选第二篇": ("select_candidate", {"paper_id": "2607.10002"}),
        "写文章": ("write_article", {}),
        "哪里不足？": ("read_article", {}),
        "只改标题": ("revise_article", {"instruction": "只改标题"}),
        "手机预览": ("preview_article", {}),
        "创建微信草稿": ("request_draft", {}),
    }
    called = set()
    def provider(messages, **kwargs):
        text = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        if text not in called:
            called.add(text)
            name, arguments = decisions[text]
            return {"tool_calls": [{"id": text, "name": name, "arguments": arguments}]}
        kwargs["on_delta"]("处理结果已准备好。")
        return {"content": "处理结果已准备好。"}

    async def exercise():
        with patch("smearglepaper.tui.DATA_DIR", tmp_path), patch("smearglepaper.tui.save_session"), \
             patch("smearglepaper.tui.runtime_settings", return_value={"llm": {"provider": "openai"}}), \
             patch("smearglepaper.harness.stream_chat", side_effect=provider):
            app = SmearglePaperApp()
            async with app.run_test(size=size) as pilot:
                screen = app.screen
                workflow = FakePaperWorkflow(tmp_path)
                screen.runtime = AgentRuntime(tmp_path / "runtime", workflow=workflow)
                screen.runtime.attach_event_journal(screen.session.id, screen._event_journal)
                with patch.object(screen, "action_open_preview", return_value=True) as preview:
                    for text in decisions:
                        screen._handle_user_input(text)
                        for _ in range(150):
                            await pilot.pause(0.01)
                            if not screen._busy:
                                break
                        assert not screen._busy
                        assert screen._last_runtime_error == ""
                        if text == "找论文":
                            assert screen._harness_projection.state.status == "waiting_input"
                        if text == "哪里不足？":
                            assert len(workflow.writing_calls) == 1
                        if text != "创建微信草稿":
                            composer = screen.query_one("#chat-input").region
                            assert composer.bottom <= size[1]
                    preview.assert_called_once()
                assert len(workflow.writing_calls) == 2
                assert workflow.publish_calls == 0
                assert isinstance(app.screen, ApprovalModal)
                assert screen._harness_projection.state.status == "waiting_approval"
                # Rejecting must not replace/cancel the still-running chat worker.
                app.screen.dismiss(False)
                for _ in range(150):
                    await pilot.pause(0.01)
                    if not screen._busy:
                        break
                assert not screen._busy
                assert workflow.publish_calls == 0
                assert screen.runtime.get_run(screen._active_run_id)["status"] == "cancelled"
                assert screen._harness_projection.state.status == "cancelled"
                assert all("/Users/" not in m.content for m in screen._trace_messages if not m.detail_only)
    asyncio.run(exercise())
