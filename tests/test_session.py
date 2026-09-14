from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smearglepaper.session import Session, save_session, load_session, list_sessions, delete_session, auto_title


class SessionTests(unittest.TestCase):
    def test_session_create(self) -> None:
        session = Session.create("测试对话")
        self.assertEqual(session.title, "测试对话")
        self.assertEqual(len(session.id), 12)
        self.assertEqual(session.messages, [])
        self.assertTrue(session.created_at)

    def test_session_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("smearglepaper.session.SESSIONS_DIR", Path(tmp)):
                session = Session.create("测试")
                session.messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
                save_session(session)

                loaded = load_session(session.id)
                self.assertEqual(loaded.id, session.id)
                self.assertEqual(loaded.title, "测试")
                self.assertEqual(len(loaded.messages), 2)

    def test_list_sessions_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("smearglepaper.session.SESSIONS_DIR", Path(tmp)):
                s1 = Session.create("第一个")
                save_session(s1)
                s2 = Session.create("第二个")
                save_session(s2)

                sessions = list_sessions()
                self.assertEqual(len(sessions), 2)
                # Most recently updated first
                self.assertEqual(sessions[0].id, s2.id)

    def test_delete_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("smearglepaper.session.SESSIONS_DIR", Path(tmp)):
                session = Session.create("待删除")
                save_session(session)
                self.assertEqual(len(list_sessions()), 1)

                delete_session(session.id)
                self.assertEqual(len(list_sessions()), 0)

    def test_load_nonexistent_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("smearglepaper.session.SESSIONS_DIR", Path(tmp)):
                with self.assertRaises(FileNotFoundError):
                    load_session("nonexistent")

    def test_auto_title_from_first_user_message(self) -> None:
        messages = [
            {"role": "user", "content": "帮我收集 agent 方向的论文"},
            {"role": "assistant", "content": "好的"},
        ]
        self.assertEqual(auto_title(messages), "帮我收集 agent 方向的论文")

    def test_auto_title_truncates_long_message(self) -> None:
        messages = [{"role": "user", "content": "这是一条非常非常长的消息" * 10}]
        title = auto_title(messages)
        self.assertLessEqual(len(title), 33)  # 30 + "..."

    def test_auto_title_fallback(self) -> None:
        self.assertEqual(auto_title([]), "新对话")
        self.assertEqual(auto_title([{"role": "assistant", "content": "hi"}]), "新对话")

    def test_session_touch_updates_time(self) -> None:
        session = Session.create("测试")
        old_time = session.updated_at
        session.touch()
        self.assertGreaterEqual(session.updated_at, old_time)


class TUIAppTests(unittest.TestCase):
    def test_app_creates(self) -> None:
        from smearglepaper.tui import SmearglePaperApp
        app = SmearglePaperApp()
        self.assertEqual(app.TITLE, "AutoWechat")

    def test_console_creates(self) -> None:
        from smearglepaper.tui import AgentConsole
        screen = AgentConsole()
        self.assertEqual(screen.session.title, "新对话")
        self.assertFalse(screen._busy)
        self.assertEqual(screen._last_agent_reply, "")

    def test_copy_to_clipboard_function(self) -> None:
        from smearglepaper.tui import copy_to_clipboard
        # Just verify it doesn't crash (may fail on headless systems)
        result = copy_to_clipboard("test")
        self.assertIsInstance(result, bool)


class RunStateTests(unittest.TestCase):
    def test_run_state_defaults(self) -> None:
        from smearglepaper.run_state import RunState, RunMode, RunStatus
        rs = RunState()
        self.assertEqual(rs.mode, RunMode.IDLE)
        self.assertEqual(rs.status, RunStatus.IDLE)
        self.assertEqual(len(rs.id), 12)

    def test_run_state_start_finish(self) -> None:
        from smearglepaper.run_state import RunState, RunMode, RunStatus
        rs = RunState()
        rs.start("test task")
        self.assertEqual(rs.mode, RunMode.RUN)
        self.assertEqual(rs.status, RunStatus.RUNNING)
        rs.finish(success=True)
        self.assertEqual(rs.status, RunStatus.SUCCESS)

    def test_run_state_set_step(self) -> None:
        from smearglepaper.run_state import RunState, StepStatus, WorkflowStep
        rs = RunState()
        rs.steps = [WorkflowStep("test", "Test Step")]
        rs.set_step("test", StepStatus.RUNNING)
        self.assertEqual(rs.steps[0].status, StepStatus.RUNNING)

    def test_workflow_step_icons(self) -> None:
        from smearglepaper.run_state import StepStatus, WorkflowStep
        for status, icon in [
            (StepStatus.WAITING, "·"),
            (StepStatus.RUNNING, "⟳"),
            (StepStatus.SUCCESS, "✓"),
            (StepStatus.FAILED, "✗"),
        ]:
            step = WorkflowStep("t", "T", status=status)
            self.assertEqual(step.icon, icon)

    def test_progress_bar(self) -> None:
        from smearglepaper.run_state import ProgressInfo
        p = ProgressInfo(current=3, total=10)
        self.assertEqual(p.percent, 30)
        self.assertIn("30%", p.bar)

    def test_default_workflow_steps(self) -> None:
        from smearglepaper.run_state import default_workflow_steps
        steps = default_workflow_steps()
        self.assertEqual(len(steps), 6)
        self.assertEqual(steps[0].name, "collect-papers")

    def test_scan_artifacts(self) -> None:
        from smearglepaper.run_state import scan_artifacts
        artifacts = scan_artifacts()
        self.assertIsInstance(artifacts, list)

    def test_load_model_info(self) -> None:
        from smearglepaper.run_state import load_model_info
        info = load_model_info()
        self.assertIsInstance(info.provider, str)
        self.assertIsInstance(info.name, str)


if __name__ == "__main__":
    unittest.main()
