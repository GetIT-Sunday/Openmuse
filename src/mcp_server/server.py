from __future__ import annotations

from pathlib import Path

from smearglepaper.agents import check_agents
from smearglepaper.config import DATA_DIR
from smearglepaper.editor import improve_article_file
from smearglepaper.models import PaperMeta
from smearglepaper.runtime import AgentRuntime, RunRequest
from smearglepaper.scout import run_scout_review
from smearglepaper.storage import read_json
from smearglepaper.workflow import SmearglePaperWorkflow

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - exercised by preflight instead
    raise SystemExit("Install MCP support first: python -m pip install -r requirements.txt") from exc

mcp = FastMCP("smearglepaper")


@mcp.tool()
def run_workflow(
    workflow: str = "paper-to-article",
    paper_url: str = "",
    paper_id: str = "",
    topic: str = "agents",
    request: str = "",
    workspace: str = "",
    real: bool = False,
) -> dict[str, object]:
    """Start a durable workflow and immediately return its run_id. Real external writes still require approval."""
    runtime = AgentRuntime(workspace or None)
    result = runtime.start(
        RunRequest(
            workflow=workflow,
            inputs={
                key: value
                for key, value in {
                    "paper_url": paper_url,
                    "paper_id": paper_id,
                    "topic": topic,
                    "request": request,
                }.items()
                if value
            },
            workspace=workspace or None,
            dry_run=not real,
        )
    )
    return result.to_dict()


@mcp.tool()
def get_run(run_id: str, workspace: str = "", include_events: bool = True) -> dict[str, object]:
    """Get a durable run manifest and optionally its ordered event stream."""
    return AgentRuntime(workspace or None).get_run(run_id, include_events=include_events)


@mcp.tool()
def list_runs(workspace: str = "") -> list[dict[str, object]]:
    """List durable workflow runs."""
    return AgentRuntime(workspace or None).list_runs()


@mcp.tool()
def resume_run(run_id: str, workspace: str = "") -> dict[str, object]:
    """Resume a cancelled or failed durable run from verified artifacts."""
    return AgentRuntime(workspace or None).resume(run_id).to_dict()


@mcp.tool()
def cancel_run(run_id: str, workspace: str = "") -> dict[str, object]:
    """Cooperatively cancel a durable run before its next step."""
    return AgentRuntime(workspace or None).cancel(run_id).to_dict()


@mcp.tool()
def resolve_approval(run_id: str, approval_id: str, approve: bool, workspace: str = "") -> dict[str, object]:
    """Approve or reject a pending external side effect."""
    runtime = AgentRuntime(workspace or None)
    result = runtime.resolve_approval(run_id, approval_id, approve)
    return runtime.resume(run_id).to_dict() if approve else result.to_dict()


@mcp.tool()
def list_artifacts(run_id: str, workspace: str = "") -> list[dict[str, object]]:
    """List hash-verified artifacts belonging to a durable run."""
    return AgentRuntime(workspace or None).list_artifacts(run_id)


@mcp.tool()
def preflight() -> dict[str, object]:
    """Check local dependencies, LLM settings, and WeChat settings."""
    return SmearglePaperWorkflow().preflight()


@mcp.tool()
def collect_papers(query: str = "", topic: str = "latest_ai", days: int = 7, max_results: int = 20) -> dict[str, object]:
    """Collect recent arXiv papers and save them to data/papers/latest.json."""
    workflow = SmearglePaperWorkflow()
    papers = workflow.collect(topic or None, query or None, days, ["arxiv"], max_results)
    return {"count": len(papers), "output": str(DATA_DIR / "papers" / "latest.json"), "papers": papers}


@mcp.tool()
def collect_blogs(query: str = "", topic: str = "agents", days: int = 30, max_results: int = 30) -> dict[str, object]:
    """Collect recent blog posts from built-in RSS/Atom feeds and save them to data/blogs/latest.json."""
    result = SmearglePaperWorkflow().collect_blogs(topic or None, query or None, days, max_results)
    result["output"] = str(DATA_DIR / "blogs" / "latest.json")
    return result


@mcp.tool()
def scout_run(query: str = "", topic: str = "agents", days: int = 30, max_results: int = 50, include_blogs: bool = True, from_existing: bool = False) -> dict[str, object]:
    """Run Scout Agent and write JSON/HTML artifacts for human review."""
    return run_scout_review(topic or None, query or None, days, max_results, include_blogs=include_blogs, from_existing=from_existing)


@mcp.tool()
def agent_run(
    query: str = "",
    topic: str = "agents",
    paper_url: str = "",
    days: int = 30,
    top_k: int = 5,
    max_results: int = 50,
    collect_blogs_enabled: bool = True,
    improve_enabled: bool = True,
    create_draft_enabled: bool = True,
    dry_run: bool = True,
    resume: bool = False,
) -> dict[str, object]:
    """Run the one-command agentic paper-to-draft workflow. Set resume=true to skip completed steps."""
    return SmearglePaperWorkflow().agent_run(
        topic or None,
        query or None,
        paper_url or None,
        days,
        top_k,
        max_results,
        collect_blogs_enabled,
        improve_enabled,
        create_draft_enabled,
        dry_run,
        resume=resume,
    )


@mcp.tool()
def agent_check(agent: str = "all") -> dict[str, object]:
    """Check readiness for each SmearglePaper agent."""
    return check_agents(agent)


@mcp.tool()
def rank_latest(query: str = "", top_k: int = 5) -> dict[str, object]:
    """Rank papers from the latest local collection."""
    workflow = SmearglePaperWorkflow()
    papers = workflow.load_papers_file(DATA_DIR / "papers" / "latest.json")
    rows = workflow.rank(papers, top_k=top_k, query=query or None)
    return {"count": len(rows), "output": str(DATA_DIR / "papers" / "ranked_latest.json"), "ranked": rows}


@mcp.tool()
def read_paper(paper_id: str) -> dict[str, object]:
    """Download and parse a paper from the latest/ranked local collection."""
    workflow = SmearglePaperWorkflow()
    return workflow.read(_find_paper(paper_id))


@mcp.tool()
def write_article(paper_id: str) -> dict[str, object]:
    """Generate Markdown, HTML, cover, and article JSON for a local paper."""
    workflow = SmearglePaperWorkflow()
    paper = _find_paper(paper_id)
    parsed_path = DATA_DIR / "parsed" / f"{paper.paper_id.replace('/', '_')}.json"
    return workflow.write_article(paper, parsed=read_json(parsed_path, {}))


@mcp.tool()
def run_writing_agent(
    paper_id: str = "",
    paper_url: str = "",
    paper_title: str = "",
    notes_path: str = "",
    article_path: str = "",
    target_audience: str = "AI方向研究生和算法岗候选人",
    style_mode: str = "balanced",
    target_score: int = 85,
    max_revisions: int = 3,
    upload_images: bool = False,
) -> dict[str, object]:
    """Run the evidence-first Paper Writing Agent with persisted planning, review, and revision stages."""
    return SmearglePaperWorkflow().run_writing_agent(
        paper_id or None,
        paper_url=paper_url or None,
        paper_title=paper_title or None,
        notes_path=Path(notes_path) if notes_path else None,
        article_path=Path(article_path) if article_path else None,
        target_audience=target_audience,
        style_mode=style_mode,
        target_score=target_score,
        max_revisions=max_revisions,
        upload_images=upload_images,
    )


@mcp.tool()
def prepare_agent_assets(article_json: str, target_score: int = 85) -> dict[str, object]:
    """Upload local images for an existing content-ready Agent article and write publish-ready outputs."""
    return SmearglePaperWorkflow().prepare_agent_assets(Path(article_json), target_score=target_score)


@mcp.tool()
def review_article(article_path: str) -> dict[str, object]:
    """Review a generated article Markdown or JSON file for publication readiness."""
    return SmearglePaperWorkflow().review_article(Path(article_path))


@mcp.tool()
def improve_article(article_path: str, output_prefix: str = "") -> dict[str, object]:
    """Improve a generated article with the configured LLM and write optimized Markdown/HTML/JSON."""
    return improve_article_file(Path(article_path), Path(output_prefix) if output_prefix else None)


@mcp.tool()
def create_draft(query: str = "", topic: str = "latest_ai", paper_url: str = "", days: int = 7, top_k: int = 5, dry_run: bool = True) -> dict[str, object]:
    """Run the full collect-rank-read-write-draft workflow."""
    return SmearglePaperWorkflow().create_topic_draft(topic or None, query or None, paper_url or None, days, top_k, dry_run=dry_run)


@mcp.tool()
def publish_article(article_json: str, draft_only: bool = True, dry_run: bool = True) -> dict[str, object]:
    """Create a WeChat draft from an existing article JSON, optionally submitting publish."""
    return SmearglePaperWorkflow().publish_existing_article(Path(article_json), real_wechat=not dry_run, publish=not draft_only)


@mcp.tool()
def update_draft(article_json: str, media_id: str, index: int = 0, dry_run: bool = True) -> dict[str, object]:
    """Update an existing WeChat draft from an article JSON."""
    return SmearglePaperWorkflow().update_existing_draft(Path(article_json), media_id=media_id, real_wechat=not dry_run, index=index)


@mcp.tool()
def collect_github_stars(language: str = "", since: str = "weekly", max_results: int = 20) -> dict[str, object]:
    """Collect trending GitHub repos and save star growth data."""
    return SmearglePaperWorkflow().collect_github_stars(language or None, since, max_results)


@mcp.tool()
def send_notification(title: str, content: str, msg_type: str = "interactive") -> dict[str, object]:
    """Send a notification to Feishu webhook."""
    return SmearglePaperWorkflow().send_notification(title, content, msg_type)


@mcp.tool()
def daily_digest(
    topic: str = "agents",
    days: int = 7,
    top_k: int = 5,
    language: str = "",
    notify: bool = False,
    resume: bool = False,
) -> dict[str, object]:
    """Collect papers, blogs, and GitHub stars in one run, optionally send Feishu notifications. Set resume=true to skip completed steps."""
    return SmearglePaperWorkflow().daily_digest(topic or None, days, top_k, language or None, notify, resume=resume)


@mcp.tool()
def trend_analysis(
    topic: str = "agents",
    days: int = 14,
    top_k: int = 10,
    language: str = "",
) -> dict[str, object]:
    """Analyze trends from papers, blogs, and GitHub stars. Returns hot keywords and trending repos."""
    return SmearglePaperWorkflow().trend_analysis(topic or None, days, top_k, language or None)


@mcp.tool()
def check_artifacts(paper_id: str) -> dict[str, object]:
    """Check artifact completion status for a paper. Shows which steps are done and what's next."""
    return SmearglePaperWorkflow().check_artifacts(paper_id)


def _find_paper(paper_id: str) -> PaperMeta:
    for item in read_json(DATA_DIR / "papers" / "latest.json", []):
        if item.get("paper_id") == paper_id:
            return PaperMeta.from_dict(item)
    for path in [DATA_DIR / "ranked" / "latest.json", DATA_DIR / "papers" / "ranked_latest.json"]:
        for row in read_json(path, []):
            paper = row.get("paper", {})
            if paper.get("paper_id") == paper_id:
                return PaperMeta.from_dict(paper)
    raise ValueError(f"Paper not found in latest data: {paper_id}")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
