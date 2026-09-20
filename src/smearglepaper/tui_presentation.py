"""Pure, user-facing projections of durable Runtime data."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PresentationArtifact:
    """An artifact rendered in the compact TUI presentation."""

    name: str
    path: str
    producer: str
    artifact_type: str
    status: str


@dataclass(frozen=True)
class PresentationAction:
    id: str
    label: str
    enabled: bool = True
    hint: str = ""


@dataclass(frozen=True)
class RunPresentation:
    """Stable, human-oriented projection of a Runtime manifest."""

    workflow: str
    status: str
    headline: str
    subject: str
    progress_current: int
    progress_total: int
    artifacts: tuple[PresentationArtifact, ...]
    quality: dict[str, Any]
    next_action: str
    error: str = ""
    phase: str = "准备开始"
    quality_verdict: str = ""
    issues: tuple[str, ...] = ()
    actions: tuple[PresentationAction, ...] = ()
    interaction: dict[str, Any] | None = None

    @property
    def progress_text(self) -> str:
        return f"{self.progress_current}/{self.progress_total} 步"

    @property
    def artifact_count(self) -> int:
        return len(self.artifacts)

    @property
    def summary_text(self) -> str:
        lines = [f"OpenMuse：{self.headline}"]
        if self.subject:
            lines.append(self.subject)
        if self.status in {"failed", "cancelled"}:
            lines.append(f"进度 {self.progress_text}，已保留 {self.artifact_count} 个可用产物。")
        else:
            lines.append(f"已完成 {self.progress_text}，生成 {self.artifact_count} 个可用产物。")
        if self.quality_verdict:
            lines.append(f"质量：{self.quality_verdict}")
        elif self.quality:
            scores = []
            for label, key in (("技术", "technical_score"), ("公众号", "wechat_score")):
                value = self.quality.get(key)
                if value is not None:
                    scores.append(f"{label} {value}")
            if scores:
                lines.append("质量：" + " · ".join(scores))
        if self.artifacts:
            lines.append("文章和研究材料已自动保存。")
        if self.next_action:
            lines.append(f"下一步：{self.next_action}")
        return "\n".join(lines)


def build_run_presentation(
    manifest: dict[str, Any],
    checkpoints: dict[str, dict[str, Any]] | None = None,
) -> RunPresentation:
    """Build a deterministic presentation without making model or network calls."""
    workflow = str(manifest.get("workflow", ""))
    status = str(manifest.get("status", "pending"))
    steps = list(manifest.get("steps", []))
    completed = sum(1 for step in steps if step.get("status") in {"completed", "skipped"})
    checkpoints = checkpoints or {}
    subject = _subject_from_checkpoints(checkpoints)
    request = dict(manifest.get("request", {}))
    inputs = dict(request.get("inputs", {}))
    if not subject:
        subject = str(inputs.get("topic") or inputs.get("query") or "").strip()

    quality = dict(manifest.get("quality", {}))
    interaction = manifest.get("interaction") if isinstance(manifest.get("interaction"), dict) else None
    error = _failure_message(manifest, steps)
    headline = _headline(workflow, status, error)
    artifacts = _presentation_artifacts(manifest.get("artifacts", []))
    return RunPresentation(
        workflow=workflow,
        status=status,
        headline=headline,
        subject=subject,
        progress_current=completed,
        progress_total=len(steps),
        artifacts=artifacts,
        quality=quality,
        next_action=_next_action(workflow, status),
        error=error,
        phase=_phase(steps, status),
        quality_verdict=_quality_verdict(quality),
        issues=_quality_issues(quality),
        actions=_actions(workflow, status, quality),
        interaction=dict(interaction) if interaction else None,
    )


def compact_artifact_name(name: str, max_length: int = 26) -> str:
    """Keep compact result surfaces stable while preserving file extensions."""
    if len(name) <= max_length:
        return name
    suffix = Path(name).suffix
    stem_budget = max(8, max_length - len(suffix) - 1)
    return f"{Path(name).stem[:stem_budget]}…{suffix}"


def _subject_from_checkpoints(checkpoints: dict[str, dict[str, Any]]) -> str:
    for step_id in ("write", "ingest", "select"):
        checkpoint = checkpoints.get(step_id, {})
        output = checkpoint.get("output", {}) if isinstance(checkpoint, dict) else {}
        paper = output.get("paper") if isinstance(output, dict) else None
        if isinstance(paper, dict) and paper.get("title"):
            return str(paper["title"])
        final = output.get("final") if isinstance(output, dict) else None
        if isinstance(final, dict) and final.get("title"):
            return str(final["title"])
    return ""


def _failure_message(manifest: dict[str, Any], steps: list[dict[str, Any]]) -> str:
    for step in steps:
        if step.get("status") != "failed":
            continue
        error = step.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if error:
            return str(error)
    return str(manifest.get("error", ""))


def _headline(workflow: str, status: str, error: str) -> str:
    if status in {"failed", "cancelled"}:
        return "任务未完成：" + friendly_error(error)
    if status == "waiting_input":
        return "请选择一篇论文继续"
    if status in {"pending", "running", "waiting_approval", "cancelling"}:
        return "正在处理你的研究任务"
    if workflow == "paper-research":
        return "论文证据已准备好"
    if workflow == "paper-to-article":
        return "文章已生成，等待你审阅"
    if workflow == "paper-to-wechat":
        return "公众号发布包已准备好"
    if workflow == "daily-digest":
        return "研究日报已生成"
    if workflow == "publish-existing":
        return "微信草稿已创建"
    return "任务已完成"


def friendly_error(error: str) -> str:
    lower = error.lower()
    if "429" in lower or "rate limit" in lower:
        return "请求过于频繁，请稍后重试"
    if "401" in lower or "api key" in lower or "unauthorized" in lower:
        return "模型服务认证失败，请检查 API Key"
    if "timeout" in lower or "timed out" in lower:
        return "网络或模型响应超时，可以直接重试"
    if "permissionerror" in lower or "operation not permitted" in lower or "permission denied" in lower:
        return "无法写入本地工作区，请检查目录权限"
    if "pdf" in lower:
        return "论文 PDF 下载或解析失败，可以重试或更换论文"
    scrubbed = re.sub(r"(?:/[\w.+@ -]+){2,}", "本地文件", error)
    return scrubbed or "请查看失败原因后重试"


def _next_action(workflow: str, status: str) -> str:
    if status == "waiting_input":
        return "选择一篇候选论文"
    if status in {"pending", "running", "waiting_approval", "cancelling"}:
        return "等待当前运行完成"
    if status in {"failed", "cancelled"}:
        return "检查失败步骤后重试"
    if workflow == "paper-research":
        return "输入“把它写成中文深度解读”继续生成文章"
    if workflow == "paper-to-article":
        return "继续修改，或打开手机预览"
    if workflow == "paper-to-wechat":
        return "打开手机预览，确认后再创建草稿"
    if workflow == "daily-digest":
        return "查看日报产物并继续追问"
    if workflow == "publish-existing":
        return "前往微信公众号后台检查草稿"
    return "查看生成的产物"


def _phase(steps: list[dict[str, Any]], status: str) -> str:
    if status == "waiting_input":
        return "选择材料"
    active = next(
        (str(step.get("id", "")) for step in steps if step.get("status") in {"running", "retrying", "waiting_approval", "waiting_input"}),
        "",
    )
    if not active and status == "completed":
        return "文章已完成"
    if active in {"collect", "rank", "choose", "select"}:
        return "选择材料"
    if active == "ingest":
        return "理解论文"
    if active == "write":
        return "撰写文章"
    if active == "review":
        return "质量审阅"
    if active in {"package", "publish"}:
        return "准备预览"
    return "准备开始"


def _quality_verdict(quality: dict[str, Any]) -> str:
    if not quality:
        return ""
    if quality.get("publish_ready"):
        return "可以预览"
    if quality.get("content_ready"):
        return "建议修改"
    return "尚不能发布"


def _quality_issues(quality: dict[str, Any]) -> tuple[str, ...]:
    raw = quality.get("issues", [])
    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw if str(item).strip())[:3]


def _actions(workflow: str, status: str, quality: dict[str, Any]) -> tuple[PresentationAction, ...]:
    if status == "waiting_input":
        return (PresentationAction("select", "选择论文"),)
    if status in {"failed", "cancelled"}:
        return (PresentationAction("retry", "重试"), PresentationAction("new", "新建任务"))
    if status != "completed":
        return ()
    if workflow == "paper-research":
        return (PresentationAction("write", "写成文章"), PresentationAction("new", "更换选题"))
    if workflow in {"paper-to-article", "paper-to-wechat"}:
        publish_ready = bool(quality.get("publish_ready"))
        return (
            PresentationAction("revise", "继续修改"),
            PresentationAction("preview", "手机预览"),
            PresentationAction("open", "打开文章"),
            PresentationAction("draft", "创建微信草稿", publish_ready, "文章通过质量检查后可用"),
        )
    return (PresentationAction("open", "查看结果"), PresentationAction("new", "新建任务"))


def _presentation_artifacts(raw_artifacts: object) -> tuple[PresentationArtifact, ...]:
    if not isinstance(raw_artifacts, list):
        return ()
    chosen: dict[str, PresentationArtifact] = {}
    for raw in raw_artifacts:
        if not isinstance(raw, dict):
            continue
        if raw.get("status") == "stale":
            continue
        path = str(raw.get("path", ""))
        producer = str(raw.get("producer", ""))
        name = Path(path).name
        if not name or name == f"{producer}-{producer}.json":
            continue
        item = PresentationArtifact(
            name=name,
            path=path,
            producer=producer,
            artifact_type=str(raw.get("type", raw.get("mime_type", "file"))),
            status=str(raw.get("status", "created")),
        )
        chosen.setdefault(name, item)
    ordered = sorted(chosen.values(), key=lambda item: (_artifact_priority(item.name), item.name))
    return tuple(ordered)


def _artifact_priority(name: str) -> tuple[int, int]:
    lower = name.lower()
    if lower.endswith(".html"):
        return (0, len(name))
    if lower.endswith(".md"):
        return (1, len(name))
    if "article" in lower or "publish" in lower or "draft" in lower:
        return (2, len(name))
    if "ingest" in lower or lower.endswith(".pdf"):
        return (3, len(name))
    if "select" in lower or "rank" in lower:
        return (4, len(name))
    return (5, len(name))
