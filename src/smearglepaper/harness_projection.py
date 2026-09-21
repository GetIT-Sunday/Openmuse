"""Pure projection of Harness events into user-facing state."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .harness_events import normalize_event


@dataclass
class ProjectionState:
    """The minimum UI state needed to render a turn or runtime replay."""

    session_id: str = ""
    turn_id: str = ""
    run_id: str = ""
    status: str = "idle"
    activity: str = ""
    phase: str = ""
    last_error: str = ""
    terminal_reason: str = ""
    assistant_text: str = ""
    pending_request: dict[str, Any] | None = None
    event_count: int = 0
    last_sequence: int = 0
    tool_states: dict[str, str] = field(default_factory=dict)
    stalled: bool = False
    first_token_ms: float | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    operation: str = ""
    elapsed_seconds: float = 0


class HarnessProjection:
    """Deterministic reducer shared by live TUI updates and event replay."""

    PHASES = {
        "collect": "选择材料",
        "rank": "选择材料",
        "choose": "选择材料",
        "select": "选择材料",
        "ingest": "理解论文",
        "write": "撰写文章",
        "review": "质量审阅",
        "package": "准备预览",
        "publish": "准备预览",
    }
    TOOL_LABELS = {
        "collect_papers": "搜索近期论文", "rank_papers": "筛选候选论文",
        "ingest_paper": "读取论文和图表", "generate_article": "生成文章",
        "run_writing_agent": "撰写并审阅文章", "review_article": "检查文章质量",
        "improve_article": "修改文章", "create_wechat_draft": "准备微信草稿",
        "start_research": "准备研究材料", "select_candidate": "确认论文",
        "write_article": "撰写文章", "revise_article": "修订当前文章",
        "read_article": "阅读当前文章", "rediscover": "重新寻找选题",
        "preview_article": "打开手机预览", "request_draft": "请求草稿审批",
    }

    def __init__(self) -> None:
        self.state = ProjectionState()
        self._terminal_turns: set[tuple[str, str]] = set()

    def reset(self) -> None:
        self.state = ProjectionState()
        self._terminal_turns.clear()

    def apply(self, event: object) -> ProjectionState:
        item = normalize_event(event)
        state = self.state
        key = (item.session_id, item.turn_id)
        if key in self._terminal_turns:
            if item.type not in {"turn.started", "run.started"}:
                return state
            self._terminal_turns.discard(key)
        if item.type in {"turn.completed", "turn.failed", "turn.cancelled", "turn.recovery_required", "run.completed", "run.failed", "run.cancelled", "run.recovery_required"}:
            self._terminal_turns.add(key)
        state.session_id = item.session_id or state.session_id
        state.turn_id = item.turn_id or state.turn_id
        state.run_id = item.run_id or state.run_id
        state.event_count += 1
        state.last_sequence = max(state.last_sequence, item.sequence)
        payload = item.payload
        event_type = item.type
        if state.status == "cancelling" and event_type in {"heartbeat", "tool.progress", "model.delta", "model.stalled", "tool.stalled"}:
            return state

        if event_type in {"session.interrupted", "turn.recovery_required", "run.recovery_required"}:
            state.status = "recovery_required" if event_type != "session.interrupted" or payload.get("status") == "recovery_required" else "interrupted"
            state.stalled = False
            state.activity = "外部操作结果待核对，已阻止自动重试" if state.status == "recovery_required" else "上次任务已中断，可以安全继续"
        elif event_type in {"turn.started", "run.started"}:
            state.status = "running"
            state.activity = "正在处理"
            state.terminal_reason = ""
            state.last_error = ""
            state.pending_request = None
            state.assistant_text = ""
            state.stalled = False
            state.first_token_ms = None
            state.usage = {}
            state.request_id = ""
            state.operation = ""
            state.elapsed_seconds = 0
            state.tool_states = {}
        elif event_type == "context.prepared":
            state.activity = "正在准备上下文"
        elif event_type == "model.started":
            state.status = "running"
            state.activity = "正在连接模型"
            state.operation = "model"
            state.stalled = False
            state.first_token_ms = None
        elif event_type == "provider.connected":
            state.status = "running"
            state.operation = "model"
            state.activity = "已连接模型 · 等待响应"
        elif event_type in {"provider.event", "provider.first_token", "model.reasoning"}:
            state.operation = "model"
            state.stalled = False
            state.activity = "正在思考"
        elif event_type == "provider.retrying":
            state.status = "running"
            state.operation = "retry"
            state.stalled = False
            state.activity = f"连接暂时中断 · {payload.get('delay_seconds', 0)} 秒后重试（第 {payload.get('attempt', 2)} 次）"
        elif event_type == "turn.cancelling":
            state.status = "cancelling"
            state.stalled = False
            state.activity = "正在取消 · 等待当前操作安全结束"
        elif event_type == "model.first_token":
            state.status = "running"
            state.stalled = False
            state.operation = "model"
            value = payload.get("latency_ms")
            state.first_token_ms = float(value) if isinstance(value, int | float) else state.first_token_ms
            state.activity = f"已收到模型首段响应 · {value or 0} ms"
        elif event_type in {"model.delta", "reasoning.delta"}:
            state.status = "running"
            state.stalled = False
            state.operation = "model"
            state.activity = "正在回答 · 内容持续生成中"
            if event_type == "model.delta":
                state.assistant_text += str(payload.get("text", ""))
        elif event_type == "heartbeat":
            elapsed = payload.get("elapsed_seconds", 0)
            state.elapsed_seconds = float(elapsed)
            state.operation = str(payload.get("operation", state.operation or "model"))
            state.stalled = bool(payload.get("stalled", False))
            if state.stalled:
                state.activity = "工具响应较慢 · 仍在处理" if state.operation == "tool" else "响应较慢 · 仍在等待模型"
            else:
                if state.operation == "tool":
                    name = str(payload.get("name", "工具"))
                    state.activity = f"正在处理 · {self.TOOL_LABELS.get(name, name)} · {payload.get('operation_seconds', elapsed)} 秒"
                elif state.operation != "retry":
                    state.activity = f"正在响应 · 已用时 {elapsed} 秒"
        elif event_type in {"model.stalled", "tool.stalled"}:
            state.status = "running"
            state.stalled = True
            state.operation = "tool" if event_type == "tool.stalled" else "model"
            state.activity = "工具响应较慢 · 仍在处理" if state.operation == "tool" else "响应较慢 · 仍在等待模型"
        elif event_type in {"tool.started", "tool.progress"}:
            name = str(payload.get("name") or payload.get("tool") or "工作")
            state.status = "running"
            state.operation = "tool"
            state.stalled = bool(payload.get("stalled", False))
            state.tool_states[name] = "running"
            label = self.TOOL_LABELS.get(name, name)
            state.activity = "工具响应较慢 · 仍在处理" if state.stalled else f"正在处理 · {label}"
        elif event_type == "tool.blocked":
            state.tool_states[str(payload.get("name", "工具"))] = "blocked"
        elif event_type in {"tool.completed", "tool.failed"}:
            name = str(payload.get("name") or payload.get("tool") or "工作")
            state.tool_states[name] = "failed" if event_type == "tool.failed" else "completed"
            state.operation = "tool"
            state.stalled = False
            label = self.TOOL_LABELS.get(name, name)
            state.activity = f"已完成 · {label}" if event_type == "tool.completed" else f"未完成 · {label}"
            if event_type == "tool.failed":
                state.last_error = str(payload.get("error", "工具执行失败"))
        elif event_type in {"step.started", "step.retrying"}:
            step = str(payload.get("step", ""))
            state.status = "running"
            state.operation = "runtime"
            state.phase = self.PHASES.get(step, step)
            state.activity = f"正在{state.phase or '处理'}"
        elif event_type == "step.completed":
            step = str(payload.get("step", ""))
            state.phase = self.PHASES.get(step, state.phase)
            state.activity = f"已完成 · {state.phase or step}"
        elif event_type == "interaction.required" or event_type == "question.required":
            state.status = "waiting_input"
            state.pending_request = dict(payload)
            state.activity = "等待你的选择"
        elif event_type == "interaction.resolved" or event_type == "question.resolved":
            state.pending_request = None
            state.status = "running"
            state.activity = "已确认，继续处理"
        elif event_type == "approval.required" or event_type == "permission.required":
            state.status = "waiting_approval"
            state.pending_request = dict(payload)
            state.activity = "等待你的确认"
        elif event_type == "approval.resolved" or event_type == "permission.resolved":
            state.pending_request = None
            state.status = "running"
            state.activity = "已确认，继续处理"
            if payload.get("decision") == "rejected":
                state.status = "cancelled"
                state.terminal_reason = "rejected"
                state.activity = "已拒绝创建草稿"
        elif event_type == "run.status_changed":
            status = str(payload.get("status", ""))
            state.status = status
            state.activity = {
                "cancelling": "正在取消",
                "cancelled": "已取消",
            }.get(status, state.activity)
        elif event_type in {"turn.completed", "run.completed"}:
            state.status = "completed"
            state.stalled = False
            state.terminal_reason = str(payload.get("reason", "completed"))
            state.activity = "已完成"
            if event_type == "turn.completed" and payload.get("task_status") in {"waiting_input", "waiting_approval"}:
                state.status = str(payload["task_status"])
                state.pending_request = dict(payload.get("pending_request") or {})
                state.activity = "等待你的选择" if state.status == "waiting_input" else "等待你的确认"
        elif event_type in {"turn.cancelled", "run.cancelled"}:
            state.status = "cancelled"
            state.stalled = False
            state.terminal_reason = str(payload.get("reason", "cancelled"))
            state.activity = "已取消"
        elif event_type in {"turn.failed", "run.failed", "step.failed"}:
            state.status = "failed"
            state.stalled = False
            state.last_error = str(payload.get("error", "任务未完成"))
            state.terminal_reason = "failed"
            state.activity = "未完成"
        if event_type == "model.completed":
            state.usage = dict(payload.get("total_usage") or payload.get("usage") or {})
            value = payload.get("first_token_ms")
            if isinstance(value, int | float):
                state.first_token_ms = float(value)
            state.request_id = str(payload.get("request_id", ""))
            state.stalled = False
            state.operation = "model"
            state.activity = "正在整理回答"
        return state

    def replay(self, events: Iterable[object]) -> ProjectionState:
        self.reset()
        for event in sorted((normalize_event(item) for item in events), key=lambda item: item.sequence):
            self.apply(event)
        return self.state
