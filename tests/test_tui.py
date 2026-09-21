from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.widgets import Input, ListView, RichLog, Static

from smearglepaper.run_state import ArtifactInfo, RunState, RunStatus, StepStatus, WorkflowStep
from smearglepaper.tools import SYSTEM_PROMPT, TOOLS, execute_tool
from smearglepaper.tui import AgentConsole, ApprovalModal, ModelConnectModal, ModelPickerModal, SessionPickerModal, SmearglePaperApp
from smearglepaper.tui_components import AUTOWECHAT_LOGO, AutoWechatStage
from smearglepaper.tui_presentation import build_run_presentation


class ToolDefinitionTests(unittest.TestCase):
    def test_tools_is_nonempty_list(self) -> None:
        self.assertGreater(len(TOOLS), 0)

    def test_each_tool_has_required_fields(self) -> None:
        for tool in TOOLS:
            self.assertEqual(tool["type"], "function")
            func = tool["function"]
            self.assertIn("name", func)
            self.assertIn("description", func)
            self.assertIn("parameters", func)
            self.assertIn("type", func["parameters"])

    def test_tool_names_are_unique(self) -> None:
        names = [t["function"]["name"] for t in TOOLS]
        self.assertEqual(len(names), len(set(names)))

    def test_system_prompt_mentions_smearglepaper(self) -> None:
        self.assertIn("SmearglePaper", SYSTEM_PROMPT)

    def test_tools_include_core_capabilities(self) -> None:
        names = {t["function"]["name"] for t in TOOLS}
        expected = {"collect_papers", "rank_papers", "collect_blogs", "collect_github",
                    "ingest_paper", "generate_article", "run_writing_agent", "review_article",
                    "create_wechat_draft", "daily_digest", "trend_analysis",
                    "check_status", "agent_run"}
        self.assertTrue(expected.issubset(names), f"Missing tools: {expected - names}")


class ExecuteToolTests(unittest.TestCase):
    def test_unknown_tool_returns_error(self) -> None:
        result = json.loads(execute_tool("nonexistent_tool", {}))
        self.assertIn("error", result)

    @patch("smearglepaper.tools._get_workflow")
    def test_collect_papers_calls_workflow(self, mock_wf) -> None:
        mock_workflow = mock_wf.return_value
        mock_workflow.collect.return_value = [
            {"paper_id": "2401.00001", "title": "Test Paper", "published_at": "2026-01-01"}
        ]
        result = json.loads(execute_tool("collect_papers", {"topic": "agents", "days": 7}))
        self.assertEqual(result["count"], 1)
        mock_workflow.collect.assert_called_once()

    @patch("smearglepaper.tools._get_workflow")
    def test_check_status_calls_workflow(self, mock_wf) -> None:
        with patch("smearglepaper.agents.check_agents") as mock_check:
            mock_check.return_value = {"ok": True, "agents": {}}
            result = json.loads(execute_tool("check_status", {}))
            self.assertTrue(result["ok"])

    @patch("smearglepaper.tools._get_workflow")
    def test_run_writing_agent_calls_workflow(self, mock_wf) -> None:
        mock_workflow = mock_wf.return_value
        mock_workflow.run_writing_agent.return_value = {
            "status": "success",
            "run_dir": "data/agent_runs/writing/test",
            "manifest": "data/agent_runs/writing/test/manifest.json",
            "final": {"score": 90},
        }
        result = json.loads(execute_tool("run_writing_agent", {"paper_id": "2401.00001"}))
        self.assertEqual(result["status"], "success")
        mock_workflow.run_writing_agent.assert_called_once()


class AgenticLoopTests(unittest.TestCase):
    def test_agentic_loop_basic(self) -> None:
        """Test agentic loop with a simple text reply (no tool calls)."""
        from smearglepaper.llm import agentic_loop

        fake_response = {
            "content": "Hello! How can I help?",
            "tool_calls": [],
            "finish_reason": "stop",
        }

        def fake_execute(name, args):
            return "{}"

        with patch("smearglepaper.llm.chat_with_tools", return_value=fake_response):
            messages, reply = agentic_loop(
                user_message="hi",
                tools=TOOLS,
                system_prompt="You are a test agent.",
                execute_fn=fake_execute,
            )
            self.assertEqual(reply, "Hello! How can I help?")
            # Should have: system, user, assistant
            self.assertGreaterEqual(len(messages), 3)

    def test_agentic_loop_with_tool_call(self) -> None:
        """Test agentic loop processes one tool call then gives text reply."""
        from smearglepaper.llm import agentic_loop

        tool_response = {
            "content": None,
            "tool_calls": [{"id": "call_1", "name": "check_status", "arguments": {}}],
            "finish_reason": "tool_calls",
        }
        text_response = {
            "content": "System status looks good!",
            "tool_calls": [],
            "finish_reason": "stop",
        }

        call_count = 0

        def mock_chat(messages, tools, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return tool_response
            return text_response

        executed_tools = []

        def fake_execute(name, args):
            executed_tools.append(name)
            return '{"ok": true}'

        with patch("smearglepaper.llm.chat_with_tools", side_effect=mock_chat):
            messages, reply = agentic_loop(
                user_message="check status",
                tools=TOOLS,
                system_prompt="You are a test agent.",
                execute_fn=fake_execute,
            )
            self.assertEqual(reply, "System status looks good!")
            self.assertEqual(executed_tools, ["check_status"])
            self.assertEqual(call_count, 2)

    def test_agentic_loop_max_rounds(self) -> None:
        """Test agentic loop stops after max_rounds."""
        from smearglepaper.llm import agentic_loop

        infinite_tool_response = {
            "content": None,
            "tool_calls": [{"id": "call_1", "name": "check_status", "arguments": {}}],
            "finish_reason": "tool_calls",
        }

        def mock_chat(messages, tools, **kwargs):
            return infinite_tool_response

        def fake_execute(name, args):
            return '{"ok": true}'

        with patch("smearglepaper.llm.chat_with_tools", side_effect=mock_chat):
            messages, reply = agentic_loop(
                user_message="loop forever",
                tools=TOOLS,
                system_prompt="test",
                execute_fn=fake_execute,
                max_rounds=3,
            )
            # Should have stopped after 3 rounds, no final text reply
            self.assertEqual(reply, "")


class TUIAppTests(unittest.TestCase):
    def test_app_creates(self) -> None:
        app = SmearglePaperApp()
        self.assertEqual(app.TITLE, "OpenMuse")

    def test_console_creates(self) -> None:
        screen = AgentConsole()
        self.assertEqual(screen.session.messages, [])
        self.assertFalse(screen._busy)
        self.assertTrue(screen._context_visible)

    def test_explicit_offline_parser_preserves_topic_and_paper(self) -> None:
        from smearglepaper.conversation import offline_request

        topic_workflow, topic_inputs = offline_request("多智能体协作")
        paper_workflow, paper_inputs = offline_request("解读 https://arxiv.org/abs/2607.10001")
        self.assertEqual(topic_workflow, "paper-to-article")
        self.assertEqual(topic_inputs["candidate_count"], 3)
        self.assertEqual(topic_inputs["ranking_profile"], "balanced")
        self.assertEqual(paper_workflow, "paper-to-article")
        self.assertEqual(paper_inputs["paper_url"], "https://arxiv.org/abs/2607.10001")

    def test_initial_screen_is_autowechat_stage_and_composer(self) -> None:
        async def exercise() -> None:
            app = SmearglePaperApp()
            async with app.run_test(size=(120, 42)) as pilot:
                screen = app.screen
                stage = screen.query_one("#effect-stage", AutoWechatStage)
                self.assertEqual(str(stage.query_one("#stage-brand", Static).render()), AUTOWECHAT_LOGO)
                self.assertIsNotNone(screen.query_one("#chat-input", Input))
                self.assertIsNotNone(screen.query_one("#quick-paper"))
                self.assertIsNotNone(screen.query_one("#quick-topic"))
                self.assertIsNotNone(screen.query_one("#quick-continue"))
                self.assertTrue(screen.query_one("#chat-input", Input).has_focus)
                self.assertFalse(screen.query_one("#trace-area", RichLog).display)
                self.assertFalse(screen.query_one("#slash-command-popup").display)
                await pilot.pause()

        import asyncio

        asyncio.run(exercise())

    def test_composer_shows_mode_model_and_reasoning_strength(self) -> None:
        async def exercise() -> None:
            app = SmearglePaperApp()
            async with app.run_test(size=(120, 42)) as pilot:
                meta = app.screen.query_one("#composer-meta", Static)
                rendered = str(meta.render())
                self.assertIn("模式：安全预览", rendered)
                self.assertIn("模型：", rendered)
                self.assertIn("推理强度：默认", rendered)
                app.screen._handle_slash_command("/reasoning high")
                await pilot.pause()
                self.assertIn("推理强度：深度", str(meta.render()))

        import asyncio

        asyncio.run(exercise())

    def test_unconfigured_home_has_one_clear_model_setup_action(self) -> None:
        async def exercise() -> None:
            app = SmearglePaperApp()
            with patch.dict("os.environ", {"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": ""}, clear=False):
                async with app.run_test(size=(98, 41)) as pilot:
                    stage = app.screen.query_one("#effect-stage", AutoWechatStage)
                    self.assertTrue(stage.query_one("#stage-quick-actions").display)
                    self.assertTrue(stage.query_one("#quick-connect").display)
                    self.assertFalse(stage.query_one("#quick-paper").display)
                    await pilot.pause()

        import asyncio

        asyncio.run(exercise())

    def test_slash_popup_only_appears_after_typing_slash(self) -> None:
        async def exercise() -> None:
            app = SmearglePaperApp()
            async with app.run_test(size=(120, 42)) as pilot:
                screen = app.screen
                popup = screen.query_one("#slash-command-popup")
                self.assertFalse(popup.display)
                await pilot.press("/")
                await pilot.pause()
                self.assertTrue(popup.display)
                await pilot.press("escape")
                await pilot.pause()
                self.assertFalse(popup.display)
                self.assertEqual(screen.query_one("#chat-input", Input).value, "")

        import asyncio

        asyncio.run(exercise())

    def test_exit_message_keeps_autowechat_brand_and_save_status(self) -> None:
        message = AgentConsole._exit_message()
        self.assertIn(AUTOWECHAT_LOGO, message)
        self.assertIn("已自动保存", message)

    def test_connect_command_opens_model_configuration_modal(self) -> None:
        async def exercise() -> None:
            app = SmearglePaperApp()
            with patch(
                "smearglepaper.config.openai_connection_settings",
                return_value={"base_url": "https://example.com/v1", "model": "demo", "api_key_configured": False},
            ):
                async with app.run_test(size=(120, 42)) as pilot:
                    app.screen._handle_slash_command("/connect")
                    await pilot.pause()
                    self.assertIsInstance(app.screen, ModelConnectModal)
                    self.assertEqual(app.screen.query_one("#connect-base-url", Input).value, "https://example.com/v1")
                    self.assertEqual(app.screen.query_one("#connect-model", Input).value, "demo")

        import asyncio

        asyncio.run(exercise())

    def test_connect_uses_provider_first_flow_and_escape_returns(self) -> None:
        async def exercise() -> None:
            app = SmearglePaperApp()
            with patch(
                "smearglepaper.config.openai_connection_settings",
                return_value={"base_url": "https://example.com/v1", "model": "demo", "api_key_configured": False},
            ):
                async with app.run_test(size=(103, 44)) as pilot:
                    app.screen._handle_slash_command("/connect")
                    await pilot.pause()
                    self.assertTrue(app.screen.query_one("#connect-providers").display)
                    self.assertFalse(app.screen.query_one("#connect-config").display)
                    await pilot.press("down", "enter")
                    await pilot.pause()
                    self.assertFalse(app.screen.query_one("#connect-providers").display)
                    self.assertTrue(app.screen.query_one("#connect-config").display)
                    self.assertEqual(
                        app.screen.query_one("#connect-base-url", Input).value,
                        "https://opencode.ai/zen/go/v1",
                    )
                    self.assertEqual(app.screen.query_one("#connect-model", Input).value, "mimo-v2.5-pro")
                    await pilot.press("escape")
                    await pilot.pause()
                    self.assertTrue(app.screen.query_one("#connect-providers").display)
                    self.assertFalse(app.screen.query_one("#connect-config").display)

        import asyncio

        asyncio.run(exercise())

    def test_zen_free_model_explains_external_api_restriction(self) -> None:
        async def exercise() -> None:
            app = SmearglePaperApp()
            with patch(
                "smearglepaper.config.openai_connection_settings",
                return_value={"base_url": "https://opencode.ai/zen/v1", "model": "mimo-v2.5-free", "api_key_configured": True},
            ), patch("smearglepaper.config.save_openai_connection") as save:
                async with app.run_test(size=(103, 44)) as pilot:
                    app.screen._handle_slash_command("/connect")
                    await pilot.pause()
                    await pilot.press("enter")
                    await pilot.pause()
                    await pilot.click("#connect-test")
                    await pilot.pause()
                    status = str(app.screen.query_one("#connect-status", Static).render())
                    self.assertIn("只能在 OpenCode 内使用", status)
                    save.assert_not_called()

        import asyncio

        asyncio.run(exercise())

    def test_model_command_opens_picker_and_session_command_opens_manager(self) -> None:
        async def exercise() -> None:
            app = SmearglePaperApp()
            async with app.run_test(size=(120, 42)) as pilot:
                app.screen._handle_slash_command("/model")
                await pilot.pause()
                self.assertIsInstance(app.screen, ModelPickerModal)
                app.screen.dismiss(None)
                await pilot.pause()
                app.screen._handle_slash_command("/session")
                await pilot.pause()
                self.assertIsInstance(app.screen, SessionPickerModal)

        import asyncio

        asyncio.run(exercise())

    def test_stream_delta_uses_delayed_render_timer(self) -> None:
        async def exercise() -> None:
            app = SmearglePaperApp()
            async with app.run_test(size=(120, 42)) as pilot:
                screen = app.screen
                screen._hide_welcome()
                screen._begin_streaming_agent()
                screen._append_stream_delta("正在生成")
                self.assertTrue(screen._stream_render_pending)
                for _ in range(10):
                    await pilot.pause(0.05)
                    if not screen._stream_render_pending:
                        break
                self.assertFalse(screen._stream_render_pending)
                self.assertEqual(screen._streaming_message.content, "正在生成")

        import asyncio

        asyncio.run(exercise())

    def test_memory_commands_are_user_visible_and_forget_is_scoped(self) -> None:
        async def exercise() -> None:
            app = SmearglePaperApp()
            async with app.run_test(size=(120, 42)):
                screen = app.screen
                screen._conversation_harness.memory.append("user", "测试记忆")
                screen._handle_slash_command("/memory")
                self.assertTrue(any("测试记忆" in message.content for message in screen._trace_messages))
                screen._handle_slash_command("/forget")
                self.assertEqual(screen._conversation_harness.memory.turns, [])

        import asyncio

        asyncio.run(exercise())

    def test_diagnose_command_never_exposes_api_key(self) -> None:
        async def exercise() -> None:
            app = SmearglePaperApp()
            with patch.dict(
                "os.environ",
                {
                    "OPENAI_BASE_URL": "https://opencode.ai/zen/go/v1",
                    "OPENAI_MODEL": "mimo-v2.5-pro",
                    "OPENAI_API_KEY": "secret-test-key",
                },
                clear=False,
            ):
                async with app.run_test(size=(98, 41)) as pilot:
                    app.screen._show_diagnostics()
                    rendered = "\n".join(message.content for message in app.screen._trace_messages)
                    self.assertIn("已配置", rendered)
                    self.assertNotIn("secret-test-key", rendered)
                    self.assertIn("x-opencode-session", rendered)
                    await pilot.pause()

        import asyncio

        asyncio.run(exercise())

    def test_effect_stage_renders_running_and_completed_artifacts(self) -> None:
        async def exercise() -> None:
            app = SmearglePaperApp()
            async with app.run_test(size=(120, 42)):
                stage = app.screen.query_one("#effect-stage", AutoWechatStage)
                state = RunState(
                    title="paper-to-wechat",
                    status=RunStatus.RUNNING,
                    current_task="generate_article",
                    steps=[
                        WorkflowStep("research", "research", StepStatus.SUCCESS),
                        WorkflowStep("write", "generate_article", StepStatus.RUNNING),
                    ],
                )
                stage.update_running(state)
                self.assertIn("正在处理", str(stage.query_one("#stage-status", Static).render()))
                self.assertIn("正在处理研究材料", str(stage.query_one("#stage-current", Static).render()))

                state.status = RunStatus.SUCCESS
                state.steps[1].status = StepStatus.SUCCESS
                state.artifacts = [ArtifactInfo("article.html", "/tmp/article.html")]
                stage.update_completed(state)
                self.assertIn("已完成", str(stage.query_one("#stage-status", Static).render()))
                self.assertIn("article.html", str(stage.query_one("#stage-artifacts", Static).render()))

        import asyncio

        asyncio.run(exercise())

    def test_candidate_list_accepts_explicit_keyboard_selection(self) -> None:
        async def exercise() -> None:
            app = SmearglePaperApp()
            async with app.run_test(size=(136, 51)) as pilot:
                screen = app.screen
                manifest = {
                    "workflow": "paper-to-article",
                    "status": "waiting_input",
                    "request": {"inputs": {"topic": "agents"}},
                    "steps": [{"id": "choose", "status": "completed", "error": None}],
                    "artifacts": [],
                    "quality": {},
                    "interaction": {
                        "type": "candidate_selection",
                        "status": "pending",
                        "options": [
                            {
                                "paper_id": f"2607.1000{index}",
                                "title": f"Candidate {index}",
                                "published_at": "2026-07-20",
                                "one_sentence_contribution": "A useful contribution.",
                                "recommendation_reasons": ["与主题相关"],
                            }
                            for index in range(1, 4)
                        ],
                    },
                }
                screen._presentation = build_run_presentation(manifest)
                screen._update_header()
                await pilot.pause()
                self.assertEqual(len(screen.query_one("#stage-candidates", ListView).children), 3)
                with patch.object(screen, "_choose_candidate") as choose:
                    candidates = screen.query_one("#stage-candidates", ListView)
                    candidates.index = 1
                    candidates.focus()
                    await pilot.press("enter")
                choose.assert_called_once_with("2607.10002", "Candidate 2")

        import asyncio

        asyncio.run(exercise())

    def test_runtime_flow_renders_real_events_and_switches_agent_view(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                app = SmearglePaperApp()
                async with app.run_test(size=(140, 48)) as pilot:
                    screen = app.screen
                    self.assertIsInstance(screen, AgentConsole)
                    screen.runtime = __import__(
                        "smearglepaper.runtime", fromlist=["AgentRuntime"]
                    ).AgentRuntime(Path(tmp))
                    screen._handle_user_input("/offline 解读离线示例论文")
                    for _ in range(200):
                        await pilot.pause()
                        if not screen._busy and screen._active_run_id:
                            break
                    self.assertFalse(screen._busy)
                    self.assertTrue(screen._active_run_id)
                    manifest = screen.runtime.get_run(screen._active_run_id)
                    self.assertEqual(manifest["status"], "completed")
                    self.assertTrue(any(item["producer"] == "write" for item in manifest["artifacts"]))
                    self.assertIn(
                        "已完成",
                        str(screen.query_one("#stage-status", Static).render()),
                    )
                    self.assertEqual(screen._presentation.subject, "A Small Offline Transformer Walkthrough")
                    self.assertIn("文章已生成", screen._presentation.headline)
                    visible = "\n".join(
                        message.content for message in screen._trace_messages if not message.detail_only
                    )
                    self.assertNotIn("/Users/", visible)
                    self.assertNotIn("Started", visible)

                    screen._handle_slash_command("/details")
                    self.assertTrue(screen._details_visible)
                    detailed = "\n".join(message.content for message in screen._trace_messages)
                    self.assertIn("Started", detailed)

                    screen._handle_slash_command("/artifacts")
                    screen.action_toggle_details()
                    screen._handle_slash_command("/artifact 1")
                    artifact_detail = "\n".join(
                        message.content for message in screen._trace_messages if not message.detail_only
                    )
                    self.assertIn("path:", artifact_detail)
                    screen.action_next_agent()
                    self.assertEqual(screen._active_agent, "scout")
                    self.assertTrue(screen._details_visible)
                    screen._active_agent = "writer"
                    screen._replay_agent_view()
                    self.assertEqual(screen._active_agent, "writer")

        import asyncio

        asyncio.run(exercise())

    def test_small_terminal_keeps_composer_inside_viewport(self) -> None:
        async def exercise() -> None:
            for size in ((80, 24), (136, 51), (140, 48)):
                app = SmearglePaperApp()
                async with app.run_test(size=size) as pilot:
                    await pilot.pause()
                    input_region = app.screen.query_one("#chat-input", Input).region
                    self.assertLessEqual(input_region.bottom, app.screen.size.height - 1)

        import asyncio

        asyncio.run(exercise())

    def test_approval_modal_exposes_reject_and_approve(self) -> None:
        async def exercise() -> None:
            app = SmearglePaperApp()
            async with app.run_test(size=(120, 40)) as pilot:
                decisions: list[bool | None] = []
                app.push_screen(
                    ApprovalModal(
                        {
                            "target": "WeChat draft",
                            "side_effect": "Create an external draft",
                            "artifact_paths": ["article.json"],
                            "quality": {"technical_score": 92, "wechat_score": 90},
                        }
                    ),
                    decisions.append,
                )
                await pilot.pause()
                self.assertIsNotNone(app.screen.query_one("#approval-reject"))
                self.assertIsNotNone(app.screen.query_one("#approval-approve"))
                await pilot.click("#approval-reject")
                await pilot.pause()
                self.assertEqual(decisions, [False])

        import asyncio

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
