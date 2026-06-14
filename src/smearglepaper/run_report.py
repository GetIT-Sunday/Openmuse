from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import DATA_DIR
from .storage import ensure_parent, write_json


def generate_run_id() -> str:
    return "run_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S")


@dataclass
class StepRecord:
    name: str
    status: str = "pending"  # pending | success | failed | skipped | failed_quality_gate
    reason: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_seconds: float = 0
    artifacts: list[str] = field(default_factory=list)
    error: dict[str, object] | None = None
    quality: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "name": self.name,
            "status": self.status,
            "reason": self.reason,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": round(self.duration_seconds, 2),
            "artifacts": self.artifacts,
            "error": self.error,
        }
        if self.quality:
            d["quality"] = self.quality
        return d


@dataclass
class RunReport:
    run_id: str
    workflow: str = ""
    query: str = ""
    resume: bool = False
    dry_run: bool = True
    status: str = "running"  # running | success | failed | partial_success
    started_at: str = ""
    ended_at: str = ""
    duration_seconds: float = 0
    steps: list[StepRecord] = field(default_factory=list)
    selected_paper: dict[str, object] | None = None
    article_quality: dict[str, object] | None = None
    final_article_json: str | None = None
    wechat: dict[str, object] | None = None
    next_action: str = ""

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = dt.datetime.now(dt.timezone.utc).isoformat()
        if not self.run_id:
            self.run_id = generate_run_id()

    @property
    def run_dir(self) -> Path:
        return DATA_DIR / "runs" / self.run_id

    def start_step(self, name: str) -> StepRecord:
        step = StepRecord(
            name=name,
            status="pending",
            started_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        )
        self.steps.append(step)
        return step

    def complete_step(
        self,
        step: StepRecord,
        status: str = "success",
        reason: str | None = None,
        artifacts: list[str] | None = None,
        error: dict[str, object] | None = None,
        quality: dict[str, object] | None = None,
    ) -> None:
        step.ended_at = dt.datetime.now(dt.timezone.utc).isoformat()
        step.status = status
        step.reason = reason
        if artifacts:
            step.artifacts = artifacts
        step.error = error
        step.quality = quality
        if step.started_at:
            start = dt.datetime.fromisoformat(step.started_at)
            end = dt.datetime.fromisoformat(step.ended_at)
            step.duration_seconds = (end - start).total_seconds()

    def finish(self, status: str = "success", next_action: str = "") -> None:
        self.status = status
        self.ended_at = dt.datetime.now(dt.timezone.utc).isoformat()
        self.next_action = next_action
        if self.started_at:
            start = dt.datetime.fromisoformat(self.started_at)
            end = dt.datetime.fromisoformat(self.ended_at)
            self.duration_seconds = (end - start).total_seconds()

    def fail_active_step(self, exc: Exception, retryable: bool = True) -> None:
        for step in reversed(self.steps):
            if step.status == "pending" and step.ended_at is None:
                self.complete_step(
                    step,
                    "failed",
                    error={
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "retryable": retryable,
                    },
                )
                break

    def save(self) -> Path:
        ensure_parent(self.run_dir / "run_report.json")
        # Save JSON
        write_json(self.run_dir / "run_report.json", self.to_dict())
        # Save Markdown
        md = self.to_markdown()
        (self.run_dir / "run_report.md").write_text(md, encoding="utf-8")
        return self.run_dir

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "workflow": self.workflow,
            "query": self.query,
            "resume": self.resume,
            "dry_run": self.dry_run,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": round(self.duration_seconds, 2),
            "steps": [s.to_dict() for s in self.steps],
            "selected_paper": self.selected_paper,
            "article_quality": self.article_quality,
            "final_article_json": self.final_article_json,
            "wechat": self.wechat,
            "next_action": self.next_action,
        }

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append("# SmearglePaper Run Report\n")
        lines.append("## Run Summary\n")
        lines.append(f"- **Run ID:** {self.run_id}")
        lines.append(f"- **Workflow:** {self.workflow}")
        lines.append(f"- **Query:** {self.query}")
        lines.append(f"- **Resume:** {self.resume}")
        lines.append(f"- **Dry Run:** {self.dry_run}")
        lines.append(f"- **Status:** {self.status}")
        lines.append(f"- **Duration:** {self.duration_seconds:.0f}s")
        lines.append("")

        lines.append("## Step Timeline\n")
        lines.append("| Step | Status | Reason | Duration | Artifacts |")
        lines.append("|---|---|---|---:|---|")
        for step in self.steps:
            status_display = step.status
            if step.status == "failed_quality_gate":
                status_display = "failed_quality_gate"
            reason = step.reason or "-"
            artifacts = ", ".join(step.artifacts) if step.artifacts else "-"
            lines.append(f"| {step.name} | {status_display} | {reason} | {step.duration_seconds:.0f}s | {artifacts} |")
        lines.append("")

        if self.selected_paper:
            lines.append("## Selected Paper\n")
            lines.append(f"- **Title:** {self.selected_paper.get('title', '-')}")
            lines.append(f"- **URL:** {self.selected_paper.get('url', '-')}")
            if self.selected_paper.get("reason"):
                lines.append(f"- **Reason:** {self.selected_paper['reason']}")
            lines.append("")

        if self.article_quality:
            lines.append("## Article Quality\n")
            lines.append(f"- **Final Score:** {self.article_quality.get('final_score', '-')}")
            lines.append(f"- **Threshold:** {self.article_quality.get('threshold', '-')}")
            lines.append(f"- **Pass:** {self.article_quality.get('pass', '-')}")
            lines.append("")

        if self.final_article_json:
            lines.append("## Final Article\n")
            lines.append(f"- **JSON:** {self.final_article_json}")
            lines.append("")

        if self.wechat:
            lines.append("## WeChat\n")
            lines.append(f"- **Draft Created:** {self.wechat.get('draft_created', False)}")
            if self.wechat.get("draft_id"):
                lines.append(f"- **Draft ID:** {self.wechat['draft_id']}")
            lines.append(f"- **Published:** {self.wechat.get('published', False)}")
            lines.append("")

        if self.next_action:
            lines.append("## Next Action\n")
            lines.append(self.next_action)
            lines.append("")

        # Failed steps detail
        failed = [s for s in self.steps if s.status in ("failed", "failed_quality_gate")]
        if failed:
            lines.append("## Failed Steps\n")
            for step in failed:
                lines.append(f"### {step.name}\n")
                lines.append(f"- **Status:** {step.status}")
                if step.reason:
                    lines.append(f"- **Reason:** {step.reason}")
                if step.error:
                    lines.append(f"- **Error:** {step.error.get('message', '')}")
                lines.append("")

        return "\n".join(lines)
