"""Conversation harness primitives shared by the TUI and future clients."""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .llm import stream_chat


@dataclass(frozen=True)
class TurnEvent:
    type: str
    session_id: str
    turn_id: str
    sequence: int
    source: str
    payload: dict[str, object] = field(default_factory=dict)
    timestamp: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "type": self.type,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "sequence": self.sequence,
            "source": self.source,
            "payload": self.payload,
            "timestamp": self.timestamp or datetime.now(timezone.utc).isoformat(),
        }


class ConversationMemory:
    """Small, inspectable transcript store with a bounded prompt window."""

    def __init__(self, path: Path | None = None, max_turns: int = 20) -> None:
        self.path = path
        self.max_turns = max(2, max_turns)
        self.turns: list[dict[str, str]] = []
        self.summary = ""
        self._lock = threading.RLock()
        self.load()

    def load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                self.turns = [item for item in value.get("turns", []) if isinstance(item, dict)][-self.max_turns :]
                self.summary = str(value.get("summary", ""))
        except (OSError, json.JSONDecodeError):
            return

    def append(self, role: str, content: str) -> None:
        with self._lock:
            self.turns.append({"role": role, "content": content})
            self.turns = self.turns[-self.max_turns :]
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps({"turns": self.turns, "summary": self.summary}, ensure_ascii=False, indent=2), encoding="utf-8")

    def clear(self) -> None:
        """Forget the conversational context while keeping the session itself."""
        with self._lock:
            self.turns = []
            self.summary = ""
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(
                    json.dumps({"turns": [], "summary": ""}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

    def context(self, system_prompt: str) -> list[dict[str, object]]:
        with self._lock:
            messages: list[dict[str, object]] = [{"role": "system", "content": system_prompt}]
            if self.summary:
                messages.append({"role": "system", "content": f"对话摘要：{self.summary}"})
            messages.extend(self.turns)
            return messages


class ConversationHarness:
    """Turn-oriented streaming controller; durable workflows remain tools."""

    def __init__(self, session_id: str, memory: ConversationMemory | None = None) -> None:
        self.session_id = session_id
        self.memory = memory or ConversationMemory()
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(
        self,
        user_text: str,
        *,
        system_prompt: str,
        on_event: Callable[[TurnEvent], None],
        model: str | None = None,
        reasoning_effort: str = "default",
        tools: list[dict[str, object]] | None = None,
        execute_tool: Callable[[str, dict[str, object]], str] | None = None,
        allow_tool: Callable[[str, dict[str, object]], tuple[bool, str]] | None = None,
        max_tool_output: int = 6000,
        max_rounds: int = 6,
    ) -> str:
        turn_id = uuid.uuid4().hex[:12]
        sequence = 0

        def emit(event_type: str, source: str, payload: dict[str, object] | None = None) -> None:
            nonlocal sequence
            sequence += 1
            on_event(TurnEvent(event_type, self.session_id, turn_id, sequence, source, payload or {}, datetime.now(timezone.utc).isoformat()))

        self._cancel.clear()
        self.memory.append("user", user_text)
        emit("turn.started", "harness", {"text": user_text})
        emit("context.prepared", "memory", {"turns": len(self.memory.turns), "has_summary": bool(self.memory.summary)})
        emit("model.started", "model", {"model": model or "provider default"})
        content: list[str] = []
        last_heartbeat = time.monotonic()

        def delta(text: str) -> None:
            nonlocal last_heartbeat
            if self._cancel.is_set():
                return
            content.append(text)
            emit("model.delta", "model", {"text": text})
            now = time.monotonic()
            if now - last_heartbeat >= 2:
                emit("heartbeat", "harness", {"elapsed_seconds": round(now - last_heartbeat, 1)})
                last_heartbeat = now

        try:
            messages = self.memory.context(system_prompt)
            result: dict[str, object] = {}
            for round_index in range(max_rounds):
                result = stream_chat(
                    messages,
                    model=model,
                    tools=tools,
                    on_delta=delta,
                    session_id=self.session_id,
                    reasoning_effort=reasoning_effort,
                )
                calls = result.get("tool_calls", [])
                if not calls or not execute_tool:
                    break
                assistant_content = str(result.get("content", ""))
                wire_calls = []
                for call in calls if isinstance(calls, list) else []:
                    if not isinstance(call, dict):
                        continue
                    wire_calls.append({
                        "id": call.get("id", uuid.uuid4().hex[:8]),
                        "type": "function",
                        "function": {"name": call.get("name", ""), "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=False)},
                    })
                messages.append({"role": "assistant", "content": assistant_content, "tool_calls": wire_calls})
                for call in calls if isinstance(calls, list) else []:
                    if not isinstance(call, dict):
                        continue
                    name = str(call.get("name", ""))
                    arguments = call.get("arguments", {})
                    if not isinstance(arguments, dict):
                        arguments = {}
                    emit("tool.started", "tool", {"name": name, "round": round_index + 1})
                    permitted, reason = allow_tool(name, arguments) if allow_tool else (True, "")
                    if not permitted:
                        output = json.dumps({"error": reason or "Tool call requires user confirmation."}, ensure_ascii=False)
                        emit("approval.required", "policy", {"name": name, "reason": reason})
                    else:
                        try:
                            output = execute_tool(name, arguments)
                        except Exception as exc:  # Keep the loop alive with a typed tool error.
                            output = json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)
                            emit("tool.failed", "tool", {"name": name, "error": str(exc)})
                    output = str(output)[:max_tool_output]
                    messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": output})
                    emit("tool.completed", "tool", {"name": name, "output_preview": output[:240]})
                emit("context.prepared", "memory", {"turns": len(messages), "tool_round": round_index + 1})
            reply = str(result.get("content", ""))
            if self._cancel.is_set():
                emit("turn.cancelled", "harness", {})
                return ""
            self.memory.append("assistant", reply)
            emit("model.completed", "model", {"finish_reason": result.get("finish_reason", "stop"), "usage": result.get("usage", {})})
            emit("turn.completed", "harness", {"characters": len(reply)})
            return reply
        except Exception as exc:
            emit("turn.failed", "harness", {"error": f"{type(exc).__name__}: {exc}"})
            raise
