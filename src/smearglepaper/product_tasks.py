from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import DATA_DIR
from .storage import read_json, write_json

TASK_SCHEMA_VERSION = 1

TERMINAL_STATUSES = {"draft_created", "published", "failed", "cancelled"}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "created": {"planning", "cancelled"},
    "planning": {"discovering", "researching", "reviewing", "needs_attention", "failed", "cancelled"},
    "discovering": {"awaiting_topic_approval", "needs_attention", "failed", "cancelled"},
    "awaiting_topic_approval": {"researching", "cancelled"},
    "researching": {"writing", "needs_attention", "failed", "cancelled"},
    "writing": {"reviewing", "needs_attention", "failed", "cancelled"},
    "reviewing": {"preparing_assets", "awaiting_publish_approval", "needs_attention", "failed", "cancelled"},
    "preparing_assets": {"awaiting_publish_approval", "needs_attention", "failed", "cancelled"},
    "awaiting_publish_approval": {"creating_draft", "reviewing", "cancelled"},
    "creating_draft": {"draft_created", "needs_attention", "failed"},
    "draft_created": {"creating_draft", "published"},
    "needs_attention": {"planning", "researching", "writing", "reviewing", "preparing_assets", "creating_draft", "cancelled"},
    "failed": {"planning", "researching", "writing", "reviewing", "preparing_assets", "creating_draft", "cancelled"},
    "published": set(),
    "cancelled": set(),
}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


@dataclass
class ProductTask:
    task_id: str
    intent: str
    status: str = "created"
    active_stage: str = "intake"
    request: dict[str, object] = field(default_factory=dict)
    plan: dict[str, object] = field(default_factory=dict)
    candidates: list[dict[str, object]] = field(default_factory=list)
    selected_paper: dict[str, object] | None = None
    approvals: list[dict[str, object]] = field(default_factory=list)
    artifacts: dict[str, object] = field(default_factory=dict)
    writing_run_id: str | None = None
    wechat: dict[str, object] = field(default_factory=lambda: {"media_id": None, "publish_id": None})
    metrics: dict[str, object] = field(default_factory=dict)
    error: dict[str, object] | None = None
    schema_version: int = TASK_SCHEMA_VERSION

    @classmethod
    def create(cls, intent: str, request: dict[str, object]) -> "ProductTask":
        now = _now()
        return cls(
            task_id=f"task-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}",
            intent=intent,
            request=request,
            metrics={"started_at": now, "updated_at": now, "llm_calls": 0, "tokens": 0},
        )

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ProductTask":
        return cls(
            task_id=str(payload["task_id"]),
            intent=str(payload["intent"]),
            status=str(payload.get("status", "created")),
            active_stage=str(payload.get("active_stage", "intake")),
            request=dict(payload.get("request", {})),  # type: ignore[arg-type]
            plan=dict(payload.get("plan", {})),  # type: ignore[arg-type]
            candidates=list(payload.get("candidates", [])),  # type: ignore[arg-type]
            selected_paper=dict(payload["selected_paper"]) if isinstance(payload.get("selected_paper"), dict) else None,
            approvals=list(payload.get("approvals", [])),  # type: ignore[arg-type]
            artifacts=dict(payload.get("artifacts", {})),  # type: ignore[arg-type]
            writing_run_id=str(payload["writing_run_id"]) if payload.get("writing_run_id") else None,
            wechat=dict(payload.get("wechat", {})),  # type: ignore[arg-type]
            metrics=dict(payload.get("metrics", {})),  # type: ignore[arg-type]
            error=dict(payload["error"]) if isinstance(payload.get("error"), dict) else None,
            schema_version=int(payload.get("schema_version", TASK_SCHEMA_VERSION)),
        )

    def transition(self, status: str, active_stage: str, *, error: dict[str, object] | None = None) -> None:
        if status == self.status:
            self.active_stage = active_stage
        elif status not in ALLOWED_TRANSITIONS.get(self.status, set()):
            raise ValueError(f"Invalid task transition: {self.status} -> {status}")
        else:
            self.status = status
            self.active_stage = active_stage
        self.error = error
        self.metrics["updated_at"] = _now()

    def approve(self, gate: str, *, artifact: str | None = None, detail: dict[str, object] | None = None) -> None:
        expected = {
            "topic": "awaiting_topic_approval",
            "publish": "awaiting_publish_approval",
        }
        if gate not in expected:
            raise ValueError(f"Unknown approval gate: {gate}")
        if self.status != expected[gate]:
            raise ValueError(f"Task is not waiting for {gate} approval.")
        self.approvals.append({"gate": gate, "approved_at": _now(), "artifact": artifact, "detail": detail or {}})
        self.metrics["updated_at"] = _now()

    def latest_approval(self, gate: str) -> dict[str, object] | None:
        return next((item for item in reversed(self.approvals) if item.get("gate") == gate), None)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ProductTaskRepository:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or DATA_DIR.parent / "workspace" / "tasks"

    def save(self, task: ProductTask) -> Path:
        path = self.path(task.task_id)
        write_json(path, task.to_dict())
        self._update_index()
        return path

    def load(self, task_id: str) -> ProductTask:
        payload = read_json(self.path(task_id), {})
        if not payload:
            raise FileNotFoundError(f"Product task not found: {task_id}")
        return ProductTask.from_dict(payload)

    def list(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        if not self.root.exists():
            return rows
        for path in sorted(self.root.glob("*/manifest.json"), reverse=True):
            payload = read_json(path, {})
            rows.append(
                {
                    "task_id": payload.get("task_id"),
                    "intent": payload.get("intent"),
                    "status": payload.get("status"),
                    "active_stage": payload.get("active_stage"),
                    "updated_at": dict(payload.get("metrics", {})).get("updated_at"),
                }
            )
        return rows

    def path(self, task_id: str) -> Path:
        return self.root / task_id / "manifest.json"

    def _update_index(self) -> None:
        write_json(self.root / "index.json", self.list())
