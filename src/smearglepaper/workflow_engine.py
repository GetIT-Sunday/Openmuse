"""Workflow definitions, intent parsing, and mock executor."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Callable

from .run_state import (
    ArtifactInfo,
    ProgressInfo,
    RunMode,
    RunState,
    RunStatus,
    StepStatus,
    WorkflowStep,
    load_model_info,
)
from .trace_message import TraceMessage


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
}


# ── Intent Parsing ───────────────────────────────────────────────────────

def parse_intent(message: str) -> tuple[str, str]:
    """Parse user message to determine workflow and topic.

    Returns (workflow_id, topic).
    """
    lower = message.lower()

    # Paper-related
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


# ── Mock Executor ────────────────────────────────────────────────────────

async def execute_workflow(
    run_state: RunState,
    update_fn: Callable[[], None],
    trace_fn: Callable[[str, str, str], None],
) -> None:
    """Execute a workflow with mock data, updating state and trace in real-time."""
    wf_id = run_state.title.split("-")[0] if "-" in run_state.title else "arxiv-paper-flow"
    # Try to find workflow from title
    for key in WORKFLOWS:
        if key in run_state.title:
            wf_id = key
            break

    today = datetime.now().strftime("%Y-%m-%d")
    total_steps = len(run_state.steps)

    for i, step in enumerate(run_state.steps):
        # Start step
        run_state.current_step = step.name
        run_state.current_task = step.label
        step.status = StepStatus.RUNNING
        update_fn()

        trace_fn("step", step.label, f"⟳ 正在执行 {step.label}...")

        await asyncio.sleep(1.0)

        # Step-specific mock logic
        if step.name == "collect-arxiv":
            run_state.stats.papers_collected = 20
            run_state.artifacts.append(ArtifactInfo(
                name=f"arxiv-{today}.json",
                path=f"artifacts/papers/arxiv-{today}.json",
                artifact_type="file",
                size="12.4 KB",
                status="created",
            ))
            trace_fn("tool", step.label, f"✓ 检索到 20 篇相关论文\n  output: artifacts/papers/arxiv-{today}.json")

        elif step.name == "rank-papers":
            run_state.stats.papers_selected = 8
            run_state.artifacts.append(ArtifactInfo(
                name="ranked/latest.json",
                path="artifacts/ranked/latest.json",
                artifact_type="file",
                size="3.2 KB",
                status="created",
            ))
            trace_fn("tool", step.label, f"✓ 已选出 Top 8 论文\n  output: artifacts/ranked/latest.json")

        elif step.name == "ingest-paper":
            total_papers = 8
            run_state.progress = ProgressInfo(current=0, total=total_papers)
            for j in range(total_papers):
                run_state.progress.current = j + 1
                run_state.stats.parsed = j + 1
                update_fn()

                paper_id = f"2305.{12345 + j}v{j + 1}"
                trace_fn("step", step.label, f"⟳ 解析论文 {j + 1}/{total_papers}\n  当前: {paper_id}.pdf")

                if j == 2:
                    run_state.artifacts.append(ArtifactInfo(
                        name=f"ingest/{paper_id}.md",
                        path=f"artifacts/ingest/{paper_id}.md",
                        artifact_type="file",
                        size="8.7 KB",
                        status="created",
                    ))
                    trace_fn("artifact", "", f"📄 生成中间产物: artifacts/ingest/{paper_id}.md")

                await asyncio.sleep(0.5)

            trace_fn("tool", step.label, f"✓ 已解析全部 {total_papers} 篇论文")

        elif step.name == "generate-article":
            run_state.artifacts.append(ArtifactInfo(
                name="articles/latest.md",
                path="artifacts/articles/latest.md",
                artifact_type="file",
                size="15.2 KB",
                status="created",
            ))
            trace_fn("tool", step.label, "✓ 已生成中文解读文章\n  output: artifacts/articles/latest.md")

        elif step.name == "review-article":
            run_state.stats.tokens = 33421
            run_state.stats.cost = 0.87
            trace_fn("tool", step.label, "✓ 文章质量审查通过\n  score: 85/100")

        elif step.name == "publish-wechat":
            run_state.artifacts.append(ArtifactInfo(
                name="drafts/latest.md",
                path="artifacts/drafts/latest.md",
                artifact_type="file",
                size="12.1 KB",
                status="created",
            ))
            trace_fn("tool", step.label, "✓ 微信草稿已创建\n  output: artifacts/drafts/latest.md")

        else:
            trace_fn("tool", step.label, f"✓ {step.label} 完成")

        # Complete step
        step.status = StepStatus.SUCCESS
        step.summary = f"Done"
        update_fn()

    # Finish
    run_state.status = RunStatus.SUCCESS
    run_state.current_step = ""
    run_state.current_task = ""
    update_fn()
    trace_fn("agent", "Workflow Complete", f"✅ {run_state.title} 全部完成")
