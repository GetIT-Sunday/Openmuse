from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smearglepaper.harness import ConversationHarness, ConversationMemory
from smearglepaper.llm import _iter_sse_json


class _Response:
    def __iter__(self):
        return iter(
            [
                b": keepalive\n",
                'data: {"choices":[{"delta":{"content":"你好"}}]}\n\n'.encode(),
                'data: {"choices":[{"delta":{"content":"，研究者"},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":4}}\n\n'.encode(),
                b"data: [DONE]\n\n",
            ]
        )


class HarnessTests(unittest.TestCase):
    def test_sse_parser_yields_deltas_and_usage(self) -> None:
        rows = list(_iter_sse_json(_Response()))
        self.assertEqual(rows[0]["choices"][0]["delta"]["content"], "你好")  # type: ignore[index]
        self.assertEqual(rows[1]["usage"]["completion_tokens"], 4)  # type: ignore[index]

    def test_memory_is_bounded_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conversation.json"
            memory = ConversationMemory(path, max_turns=2)
            memory.append("user", "one")
            memory.append("assistant", "two")
            memory.append("user", "three")
            restored = ConversationMemory(path, max_turns=2)
            self.assertEqual([item["content"] for item in restored.turns], ["two", "three"])
            self.assertEqual(restored.context("system")[0]["role"], "system")

    def test_memory_can_be_cleared_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conversation.json"
            memory = ConversationMemory(path)
            memory.append("user", "保留前的内容")
            memory.summary = "旧摘要"
            memory.clear()
            restored = ConversationMemory(path)
            self.assertEqual(restored.turns, [])
            self.assertEqual(restored.summary, "")

    def test_harness_emits_ordered_streaming_turn_events(self) -> None:
        events = []
        with patch(
            "smearglepaper.harness.stream_chat",
            side_effect=lambda messages, on_delta, **kwargs: (
                on_delta("第一段"),
                on_delta("第二段"),
                {"content": "第一段第二段", "finish_reason": "stop", "usage": {"completion_tokens": 4}},
            )[-1],
        ):
            harness = ConversationHarness("session-1")
            reply = harness.run("写一篇", system_prompt="你是助手", on_event=events.append, model="test")
        self.assertEqual(reply, "第一段第二段")
        self.assertEqual([event.type for event in events], [
            "turn.started", "context.prepared", "model.started", "model.delta",
            "model.delta", "model.completed", "turn.completed",
        ])
        self.assertEqual([event.sequence for event in events], list(range(1, 8)))
        self.assertEqual(events[3].payload["text"], "第一段")

    def test_harness_executes_tool_then_streams_final_reply(self) -> None:
        events = []
        calls = []
        responses = iter(
            [
                {"content": "", "finish_reason": "tool_calls", "usage": {}, "tool_calls": [
                    {"id": "call-1", "name": "search", "arguments": {"topic": "agents"}},
                ]},
                {"content": "找到三篇候选。", "finish_reason": "stop", "usage": {}, "tool_calls": []},
            ]
        )

        def fake_stream(messages, on_delta, **kwargs):
            result = next(responses)
            if result["content"]:
                on_delta(result["content"])
            return result

        def fake_execute(name, arguments):
            calls.append((name, arguments))
            return '{"count": 3}'

        with patch("smearglepaper.harness.stream_chat", side_effect=fake_stream):
            harness = ConversationHarness("session-1")
            reply = harness.run(
                "找论文",
                system_prompt="system",
                on_event=events.append,
                tools=[{"type": "function"}],
                execute_tool=fake_execute,
            )
        self.assertEqual(reply, "找到三篇候选。")
        self.assertEqual(calls, [("search", {"topic": "agents"})])
        self.assertIn("tool.started", [event.type for event in events])
        self.assertIn("tool.completed", [event.type for event in events])

    def test_harness_policy_blocks_side_effect_and_truncates_tool_output(self) -> None:
        events = []
        responses = iter([
            {"content": "", "finish_reason": "tool_calls", "usage": {}, "tool_calls": [
                {"id": "call-1", "name": "create_wechat_draft", "arguments": {"dry_run": False}},
            ]},
            {"content": "需要确认", "finish_reason": "stop", "usage": {}, "tool_calls": []},
        ])

        def fake_stream(messages, on_delta, **kwargs):
            result = next(responses)
            if result["content"]:
                on_delta(result["content"])
            return result

        executed = []
        with patch("smearglepaper.harness.stream_chat", side_effect=fake_stream):
            harness = ConversationHarness("session-1")
            reply = harness.run(
                "发布",
                system_prompt="system",
                on_event=events.append,
                tools=[{"type": "function"}],
                execute_tool=lambda name, args: executed.append(name) or "x" * 100,
                allow_tool=lambda name, args: (False, "必须确认"),
                max_tool_output=10,
            )
        self.assertEqual(reply, "需要确认")
        self.assertEqual(executed, [])
        self.assertIn("approval.required", [event.type for event in events])

    def test_cancelled_turn_does_not_persist_assistant_reply(self) -> None:
        events = []
        harness = ConversationHarness("session-1")

        def fake_stream(messages, on_delta, **kwargs):
            harness.cancel()
            on_delta("ignored")
            return {"content": "ignored", "finish_reason": "stop", "usage": {}}

        with patch("smearglepaper.harness.stream_chat", side_effect=fake_stream):
            self.assertEqual(harness.run("hi", system_prompt="system", on_event=events.append), "")
        self.assertEqual(events[-1].type, "turn.cancelled")
        self.assertEqual([item["role"] for item in harness.memory.turns], ["user"])


if __name__ == "__main__":
    unittest.main()
