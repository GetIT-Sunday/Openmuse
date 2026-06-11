from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from smearglepaper.tools import TOOLS, SYSTEM_PROMPT, execute_tool
from smearglepaper.tui import SmearglePaperApp, AgentConsole


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
                    "ingest_paper", "generate_article", "review_article",
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
        self.assertEqual(app.TITLE, "SmearglePaper Agent")

    def test_console_creates(self) -> None:
        screen = AgentConsole()
        self.assertEqual(screen.session.messages, [])
        self.assertFalse(screen._busy)
        self.assertTrue(screen._context_visible)


if __name__ == "__main__":
    unittest.main()
