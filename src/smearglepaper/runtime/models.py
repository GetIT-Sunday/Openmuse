from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..cancellation import CancellationToken
from ..harness_events import HarnessEvent


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class RunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_INPUT = "waiting_input"
    RECOVERY_REQUIRED = "recovery_required"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    FAILED = "failed"
    COMPLETED = "completed"


class StepStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_INPUT = "waiting_input"
    SKIPPED = "skipped"
    FAILED = "failed"
    COMPLETED = "completed"


@dataclass
class RunRequest:
    workflow: str
    inputs: dict[str, Any] = field(default_factory=dict)
    workspace: Path | str | None = None
    dry_run: bool = True
    model: str | None = None
    resume_from: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = dataclasses.asdict(self)
        value["workspace"] = str(self.workspace) if self.workspace else None
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RunRequest:
        return cls(
            workflow=str(value.get("workflow", "")),
            inputs=dict(value.get("inputs", {})),
            workspace=value.get("workspace"),
            dry_run=bool(value.get("dry_run", True)),
            model=str(value["model"]) if value.get("model") else None,
            resume_from=str(value["resume_from"]) if value.get("resume_from") else None,
        )


@dataclass
class RunResult:
    run_id: str
    status: str
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    quality: dict[str, Any] = field(default_factory=dict)
    next_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class RunEvent:
    id: str
    run_id: str
    session_id: str
    agent_id: str
    type: str
    sequence: int
    timestamp: str
    payload: dict[str, Any] = field(default_factory=dict)
    turn_id: str = ""
    source: str = "runtime"

    @classmethod
    def create(
        cls,
        run_id: str,
        session_id: str,
        agent_id: str,
        event_type: str,
        sequence: int,
        payload: dict[str, Any] | None = None,
    ) -> RunEvent:
        return cls(
            id=uuid.uuid4().hex,
            run_id=run_id,
            session_id=session_id,
            agent_id=agent_id,
            type=event_type,
            sequence=sequence,
            timestamp=utc_now(),
            payload=payload or {},
            turn_id=f"run:{run_id}",
            source="runtime",
        )

    def to_harness_event(self) -> HarnessEvent:
        return HarnessEvent(
            type=self.type,
            session_id=self.session_id,
            turn_id=self.turn_id or f"run:{self.run_id}",
            sequence=self.sequence,
            source=self.source or "runtime",
            payload=dict(self.payload),
            timestamp=self.timestamp,
            id=self.id,
            run_id=self.run_id,
            agent_id=self.agent_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RunEvent:
        return cls(**value)


@dataclass
class ArtifactRecord:
    id: str
    type: str
    schema_version: str
    path: str
    sha256: str
    producer: str
    dependencies: list[str] = field(default_factory=list)
    status: str = "created"
    mime_type: str = "application/octet-stream"
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    input_model: str = "object"
    output_model: str = "object"
    network: bool = False
    side_effect: bool = False
    retryable: bool = False
    approval: str = "never"


@dataclass(frozen=True)
class WorkflowStep:
    id: str
    tool: str
    agent: str
    dependencies: tuple[str, ...] = ()
    max_retries: int = 0
    approval: str = "never"
    condition: Callable[[RunRequest], bool] | None = None
    input_keys: tuple[str, ...] | None = None


@dataclass(frozen=True)
class WorkflowDefinition:
    id: str
    description: str
    steps: tuple[WorkflowStep, ...]


@dataclass
class ToolOutcome:
    output: dict[str, Any] = field(default_factory=dict)
    artifact_paths: list[str] = field(default_factory=list)
    quality: dict[str, Any] = field(default_factory=dict)
    usage: Usage | None = None
    message: str = ""
    interaction: dict[str, Any] | None = None


ToolHandler = Callable[["ToolContext"], ToolOutcome]


@dataclass
class ToolContext:
    run_id: str
    request: RunRequest
    step: WorkflowStep
    outputs: dict[str, dict[str, Any]]
    run_dir: Path
    emit: Callable[[str, dict[str, Any] | None], None]
    workflow: Any
    cancellation: CancellationToken | None = None
