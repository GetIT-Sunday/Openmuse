"""Legacy TUI workflow helpers backed by the durable Runtime."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .run_state import (
    ArtifactInfo,
    RunMode,
    RunState,
    RunStatus,
    StepStatus,
    WorkflowStep,
    load_model_info,
)

# ── Workflow Definitions ─────────────────────────────────────────────────

WORKFLOWS: dict[str, dict[str, Any]] = {
    "arxiv-paper-flow": {
        "id": "arxiv-paper-flow",
        "label": "arxiv-paper-flow",
        "steps": [
            {"id": "collect-arxiv", "label": "Collect Papers"},
            {"id": "rank-papers", "label": "Rank Papers"},
            {"id": "ingest-paper", "label": "Ingest Papers"},
            {"id": "generate-article", "label": "Generate Article"},
            {"id": "review-article", "label": "Review Article"},
            {"id": "publish-wechat", "label": "Publish WeChat"},
        ],
    },
    "github-trending-flow": {
        "id": "github-trending-flow",
        "label": "github-trending-flow",
        "steps": [
            {"id": "collect-github", "label": "Collect GitHub Repos"},
            {"id": "summarize", "label": "Summarize Trending"},
            {"id": "notify", "label": "Send Notification"},
        ],
    },
    "wechat-publish-flow": {
        "id": "wechat-publish-flow",
        "label": "wechat-publish-flow",
        "steps": [
            {"id": "draft-article", "label": "Draft Article"},
            {"id": "review-article", "label": "Review Article"},
            {"id": "publish-wechat", "label": "Publish WeChat"},
        ],
    },
    "paper-writing-agent-flow": {
        "id": "paper-writing-agent-flow",
        "label": "paper-writing-agent-flow",
        "steps": [
            {"id": "prepare-evidence", "label": "Prepare Evidence"},
            {"id": "plan-article", "label": "Plan Argument"},
            {"id": "generate-article", "label": "Draft Article"},
            {"id": "review-article", "label": "Evidence Review"},
            {"id": "improve-article", "label": "Revise Article"},
            {"id": "finalize-article", "label": "Finalize Article"},
        ],
    },
}


# ── Intent Parsing ───────────────────────────────────────────────────────

def parse_intent(message: str) -> tuple[str, str]:
    """Parse user message to determine workflow and topic.

    Returns (workflow_id, topic).
    """
    lower = message.lower()

    # Paper-related
    writing_agent_keywords = ["写作agent", "writing agent", "论文写作", "自动修订", "证据审稿"]
    if any(k in lower for k in writing_agent_keywords):
        topic = _extract_topic(message)
        return "paper-writing-agent-flow", topic

    paper_keywords = ["论文", "paper", "arxiv", "收集", "搜索", "检索"]
    if any(k in lower for k in paper_keywords):
        topic = _extract_topic(message)
        return "arxiv-paper-flow", topic

    # GitHub-related
    github_keywords = ["github", "开源", "repo", "trending", "仓库"]
    if any(k in lower for k in github_keywords):
        topic = _extract_topic(message)
        return "github-trending-flow", topic

    # WeChat-related
    wechat_keywords = ["微信", "公众号", "草稿", "发布", "wechat"]
    if any(k in lower for k in wechat_keywords):
        return "wechat-publish-flow", ""

    return "", ""


def _extract_topic(message: str) -> str:
    """Extract research topic from message."""
    topic_keywords = {
        "agents": ["agent", "agents", "智能体", "代理"],
        "nlp": ["nlp", "自然语言", "语言模型"],
        "multimodal": ["multimodal", "多模态", "vision", "图像"],
        "reasoning": ["reasoning", "推理", "逻辑"],
        "reinforcement_learning": ["reinforcement", "强化学习", "rl"],
        "latest_ai": ["latest", "最新", "ai", "llm", "大模型"],
    }
    lower = message.lower()
    for topic, keywords in topic_keywords.items():
        if any(k in lower for k in keywords):
            return topic
    return "agents"


# ── RunState Factory ─────────────────────────────────────────────────────

def create_run_state(workflow_id: str, topic: str, user_goal: str) -> RunState:
    """Create a new RunState for a workflow."""
    wf = WORKFLOWS[workflow_id]
    steps = [
        WorkflowStep(s["id"], s["label"], StepStatus.WAITING)
        for s in wf["steps"]
    ]
    return RunState(
        id=f"{topic}-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}",
        title=f"{topic}-{workflow_id}",
        mode=RunMode.RUN,
        status=RunStatus.RUNNING,
        current_step=wf["steps"][0]["id"] if wf["steps"] else "",
        current_task=wf["steps"][0]["label"] if wf["steps"] else "",
        steps=steps,
        model=load_model_info(),
    )


# ── Runtime compatibility bridge ────────────────────────────────────────

async def execute_workflow(
    run_state: RunState,
    update_fn: Callable[[], None],
    trace_fn: Callable[[str, str, str], None],
) -> None:
    """Execute a real durable workflow while updating the legacy RunState view."""
    from pathlib import Path

    from .runtime import AgentRuntime, RunRequest

    workflow_map = {
        "arxiv-paper-flow": "paper-research",
        "paper-writing-agent-flow": "paper-to-article",
        "wechat-publish-flow": "paper-to-wechat",
        "github-trending-flow": "daily-digest",
    }
    legacy_id = next((key for key in workflow_map if key in run_state.title), "arxiv-paper-flow")
    runtime = AgentRuntime()

    def on_event(event: object) -> None:
        event_type = str(getattr(event, "type", ""))
        payload = dict(getattr(event, "payload", {}))
        step_id = str(payload.get("step", ""))
        if event_type == "step.started":
            run_state.current_step = step_id
            run_state.current_task = step_id
            run_state.set_step(step_id, StepStatus.RUNNING)
            trace_fn("step", step_id, f"Running {step_id}")
        elif event_type == "step.completed":
            run_state.set_step(step_id, StepStatus.SUCCESS)
        elif event_type == "artifact.created":
            path = str(payload.get("path", ""))
            run_state.artifacts.append(ArtifactInfo(Path(path).name, path))
            trace_fn("artifact", "", path)
        elif event_type in {"step.failed", "run.failed"}:
            run_state.status = RunStatus.FAILED
            trace_fn("error", step_id, str(payload.get("error", "Runtime failed")))
        update_fn()

    result = await asyncio.to_thread(
        runtime.run,
        RunRequest(workflow_map[legacy_id], {"topic": "agents"}),
        event_handler=on_event,
    )
    run_state.status = RunStatus.SUCCESS if result.status == "completed" else RunStatus.FAILED
    run_state.current_step = ""
    run_state.current_task = ""
    update_fn()
    trace_fn("agent", "Workflow Complete", f"{run_state.title}: {result.status}")
