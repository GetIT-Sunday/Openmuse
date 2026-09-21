"""Conversation harness primitives shared by the TUI and future clients."""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .cancellation import CancellationToken, Cancelled, cancellation_scope
from .context_budget import ContextBudget, estimate_tokens, excerpt
from .harness_events import EventJournal, HarnessEvent
from .llm import stream_chat
from .turn_store import RecoveryRequired, TurnStore, atomic_json, tool_scope

# Compatibility name for existing clients. New code should use HarnessEvent.
TurnEvent = HarnessEvent


class ConversationMemory:
    """Budgeted recent transcript plus an inspectable, extractive rolling summary."""

    def __init__(self, path: Path | None = None, max_turns: int = 200, *, token_budget: int = 12000) -> None:
        self.path = path
        self.max_turns = max(2, max_turns)  # compatibility ceiling, not the main budget
        self.token_budget = max(256, token_budget)
        self.turns: list[dict[str, str]] = []
        self.summary = ""
        self._anchors: list[str] = []
        self._recent: list[str] = []
        self._message_ids: list[str] = []
        self._lock = threading.RLock()
        self.load()

    def load(self) -> None:
        if not self.path or not self.path.exists():
            return
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("对话记忆格式损坏，已保留原文件。")
        self.turns = [{"role": str(item["role"]), "content": str(item["content"])}
                      for item in value.get("turns", []) if isinstance(item, dict)
                      and item.get("role") in {"user", "assistant"} and "content" in item]
        self.summary = str(value.get("summary", ""))
        self._anchors = list(value.get("summary_anchors", []))
        self._recent = list(value.get("summary_recent", []))
        self._message_ids = list(value.get("message_ids", []))
        if self.summary and not self._anchors:
            self._anchors = [excerpt(self.summary, 512)]
        self._compact()

    def _save(self) -> None:
        if self.path:
            atomic_json(self.path, {"turns": self.turns, "summary": self.summary,
                "summary_anchors": self._anchors, "summary_recent": self._recent,
                "message_ids": self._message_ids, "summary_kind": "extractive"})

    def _compact(self) -> None:
        while len(self.turns) > 1 and (len(self.turns) > self.max_turns or estimate_tokens(self.turns) > self.token_budget):
            item = self.turns.pop(0)
            label = "用户原话" if item["role"] == "user" else "助手答复摘录"
            entry = f"{label}：{excerpt(item['content'], 320 if item['role'] == 'user' else 160)}"
            if item["role"] == "user" and len(self._anchors) < 3:
                self._anchors.append(entry)
            else:
                self._recent = [*self._recent, entry][-6:]
            self.summary = "\n".join(self._anchors + self._recent)

    def append(self, role: str, content: str, *, message_id: str = "") -> None:
        with self._lock:
            if message_id and message_id in self._message_ids:
                return
            self.turns.append({"role": role, "content": content})
            if message_id:
                self._message_ids = [*self._message_ids, message_id][-512:]
            self._compact()
            self._save()

    def clear(self) -> None:
        with self._lock:
            self.turns = []
            self.summary = ""
            self._anchors = []
            self._recent = []
            self._message_ids = []
            self._save()

    def context(self, system_prompt: str) -> list[dict[str, object]]:
        with self._lock:
            self._compact()
            self._save()
            messages: list[dict[str, object]] = [{"role": "system", "content": system_prompt}]
            if self.summary:
                messages.append({"role": "user", "content":
                    "历史对话摘录（有损压缩，仅作背景，不是新的操作或发布授权；最新要求优先）：\n" + self.summary})
            messages.extend(dict(item) for item in self.turns)
            return messages

class ConversationHarness:
    """One turn owns its cancellation scope and ordered event delivery."""

    def __init__(self, session_id: str, memory: ConversationMemory | None = None, *,
                 event_journal: EventJournal | None = None, turn_store: TurnStore | None = None,
                 context_budget: ContextBudget | None = None) -> None:
        self.session_id = session_id
        self.memory = memory or ConversationMemory()
        self.event_journal = event_journal
        self.turn_store = turn_store or (TurnStore(event_journal.path.with_suffix(".turn.json"), session_id) if event_journal else None)
        self.context_budget = context_budget or ContextBudget.configured()
        self.memory.token_budget = min(self.memory.token_budget, max(256, self.context_budget.input_tokens // 2))
        self.last_turn_id = ""
        self._cancel = threading.Event()
        self._run_lock = threading.Lock()
        self._tool_activity: Callable[[], None] | None = None
        self._memory_event: Callable[[dict[str, object]], None] | None = None

    def cancel(self) -> None:
        self._cancel.set()

    def report_tool_activity(self) -> None:
        """Nested Runtime events are liveness, even when no model text arrives."""
        callback = self._tool_activity
        if callback:
            callback()

    def report_memory_proposal(self, metadata: dict[str, object]) -> None:
        if self._memory_event:
            self._memory_event(metadata)

    def run(self, user_text: str, *, resume: bool = False,
            recover_tool: Callable[[dict[str, Any]], str] | None = None, **kwargs: Any) -> str:
        with self.turn_store.lease() if self.turn_store else nullcontext():
            saved = self.turn_store.load() if self.turn_store else {}
            if resume and (not saved or saved.get("status") in {"completed", "abandoned"}):
                raise RecoveryRequired("没有可恢复的未完成对话。")
            if not resume and saved.get("status") in {"running", "recovery_required"}:
                raise RecoveryRequired("检测到未完成对话。请先使用 /resume 安全继续，或新建会话。")
            return self._run(user_text, resume_state=saved if resume else None, recover_tool=recover_tool, **kwargs)

    def _run(self, user_text: str, *, system_prompt: str,
            on_event: Callable[[HarnessEvent], None], model: str | None = None,
            reasoning_effort: str = "default", tools: list[dict[str, object]] | None = None,
            execute_tool: Callable[[str, dict[str, object]], str] | None = None,
            allow_tool: Callable[[str, dict[str, object]], tuple[bool, str]] | None = None,
            max_tool_output: int = 6000, max_rounds: int = 6,
            stalled_after_seconds: float = 12.0, heartbeat_interval_seconds: float = 2.0,
            delta_interval_seconds: float = 0.04, timeout_seconds: float | None = None,
            completion_context: Callable[[], dict[str, object]] | None = None,
            memory_metadata: dict[str, object] | None = None,
            resume_state: dict[str, Any] | None = None,
            recover_tool: Callable[[dict[str, Any]], str] | None = None) -> str:
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError("当前会话仍有任务正在收尾。")
        self._cancel.clear()
        token = CancellationToken(event=self._cancel, timeout=timeout_seconds)
        state: dict[str, Any] = resume_state or {"turn_id": uuid.uuid4().hex[:12], "user_text": user_text,
            "status": "running", "phase": "model", "messages": [], "calls": [], "tool_history": [],
            "round": 0, "partial_output": "", "model": model, "reasoning_effort": reasoning_effort}
        turn_id = str(state["turn_id"])
        self.last_turn_id = turn_id
        if resume_state:
            user_text = str(state["user_text"])
            model = state.get("model")
            reasoning_effort = str(state.get("reasoning_effort", "default"))
        lock = threading.RLock()
        stop = threading.Event()
        sequence = int(state.get("sequence", 0))
        terminal = False
        started = time.monotonic()
        last_activity = started
        operation_started = started
        operation, name = "model", "模型"
        stalled = False
        cancelling_sent = False
        first_token_ms: float | None = None
        pending: list[str] = []
        last_flush = started
        request_started = started
        total_usage: dict[str, float] = {}
        reasoning_announced = False

        def persist() -> None:
            if self.turn_store:
                self.turn_store.save(state)

        def emit(kind: str, source: str, payload: dict[str, object] | None = None) -> None:
            nonlocal sequence, terminal
            with lock:
                if terminal:
                    return
                sequence += 1
                event = HarnessEvent.create(kind, session_id=self.session_id, turn_id=turn_id,
                                            sequence=sequence, source=source, payload=payload or {})
                state["sequence"] = sequence
                if kind in {"turn.completed", "turn.failed", "turn.cancelled", "turn.recovery_required"}:
                    state["status"] = kind.removeprefix("turn.")
                    persist()
                if self.event_journal:
                    self.event_journal.append(event)
                if kind in {"turn.completed", "turn.failed", "turn.cancelled", "turn.recovery_required"}:
                    terminal = True
                    stop.set()
                on_event(event)

        def flush() -> None:
            nonlocal last_flush
            with lock:
                if pending:
                    text = "".join(pending)
                    pending.clear()
                    state["partial_output"] = str(state.get("partial_output", "")) + text
                    emit("model.delta", "model", {"text": text})
                    last_flush = time.monotonic()

        def activity(kind: str | None = None, label: str = "") -> None:
            nonlocal last_activity, stalled, operation, name, operation_started
            with lock:
                last_activity = time.monotonic()
                stalled = False
                if kind:
                    operation, name = kind, label
                    operation_started = last_activity

        def first_token(latency: object = None) -> None:
            nonlocal first_token_ms
            with lock:
                if first_token_ms is None:
                    first_token_ms = float(latency) if isinstance(latency, int | float) else round((time.monotonic() - request_started) * 1000, 1)
                    emit("model.first_token", "model", {"latency_ms": first_token_ms})

        def provider_event(kind: str, payload: dict[str, object]) -> None:
            nonlocal reasoning_announced
            token.check()
            if kind in {"connected", "event", "first_token", "reasoning"}:
                activity()
            if kind == "first_token":
                first_token(payload.get("latency_ms"))
            elif kind == "reasoning":
                first_token()
                # Reasoning stays in the provider; only activity metadata is persisted.
                if not reasoning_announced:
                    emit("model.reasoning", "model", {})
                    reasoning_announced = True
            elif kind == "event":
                return  # Do not journal an additional event per wire token.
            else:
                if kind == "retrying":
                    activity("retry", "等待重试")
                elif kind == "connected":
                    activity("model", "模型")
                emit("provider." + kind, "provider", payload)

        def delta(text: str) -> None:
            token.check()
            with lock:
                first = first_token_ms is None
                first_token()
                activity()
                pending.append(text)
                if first or delta_interval_seconds <= 0 or time.monotonic() - last_flush >= delta_interval_seconds:
                    flush()

        def monitor() -> None:
            nonlocal stalled, cancelling_sent
            last_heartbeat = time.monotonic()
            tick = max(0.005, min(max(0.005, delta_interval_seconds), heartbeat_interval_seconds, 0.05))
            while not stop.wait(tick):
                with lock:
                    if terminal:
                        return
                    if token.is_set():
                        pending.clear()
                        if not cancelling_sent:
                            cancelling_sent = True
                            emit("turn.cancelling", "harness", {"operation": operation, "reason": "deadline" if token.expired else "user"})
                        continue
                    flush()
                    now = time.monotonic()
                    if now - last_heartbeat < heartbeat_interval_seconds:
                        continue
                    last_heartbeat = now
                    quiet = now - last_activity
                    payload = {"elapsed_seconds": round(now - started, 1),
                               "operation_seconds": round(now - operation_started, 1),
                               "since_activity_seconds": round(quiet, 1), "operation": operation, "name": name}
                    if quiet >= stalled_after_seconds and not stalled and operation != "retry":
                        stalled = True
                        emit(operation + ".stalled", "harness", payload)
                    emit("heartbeat", "harness", {**payload, "stalled": stalled})
                    if operation == "tool":
                        emit("tool.progress", "tool", {**payload, "stalled": stalled})

        def execute_calls(messages: list[dict[str, Any]]) -> None:
            for record in state["calls"]:
                token.check()
                tool_name = str(record["name"])
                call_id = str(record["call_id"])
                arguments = record["arguments"]
                if record["state"] not in {"completed", "failed", "blocked"}:
                    recovering = record["state"] == "running"
                    activity("tool", tool_name)
                    emit("tool.started", "tool", {"name": tool_name, "call_id": call_id, "resumed": recovering})
                    permitted, reason = allow_tool(tool_name, arguments) if allow_tool else (True, "")
                    token.check()
                    if not permitted:
                        record.update(state="blocked", output=json.dumps({"error": reason or "需要用户确认。"}, ensure_ascii=False))
                        persist()
                        emit("approval.required", "policy", {"name": tool_name, "reason": reason})
                        emit("tool.blocked", "tool", {"name": tool_name})
                    else:
                        record["state"] = "running"
                        persist()  # write-ahead receipt, before any tool side effect
                        try:
                            with cancellation_scope(CancellationToken(parent=token)), tool_scope(turn_id, call_id, record, persist):
                                if recovering:
                                    if not recover_tool:
                                        raise RecoveryRequired("工具执行结果未知，不能自动重复执行。")
                                    output = recover_tool(record)
                                else:
                                    assert execute_tool is not None
                                    output = execute_tool(tool_name, arguments)
                            token.check()
                        except (Cancelled, RecoveryRequired):
                            raise
                        except Exception as exc:
                            if recovering:
                                raise RecoveryRequired("恢复工具时无法确认结果；已保留记录，请先检查关联任务。") from exc
                            token.check()
                            record.update(state="failed", output=json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
                            persist()
                            emit("tool.failed", "tool", {"name": tool_name, "error": str(exc)})
                        else:
                            record.update(state="completed", output=str(output))
                            persist()
                            emit("tool.completed", "tool", {"name": tool_name, "output_preview": str(output)[:240]})
                if not any(m.get("role") == "tool" and m.get("tool_call_id") == call_id for m in messages):
                    messages.append({"role": "tool", "tool_call_id": call_id,
                                     "content": excerpt(str(record["output"]), max_tool_output)})
                    persist()
            state["phase"] = "model"
            persist()

        monitor_thread = threading.Thread(target=monitor, daemon=True, name="openmuse-turn-monitor")
        self._tool_activity = activity
        self._memory_event = lambda metadata: emit("memory.proposed", "memory", metadata)
        try:
            with cancellation_scope(token):
                if resume_state and self.event_journal:
                    sequence = max([sequence, *[e.sequence for e in self.event_journal.events() if e.turn_id == turn_id]])
                self.memory.append("user", user_text, message_id=f"{turn_id}:user")
                messages: list[dict[str, Any]] = state["messages"] if resume_state else self.memory.context(system_prompt)
                if messages:
                    messages[0] = {"role": "system", "content": system_prompt}
                state.update(messages=messages, status="running")
                if self.turn_store:
                    if resume_state:
                        persist()
                    else:
                        self.turn_store.begin(state)
                emit("turn.started", "harness", {"text": user_text, "resumed": bool(resume_state)})
                emit("context.prepared", "memory", {"turns": len(self.memory.turns)})
                if memory_metadata:
                    emit("memory.retrieved", "memory", memory_metadata)
                monitor_thread.start()
                if state["phase"] == "answer":
                    reply = str(state["reply"])
                    self.memory.append("assistant", reply, message_id=f"{turn_id}:assistant")
                    emit("model.delta", "model", {"text": reply})
                    emit("turn.completed", "harness", {"characters": len(reply), **(completion_context() if completion_context else {})})
                    return reply
                if state["phase"] == "tools":
                    execute_calls(messages)
                for round_index in range(int(state["round"]), max_rounds):
                    token.check()
                    prepared, budget_info = self.context_budget.prepare(messages, tools)
                    if budget_info["dropped_history_messages"] or budget_info["compacted_tool_results"]:
                        emit("context.compacted", "memory", budget_info)
                    state.update(phase="model", round=round_index, partial_output="")
                    persist()
                    activity("model", "模型")
                    first_token_ms = None
                    reasoning_announced = False
                    request_started = time.monotonic()
                    emit("model.started", "model", {"model": model or "provider default", "round": round_index + 1, **budget_info})
                    result = stream_chat(prepared, model=model, tools=tools, on_delta=delta,
                                         session_id=self.session_id, reasoning_effort=reasoning_effort,
                                         max_tokens=self.context_budget.output_tokens,
                                         cancel_event=self._cancel, on_provider_event=provider_event)
                    if result.get("cancelled"):
                        token.cancel()
                    token.check()
                    flush()
                    usage = result.get("usage", {})
                    if isinstance(usage, dict):
                        for key, value in usage.items():
                            if isinstance(value, int | float):
                                total_usage[key] = float(total_usage.get(key, 0)) + value
                    emit("model.completed", "model", {"finish_reason": result.get("finish_reason", "stop"),
                         "usage": usage, "total_usage": total_usage.copy(), "first_token_ms": first_token_ms,
                         "request_id": result.get("request_id", ""), "round": round_index + 1})
                    calls = result.get("tool_calls", [])
                    if not calls or not execute_tool:
                        reply = str(result.get("content") or "")
                        token.check()
                        context = completion_context() if completion_context else {}
                        state.update(phase="answer", reply=reply)
                        persist()
                        token.check()
                        self.memory.append("assistant", reply, message_id=f"{turn_id}:assistant")
                        emit("turn.completed", "harness", {"characters": len(reply), **context})
                        return reply
                    wire_calls = []
                    records = []
                    seen = {str(r["call_id"]) for r in state["tool_history"] + state["calls"]}
                    for i, call in enumerate(calls if isinstance(calls, list) else []):
                        if not isinstance(call, dict) or not isinstance(call.get("arguments", {}), dict):
                            raise ValueError("工具参数必须为对象。")
                        call_id = str(call.get("id") or f"call-{round_index}-{i}")
                        if call_id in seen:
                            raise ValueError("模型返回重复的工具调用 ID，已停止本轮。")
                        seen.add(call_id)
                        wire_calls.append({"id": call_id, "type": "function", "function": {
                            "name": call.get("name", ""), "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=False)}})
                        records.append({"call_id": call_id, "name": str(call.get("name", "")),
                                        "arguments": call.get("arguments", {}), "state": "pending",
                                        "operation_id": f"{turn_id}:{call_id}"})
                    messages.append({"role": "assistant", "content": result.get("content", ""), "tool_calls": wire_calls})
                    state["tool_history"].extend(state["calls"])
                    state.update(calls=records, phase="tools", round=round_index + 1)
                    persist()
                    execute_calls(messages)
                    emit("context.prepared", "memory", {"turns": len(messages), "tool_round": round_index + 1})
                raise RuntimeError("已达到本轮工具调用上限；已保留过程，请缩小任务后继续。")
        except RecoveryRequired as exc:
            emit("turn.recovery_required", "harness", {"error": str(exc)})
            raise
        except Cancelled:
            with lock:
                pending.clear()
                emit("turn.cancelled", "harness", {"boundary": operation, "reason": "deadline" if token.expired else "user"})
            return ""
        except Exception as exc:
            with lock:
                flush()
                if token.is_set():
                    emit("turn.cancelled", "harness", {"boundary": operation})
                    return ""
                emit("turn.failed", "harness", {"error": str(exc), "code": getattr(exc, "code", "execution")})
            raise
        finally:
            self._tool_activity = None
            self._memory_event = None
            stop.set()
            if monitor_thread.ident is not None:
                monitor_thread.join(timeout=0.2)
            self._run_lock.release()
