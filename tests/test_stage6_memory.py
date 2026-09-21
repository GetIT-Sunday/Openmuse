from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from test_runtime_workflows import FakePaperWorkflow
from textual.widgets import Input, ListView, Select

from smearglepaper.context_budget import estimate_tokens
from smearglepaper.conversation import ConversationController
from smearglepaper.harness import ConversationHarness, ConversationMemory
from smearglepaper.harness_events import EventJournal
from smearglepaper.preference_memory import PACK_SCOPE, PreferenceMemory
from smearglepaper.preference_ui import PreferenceMemoryModal
from smearglepaper.runtime import AgentRuntime


class Crash(BaseException):
    pass


def remember(memory, kind="audience", value="面向工程师", **kwargs):
    proposed = memory.propose(kind, value, session_id="source", turn_id="turn-source", **kwargs)
    return memory.confirm(proposed["id"])


def controller_at(root, memory, session="s"):
    return ConversationController(
        AgentRuntime(root / "runtime", workflow=FakePaperWorkflow(root)),
        ConversationHarness(session, ConversationMemory(root / f"{session}.memory.json"),
                            event_journal=EventJournal(root / f"{session}.jsonl")),
        preference_memory=memory)


def test_proposal_requires_confirmation_and_preserves_provenance(tmp_path):
    memory = PreferenceMemory(tmp_path / "prefs.sqlite3")
    proposed = memory.propose("audience", "面向工程师", session_id="s1", turn_id="t1")
    assert memory.retrieve("写一篇文章") == []
    memory.confirm(proposed["id"])
    restored = PreferenceMemory(memory.path)
    rows = restored.retrieve("写一篇文章")
    assert rows[0]["value"] == "面向工程师"
    assert rows[0]["source_session"] == "s1"
    assert rows[0]["source_turn"] == "t1"
    assert memory.path.stat().st_mode & 0o777 == 0o600


def test_profile_workspace_pack_isolation_and_specific_precedence(tmp_path):
    memory = PreferenceMemory(tmp_path / "prefs.sqlite3")
    remember(memory, value="general engineers research writing article audience")
    remember(memory, value="论文初学者", scope=PACK_SCOPE)
    remember(memory, kind="tone", value="只用于其他包", scope="pack:other")
    selected = memory.retrieve("general engineers research writing article audience", scope=PACK_SCOPE)
    assert selected[0]["value"] == "论文初学者"
    assert len(selected) == 1
    assert PreferenceMemory(memory.path, profile="other").entries() == []
    assert PreferenceMemory(tmp_path / "another-workspace.sqlite3").retrieve("写文章") == []


def test_relevance_and_budget_exclude_irrelevant_old_topics(tmp_path):
    memory = PreferenceMemory(tmp_path / "prefs.sqlite3")
    remember(memory)
    remember(memory, "topics", "强化学习机器人")
    remember(memory, "tone", "克制而有证据")
    assert memory.retrieve("你好") == []
    selected = memory.retrieve("写一篇 NLP 文章", budget=700)
    assert estimate_tokens(selected) <= 700
    assert all(row["kind"] != "topics" for row in selected)
    assert memory.retrieve("聊聊机器人", budget=1800)[0]["kind"] == "topics"
    assert memory.retrieve("写文章", budget=10) == []


def test_expiry_and_late_confirmation_are_checked(tmp_path):
    now = [datetime(2026, 9, 21, tzinfo=timezone.utc)]
    memory = PreferenceMemory(tmp_path / "prefs.sqlite3", clock=lambda: now[0])
    remember(memory, days=1)
    pending = memory.propose("tone", "克制", days=1)
    now[0] += timedelta(days=2)
    assert memory.retrieve("写文章") == []
    assert all(r["expired"] for r in memory.entries())
    with pytest.raises(ValueError, match="过期"):
        memory.confirm(pending["id"])


def test_stale_proposals_do_not_override_newer_edits(tmp_path):
    memory = PreferenceMemory(tmp_path / "prefs.sqlite3")
    initial = remember(memory)
    stale = memory.propose("audience", "旧提议")
    newer = memory.propose("audience", "面向初学者", expected_version=initial["version"])
    memory.confirm(newer["id"])
    with pytest.raises(ValueError, match="同类偏好已被修改"):
        memory.confirm(stale["id"])
    with pytest.raises(ValueError, match="其他窗口更新"):
        memory.propose("audience", "过时窗口", expected_version=initial["version"])
    assert memory.retrieve("写文章")[0]["value"] == "面向初学者"


def test_forget_redacts_values_and_cannot_be_resurrected_by_replay(tmp_path):
    memory = PreferenceMemory(tmp_path / "prefs.sqlite3")
    pending = memory.propose("tone", "不要夸张标题", operation_id="operation-1")
    memory.confirm(pending["id"])
    stale = memory.propose("tone", "不要口语化", operation_id="operation-2")
    memory.forget(pending["id"])
    assert memory.entries() == []
    assert memory.retrieve("写文章") == []
    replayed = memory.propose("tone", "不要夸张标题", operation_id="operation-1")
    assert replayed["status"] == "forgotten" and replayed["value"] == ""
    with pytest.raises(ValueError):
        memory.confirm(stale["id"])
    assert "不要夸张标题".encode() not in memory.path.read_bytes()


@pytest.mark.parametrize("value", ["sk-" + "testing" * 5, "API_KEY=testing-secret", "Bearer private-example"])
def test_credentials_rejected_before_storage(tmp_path, value):
    memory = PreferenceMemory(tmp_path / "prefs.sqlite3")
    with pytest.raises(ValueError, match="凭据"):
        memory.propose("tone", value)
    assert not memory.path.exists()


def test_corrupt_store_is_preserved_and_not_replaced(tmp_path):
    path = tmp_path / "prefs.sqlite3"
    path.write_bytes(b"not a sqlite database")
    with pytest.raises(ValueError, match="原文件"):
        PreferenceMemory(path).entries()
    assert path.read_bytes() == b"not a sqlite database"


def test_model_can_propose_but_never_confirm_and_next_session_uses_preference(tmp_path):
    memory = PreferenceMemory(tmp_path / "prefs.sqlite3")
    first = controller_at(tmp_path, memory)
    events = []
    responses = [
        {"tool_calls": [{"id": "pref", "name": "propose_memory", "arguments": {"kind": "audience", "value": "面向工程师"}}]},
        {"content": "请确认这条偏好"},
    ]
    with patch("smearglepaper.harness.stream_chat", side_effect=responses):
        first.run("记住我的文章面向工程师", on_event=events.append)
    assert memory.entries()[0]["status"] == "pending"
    assert any(e.type == "memory.proposed" for e in events)
    assert not first.runtime.list_runs()
    with pytest.raises(ValueError, match="未授权"):
        first.execute_tool("confirm_memory", {"id": memory.entries()[0]["id"]})
    memory.confirm(memory.entries()[0]["id"])
    second = controller_at(tmp_path, memory, session="new-session")
    def provider(messages, **kwargs):
        prompt = messages[0]["content"]
        assert "面向工程师" in prompt
        assert "当前用户明确要求 > 当前任务约束" in prompt
        assert "source_session" in prompt
        assert all(t["function"]["name"] not in {"confirm_memory", "forget_memory"} for t in kwargs["tools"])
        return {"content": "可以，先确认材料"}
    with patch("smearglepaper.harness.stream_chat", side_effect=provider):
        second.run("写一篇文章", on_event=events.append)
    assert events[-1].type == "turn.completed"
    retrieved = [e for e in events if e.type == "memory.retrieved"][-1]
    assert retrieved.payload["count"] == 1
    assert "面向工程师" not in str(retrieved.payload)


def test_memory_does_not_override_explicit_task_arguments(tmp_path):
    memory = PreferenceMemory(tmp_path / "prefs.sqlite3")
    remember(memory)
    controller = controller_at(tmp_path, memory)
    replies = [{"tool_calls": [{"id": "start", "name": "start_research", "arguments": {
        "workflow": "paper-to-article", "topic": "agents", "target_audience": "小学生",
        "writing_brief": "本次只用浅显语言"}}]}, {"content": "请选择材料"}]
    with patch("smearglepaper.harness.stream_chat", side_effect=replies):
        controller.run("本次面向小学生写一篇文章", on_event=lambda e: None)
    inputs = controller.runtime.get_run(controller.active_run_id)["request"]["inputs"]
    assert inputs["target_audience"] == "小学生"
    assert inputs["writing_brief"] == "本次只用浅显语言"


def test_proposal_recovery_is_idempotent_and_respects_rejection(tmp_path):
    memory = PreferenceMemory(tmp_path / "prefs.sqlite3")
    first = controller_at(tmp_path, memory)
    response = {"tool_calls": [{"id": "pref", "name": "propose_memory", "arguments": {"kind": "tone", "value": "克制"}}]}
    def event(e):
        if e.type == "memory.proposed":
            raise Crash()
    with patch("smearglepaper.harness.stream_chat", return_value=response), pytest.raises(Crash):
        first.run("记住我喜欢克制表达", on_event=event)
    pending = memory.entries()[0]
    memory.reject(pending["id"])
    restored = controller_at(tmp_path, memory)
    with patch("smearglepaper.harness.stream_chat", return_value={"content": "这条偏好未保存"}):
        restored.run("", resume=True, on_event=lambda e: None)
    assert memory.entries() == []
    assert restored.harness.turn_store.load()["status"] == "completed"


def test_resume_rebuilds_preferences_after_forgetting(tmp_path):
    memory = PreferenceMemory(tmp_path / "prefs.sqlite3")
    remembered = remember(memory)
    first = controller_at(tmp_path, memory)
    with patch("smearglepaper.harness.stream_chat", side_effect=Crash()), pytest.raises(Crash):
        first.run("写一篇文章", on_event=lambda e: None)
    memory.forget(remembered["id"])
    def provider(messages, **kwargs):
        assert "面向工程师" not in messages[0]["content"]
        return {"content": "继续"}
    with patch("smearglepaper.harness.stream_chat", side_effect=provider):
        controller_at(tmp_path, memory).run("", resume=True, on_event=lambda e: None)


def test_memory_can_be_disabled_across_sessions_without_deleting_preferences(tmp_path):
    memory = PreferenceMemory(tmp_path / "prefs.sqlite3")
    remember(memory)
    memory.set_enabled(False)
    restored = PreferenceMemory(memory.path)
    assert restored.retrieve("写文章") == []
    assert len(restored.entries()) == 1
    def provider(messages, **kwargs):
        assert "面向工程师" not in messages[0]["content"]
        assert "propose_memory" not in [t["function"]["name"] for t in kwargs["tools"]]
        return {"content": "可以"}
    with patch("smearglepaper.harness.stream_chat", side_effect=provider):
        controller_at(tmp_path, restored).run("写文章", on_event=lambda e: None)
    restored.set_enabled(True)
    assert restored.retrieve("写文章")[0]["value"] == "面向工程师"


def test_disabled_memory_does_not_block_recovery_or_recreate_proposal(tmp_path):
    memory = PreferenceMemory(tmp_path / "prefs.sqlite3")
    controller = controller_at(tmp_path, memory)
    def event(e):
        if e.type == "memory.proposed":
            raise Crash()
    response = {"tool_calls": [{"id": "pref", "name": "propose_memory", "arguments": {"kind": "tone", "value": "克制"}}]}
    with patch("smearglepaper.harness.stream_chat", return_value=response), pytest.raises(Crash):
        controller.run("记住表达克制", on_event=event)
    memory.set_enabled(False)
    with patch("smearglepaper.harness.stream_chat", return_value={"content": "已暂停偏好功能"}):
        controller_at(tmp_path, memory).run("", resume=True, on_event=lambda e: None)
    assert len(memory.entries()) == 1
    assert memory.retrieve("写文章") == []


def test_corrupt_memory_modal_shows_error_without_crashing(tmp_path):
    from smearglepaper.tui import SmearglePaperApp
    async def exercise():
        with patch("smearglepaper.tui.DATA_DIR", tmp_path), patch("smearglepaper.tui.save_session"):
            app = SmearglePaperApp()
            async with app.run_test(size=(80, 24)) as pilot:
                app.screen._preference_memory.path.write_bytes(b"broken database")
                app.screen._show_memory()
                await pilot.pause()
                assert isinstance(app.screen, PreferenceMemoryModal)
                assert "原文件" in str(app.screen.query_one("#preference-status").render())
                await pilot.press("escape")
    asyncio.run(exercise())


@pytest.mark.parametrize("size", [(80, 24), (136, 51), (140, 48)])
def test_tui_confirmation_edit_forget_and_keyboard_at_supported_sizes(tmp_path, size):
    from smearglepaper.tui import SmearglePaperApp
    async def exercise():
        with patch("smearglepaper.tui.DATA_DIR", tmp_path), patch("smearglepaper.tui.save_session"):
            app = SmearglePaperApp()
            async with app.run_test(size=size) as pilot:
                console = app.screen
                store = console._preference_memory
                store.propose("tone", "不要标题党", session_id=console.session.id)
                console._refresh_memory_review()
                assert console.query_one("#memory-review").display
                await pilot.pause()
                await pilot.click("#memory-review")
                await pilot.pause()
                modal = app.screen
                assert isinstance(modal, PreferenceMemoryModal)
                listing = modal.query_one("#preference-list", ListView)
                listing.focus()
                listing.index = 0
                await pilot.press("enter")
                await pilot.pause()
                assert modal.query_one("#preference-value", Input).value == "不要标题党"
                save = modal.query_one("#preference-save")
                assert save.region.bottom <= size[1]
                save.focus()
                await pilot.press("enter")
                await pilot.pause()
                assert store.retrieve("写文章")[0]["value"] == "不要标题党"
                listing.focus()
                listing.index = 0
                await pilot.press("enter")
                await pilot.pause()
                modal.query_one("#preference-value", Input).value = "平实严谨"
                modal.query_one("#preference-days", Select).value = 90
                save.focus()
                await pilot.press("enter")
                await pilot.pause()
                assert store.retrieve("写文章")[0]["value"] == "平实严谨"
                listing.focus()
                listing.index = 0
                await pilot.press("enter")
                await pilot.pause()
                modal.query_one("#preference-forget").focus()
                await pilot.press("enter")
                await pilot.pause()
                assert store.retrieve("写文章") == []
                assert modal.query_one("#preference-panel").region.bottom <= size[1]
                await pilot.press("escape")
                await pilot.pause()
                assert app.screen is console
                assert not console.query_one("#memory-review").display
                assert console.query_one("#input-bar").region.bottom <= size[1]
                types = {e.type for e in console._event_journal.events()}
                assert {"memory.confirmed", "memory.forgotten"} <= types
    asyncio.run(exercise())
