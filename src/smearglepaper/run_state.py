"""Unified state model for SmearglePaper TUI.

All UI components render from this single RunState to avoid state drift.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from .config import DATA_DIR
from .session import Session
from .storage import read_json


class StepStatus(str, Enum):
    WAITING = "waiting"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class RunMode(str, Enum):
    IDLE = "idle"
    CHAT = "chat"
    RUN = "run"


class RunStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class WorkflowStep:
    name: str
    label: str
    status: StepStatus = StepStatus.WAITING
    summary: str | None = None
    artifact: str | None = None

    @property
    def icon(self) -> str:
        icons = {
            StepStatus.WAITING: "·",
            StepStatus.RUNNING: "⟳",
            StepStatus.SUCCESS: "✓",
            StepStatus.FAILED: "✗",
            StepStatus.SKIPPED: "○",
        }
        return icons[self.status]

    @property
    def status_label(self) -> str:
        return self.status.value.capitalize()

    @property
    def status_color(self) -> str:
        colors = {
            StepStatus.WAITING: "muted",
            StepStatus.RUNNING: "warning",
            StepStatus.SUCCESS: "success",
            StepStatus.FAILED: "error",
            StepStatus.SKIPPED: "muted",
        }
        return colors[self.status]


@dataclass
class ArtifactInfo:
    name: str
    path: str
    artifact_type: str = "file"  # "file" or "folder"
    size: str | None = None
    status: str | None = None  # "created", "pending", "updated"

    @property
    def icon(self) -> str:
        if self.artifact_type == "folder":
            return "📁"
        return "📄"


@dataclass
class RunStats:
    papers_collected: int = 0
    papers_selected: int = 0
    parsed: int = 0
    tokens: int = 0
    cost: float | None = None

    def __str__(self) -> str:
        parts = []
        if self.papers_collected:
            parts.append(f"Papers: {self.papers_collected}")
        if self.papers_selected:
            parts.append(f"Selected: {self.papers_selected}")
        if self.parsed:
            parts.append(f"Parsed: {self.parsed}")
        return ", ".join(parts) or "No data"


@dataclass
class ModelInfo:
    provider: str = ""
    name: str = ""
    context_used: str = ""


@dataclass
class ProgressInfo:
    current: int = 0
    total: int = 0

    @property
    def percent(self) -> int:
        if self.total == 0:
            return 0
        return int(self.current / self.total * 100)

    @property
    def bar(self) -> str:
        filled = int(self.percent / 5)
        empty = 20 - filled
        return f"{'█' * filled}{'░' * empty} {self.percent}%"


@dataclass
class RunState:
    """Unified state for the TUI. All components render from this."""
    id: str = ""
    title: str = ""
    mode: RunMode = RunMode.IDLE
    status: RunStatus = RunStatus.IDLE
    current_step: str = ""
    current_task: str = ""
    progress: ProgressInfo | None = None
    steps: list[WorkflowStep] = field(default_factory=list)
    artifacts: list[ArtifactInfo] = field(default_factory=list)
    stats: RunStats = field(default_factory=RunStats)
    model: ModelInfo | None = None
    quality: dict[str, object] = field(default_factory=dict)
    providers: dict[str, str] = field(default_factory=dict)
    workspace: str = ""
    active_agent: str = "main"

    # Timestamps
    started_at: str = ""
    ended_at: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:12]

    def start(self, title: str = "") -> None:
        self.mode = RunMode.RUN
        self.status = RunStatus.RUNNING
        self.started_at = datetime.now(timezone.utc).isoformat()
        if title:
            self.title = title

    def finish(self, success: bool = True) -> None:
        self.status = RunStatus.SUCCESS if success else RunStatus.FAILED
        self.ended_at = datetime.now(timezone.utc).isoformat()
        if not success:
            for step in self.steps:
                if step.status == StepStatus.RUNNING:
                    step.status = StepStatus.FAILED
        self.mode = RunMode.IDLE

    def set_step(self, name: str, status: StepStatus, summary: str | None = None) -> None:
        for step in self.steps:
            if step.name == name:
                step.status = status
                step.summary = summary
                if status == StepStatus.RUNNING:
                    self.current_step = name
                break

    def add_artifact(self, artifact: ArtifactInfo) -> None:
        self.artifacts.append(artifact)

    def update_stats(self, **kwargs: object) -> None:
        for k, v in kwargs.items():
            if hasattr(self.stats, k):
                setattr(self.stats, k, v)

    @property
    def status_text(self) -> str:
        if self.status == RunStatus.RUNNING and self.current_step:
            running = [s for s in self.steps if s.status == StepStatus.RUNNING]
            if running and self.progress:
                return f"{running[0].label} {self.progress.current}/{self.progress.total}"
            return f"Running: {self.current_step}"
        if self.status == RunStatus.SUCCESS:
            return "Done"
        if self.status == RunStatus.FAILED:
            return "Failed"
        return "Ready"

    @property
    def status_color(self) -> str:
        colors = {
            RunStatus.IDLE: "success",
            RunStatus.RUNNING: "warning",
            RunStatus.SUCCESS: "success",
            RunStatus.FAILED: "error",
        }
        return colors.get(self.status, "muted")


# ── Artifact scanning ───────────────────────────────────────────────────

def scan_artifacts() -> list[ArtifactInfo]:
    """Scan data directory for existing artifacts."""
    artifacts: list[ArtifactInfo] = []
    dirs_to_scan = [
        ("papers", "data/papers/"),
        ("ranked", "data/ranked/"),
        ("parsed", "data/parsed/"),
        ("articles", "data/articles/"),
        ("reviews", "data/reviews/"),
        ("blogs", "data/blogs/"),
        ("github", "data/github/"),
        ("runs", "data/runs/"),
    ]
    for name, rel_path in dirs_to_scan:
        dir_path = DATA_DIR / name
        if dir_path.exists():
            files = list(dir_path.glob("*"))
            count = len(files)
            if count > 0:
                artifacts.append(ArtifactInfo(
                    name=f"{name}/",
                    path=str(dir_path),
                    artifact_type="folder",
                    size=f"{count} items",
                ))
    # Check for latest.json files
    for subdir in ["papers", "ranked", "blogs", "github"]:
        latest = DATA_DIR / subdir / "latest.json"
        if latest.exists():
            size_kb = latest.stat().st_size / 1024
            artifacts.append(ArtifactInfo(
                name=f"{subdir}/latest.json",
                path=str(latest),
                artifact_type="file",
                size=f"{size_kb:.1f} KB",
            ))
    return artifacts


def load_model_info() -> ModelInfo:
    """Load model info from environment."""
    from .config import env
    provider = "Anthropic" if env("ANTHROPIC_API_KEY") else "OpenAI" if env("OPENAI_API_KEY") else "None"
    if provider == "Anthropic":
        model = env("ANTHROPIC_MODEL", "unknown")
    else:
        model = env("OPENAI_MODEL", "unknown")
    return ModelInfo(provider=provider, name=model)


def default_workflow_steps() -> list[WorkflowStep]:
    return [
        WorkflowStep("collect-papers", "Collect Papers"),
        WorkflowStep("rank-papers", "Rank Papers"),
        WorkflowStep("ingest-paper", "Ingest Paper"),
        WorkflowStep("generate-article", "Generate Article"),
        WorkflowStep("review-article", "Review Article"),
        WorkflowStep("create-draft", "Publish WeChat"),
    ]


def demo_run_state() -> RunState:
    """Return a pre-filled RunState for Demo Run Mode."""
    steps = [
        WorkflowStep("collect-arxiv", "Collect Papers", StepStatus.SUCCESS, "20 papers collected", "arxiv-2026-06-08.json"),
        WorkflowStep("rank-papers", "Rank Papers", StepStatus.SUCCESS, "selected top 8", "ranked/latest.json"),
        WorkflowStep("ingest-paper", "Ingest Papers", StepStatus.RUNNING, "parsing paper 3/8"),
        WorkflowStep("generate-article", "Generate Article", StepStatus.WAITING),
        WorkflowStep("review-article", "Review Article", StepStatus.WAITING),
        WorkflowStep("publish-wechat", "Publish WeChat", StepStatus.WAITING),
    ]

    artifacts = [
        ArtifactInfo("arxiv-2026-06-08.json", "artifacts/arxiv-2026-06-08.json", "file", "12.4 KB", "created"),
        ArtifactInfo("ranked/latest.json", "artifacts/ranked/latest.json", "file", "3.2 KB", "created"),
        ArtifactInfo("ingest/", "artifacts/ingest/", "folder", "3 / 8", "updated"),
        ArtifactInfo("drafts/latest.md", "artifacts/drafts/latest.md", "file", "pending", "pending"),
    ]

    stats = RunStats(papers_collected=20, papers_selected=8, parsed=3, tokens=33421, cost=0.87)
    model = ModelInfo(provider="Anthropic", name="mimo-v2.5-pro", context_used="32k used")

    rs = RunState(
        id="multimodal-agents-2026-06-08",
        title="multimodal-agents",
        mode=RunMode.RUN,
        status=RunStatus.RUNNING,
        current_task="Ingest paper content",
        current_step="ingest-paper",
        steps=steps,
        artifacts=artifacts,
        stats=stats,
        model=model,
    )
    rs.progress = ProgressInfo(current=3, total=8)
    return rs
