from __future__ import annotations

from pathlib import Path

from ..candidate_selection import select_candidates
from ..config import DATA_DIR
from ..models import PaperMeta
from ..storage import read_json, write_json
from .models import (
    RunRequest,
    ToolContext,
    ToolOutcome,
    ToolSpec,
    Usage,
    WorkflowDefinition,
    WorkflowStep,
)
from .registry import ToolRegistry


class QualityGateError(RuntimeError):
    pass


def built_in_workflows() -> dict[str, WorkflowDefinition]:
    return {
        "paper-research": WorkflowDefinition(
            "paper-research",
            "Collect or resolve a paper, rank it, and extract its evidence.",
            (
                WorkflowStep("collect", "paper.collect", "scout", condition=_needs_discovery, input_keys=("topic", "query", "days", "max_results")),
                WorkflowStep("rank", "paper.rank", "scout", ("collect",), condition=_needs_discovery, input_keys=("topic", "query", "top_k")),
                WorkflowStep("choose", "paper.choose", "scout", ("rank",), condition=_needs_discovery, input_keys=("ranking_profile", "candidate_count")),
                WorkflowStep("select", "paper.select", "scout", ("choose",), input_keys=("paper_id", "paper_url", "days")),
                WorkflowStep("ingest", "paper.ingest", "reader", ("select",), max_retries=1, input_keys=()),
            ),
        ),
        "paper-to-article": WorkflowDefinition(
            "paper-to-article",
            "Produce an evidence-first article with technical and WeChat review gates.",
            (
                WorkflowStep("collect", "paper.collect", "scout", condition=_needs_discovery, input_keys=("topic", "query", "days", "max_results")),
                WorkflowStep("rank", "paper.rank", "scout", ("collect",), condition=_needs_discovery, input_keys=("topic", "query", "top_k")),
                WorkflowStep("choose", "paper.choose", "scout", ("rank",), condition=_needs_discovery, input_keys=("ranking_profile", "candidate_count")),
                WorkflowStep("select", "paper.select", "scout", ("choose",), input_keys=("paper_id", "paper_url", "days")),
                WorkflowStep("ingest", "paper.ingest", "reader", ("select",), max_retries=1, input_keys=()),
                WorkflowStep("write", "paper.write", "writer", ("ingest",), input_keys=("paper_title", "notes", "writing_brief", "input_article", "revision_instruction", "revision_number", "target_audience", "style_mode", "target_score", "max_revisions")),
                WorkflowStep("review", "paper.review", "technical-reviewer", ("write",), input_keys=("target_score",)),
                WorkflowStep("package", "paper.package", "wechat-editor", ("review",), input_keys=("style_mode",)),
            ),
        ),
        "paper-to-wechat": WorkflowDefinition(
            "paper-to-wechat",
            "Create a reviewed article and a dry-run or approved real WeChat draft.",
            (
                WorkflowStep("collect", "paper.collect", "scout", condition=_needs_discovery, input_keys=("topic", "query", "days", "max_results")),
                WorkflowStep("rank", "paper.rank", "scout", ("collect",), condition=_needs_discovery, input_keys=("topic", "query", "top_k")),
                WorkflowStep("choose", "paper.choose", "scout", ("rank",), condition=_needs_discovery, input_keys=("ranking_profile", "candidate_count")),
                WorkflowStep("select", "paper.select", "scout", ("choose",), input_keys=("paper_id", "paper_url", "days")),
                WorkflowStep("ingest", "paper.ingest", "reader", ("select",), max_retries=1, input_keys=()),
                WorkflowStep("write", "paper.write", "writer", ("ingest",), input_keys=("paper_title", "notes", "writing_brief", "input_article", "revision_instruction", "revision_number", "target_audience", "style_mode", "target_score", "max_revisions")),
                WorkflowStep("review", "paper.review", "technical-reviewer", ("write",), input_keys=("target_score",)),
                WorkflowStep("package", "paper.package", "wechat-editor", ("review",), input_keys=("style_mode",)),
                WorkflowStep("publish", "wechat.publish", "publisher", ("package",), approval="real", input_keys=()),
            ),
        ),
        "daily-digest": WorkflowDefinition(
            "daily-digest",
            "Collect papers, blogs, and GitHub repositories into a durable digest.",
            (
                WorkflowStep("collect", "paper.collect", "scout", input_keys=("topic", "days", "top_k")),
                WorkflowStep("rank", "paper.rank", "scout", ("collect",), input_keys=("topic", "top_k")),
                WorkflowStep("blogs", "digest.blogs", "scout", input_keys=("topic", "days", "top_k")),
                WorkflowStep("github", "digest.github", "scout", input_keys=("language", "top_k")),
                WorkflowStep("digest", "digest.compose", "writer", ("rank", "blogs", "github"), input_keys=("topic", "top_k")),
                WorkflowStep("notify", "digest.notify", "publisher", ("digest",), condition=_notify_enabled, input_keys=("notify",)),
            ),
        ),
        "publish-existing": WorkflowDefinition(
            "publish-existing",
            "Create an approved WeChat draft from an existing reviewed article.",
            (
                WorkflowStep("prepare", "wechat.prepare-existing", "wechat-editor", input_keys=("article_json", "quality")),
                WorkflowStep("publish", "wechat.publish-existing", "publisher", ("prepare",), approval="real", input_keys=()),
            ),
        ),
    }


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    tools = [
        (ToolSpec("paper.collect", network=True, retryable=True), _collect),
        (ToolSpec("paper.rank"), _rank),
        (ToolSpec("paper.choose"), _choose),
        (ToolSpec("paper.select", network=True, retryable=True), _select),
        (ToolSpec("paper.ingest", network=True, retryable=True), _ingest),
        (ToolSpec("paper.write", network=True), _write),
        (ToolSpec("paper.review"), _review),
        (ToolSpec("paper.package"), _package),
        (ToolSpec("wechat.publish", network=True, side_effect=True, approval="real"), _publish),
        (ToolSpec("wechat.prepare-existing"), _prepare_existing),
        (ToolSpec("wechat.publish-existing", network=True, side_effect=True, approval="real"), _publish_existing),
        (ToolSpec("digest.blogs", network=True, retryable=True), _blogs),
        (ToolSpec("digest.github", network=True, retryable=True), _github),
        (ToolSpec("digest.compose"), _compose_digest),
        (ToolSpec("digest.notify", network=True, side_effect=True), _notify),
    ]
    for spec, handler in tools:
        registry.register(spec, handler)
    return registry


def _needs_discovery(request: RunRequest) -> bool:
    return not bool(request.inputs.get("paper_url") or request.inputs.get("paper_id") or request.inputs.get("offline_example"))


def _notify_enabled(request: RunRequest) -> bool:
    return bool(request.inputs.get("notify", False))


def _collect(ctx: ToolContext) -> ToolOutcome:
    inputs = ctx.request.inputs
    papers = ctx.workflow.collect(
        str(inputs.get("topic", "agents")),
        str(inputs["query"]) if inputs.get("query") else None,
        int(inputs.get("days", 30)),
        ["arxiv"],
        int(inputs.get("max_results", 50)),
    )
    return ToolOutcome(
        output={"papers": papers, "count": len(papers)},
        artifact_paths=[str(DATA_DIR / "papers" / "latest.json")],
        message=f"Collected {len(papers)} papers",
    )


def _rank(ctx: ToolContext) -> ToolOutcome:
    papers = list(ctx.outputs.get("collect", {}).get("papers", []))
    rows = ctx.workflow.rank(
        [PaperMeta.from_dict(item) for item in papers],
        top_k=int(ctx.request.inputs.get("top_k", 5)),
        query=str(ctx.request.inputs.get("query") or ctx.request.inputs.get("topic") or ""),
    )
    return ToolOutcome(
        output={"ranked": rows, "count": len(rows)},
        artifact_paths=[str(DATA_DIR / "ranked" / "latest.json")],
        message=f"Ranked {len(rows)} candidates",
    )


def _choose(ctx: ToolContext) -> ToolOutcome:
    rows = list(ctx.outputs.get("rank", {}).get("ranked", []))
    candidates = select_candidates(
        rows,
        profile=str(ctx.request.inputs.get("ranking_profile", "balanced")),
        limit=int(ctx.request.inputs.get("candidate_count", 3)),
    )
    interaction = {
        "type": "candidate_selection",
        "title": "选择一篇论文继续",
        "options": candidates,
        "empty_message": "暂时没有找到合适的论文。可以扩大时间范围或修改主题。",
        "suggested_actions": ["扩大到 90 天", "修改研究主题"],
    }
    return ToolOutcome(
        output={"candidates": candidates, "count": len(candidates)},
        interaction=interaction,
        message=f"Prepared {len(candidates)} candidates",
    )
def _select(ctx: ToolContext) -> ToolOutcome:
    inputs = ctx.request.inputs
    paper: PaperMeta | None = None
    if inputs.get("offline_example"):
        paper = _offline_paper()
    elif inputs.get("paper_url"):
        paper = ctx.workflow._resolve_paper(  # noqa: SLF001 - runtime adapter
            None, None, str(inputs["paper_url"]), int(inputs.get("days", 7)), 1
        )
    elif inputs.get("paper_id"):
        paper_id = str(inputs["paper_id"])
        for row in list(ctx.outputs.get("rank", {}).get("ranked", [])):
            candidate = row.get("paper") if isinstance(row, dict) else None
            if isinstance(candidate, dict) and candidate.get("paper_id") == paper_id:
                paper = PaperMeta.from_dict(candidate)
                break
        for location in (DATA_DIR / "papers" / "latest.json", DATA_DIR / "ranked" / "latest.json"):
            if paper is not None:
                break
            rows: list[dict[str, object]] = read_json(location, [])
            for item in rows:
                candidate = item.get("paper", item) if isinstance(item, dict) else {}
                if isinstance(candidate, dict) and candidate.get("paper_id") == paper_id:
                    paper = PaperMeta.from_dict(candidate)
                    break
            if paper:
                break
        if paper is None:
            paper = ctx.workflow.collector.fetch_by_id(paper_id)
    else:
        rows = list(ctx.outputs.get("rank", {}).get("ranked", []))
        if rows:
            selected = rows[0].get("paper") if isinstance(rows[0], dict) else None
            if isinstance(selected, dict):
                paper = PaperMeta.from_dict(selected)
    if paper is None:
        raise RuntimeError("No paper could be selected. Broaden the query or provide --paper-url.")
    return ToolOutcome(output={"paper": paper.to_dict()}, message=f"Selected {paper.title}")


def _ingest(ctx: ToolContext) -> ToolOutcome:
    paper = PaperMeta.from_dict(dict(ctx.outputs["select"]["paper"]))
    if ctx.request.inputs.get("offline_example"):
        parsed_path = DATA_DIR / "parsed" / f"{paper.paper_id}.json"
        write_json(parsed_path, _offline_parsed(paper))
    else:
        result = ctx.workflow.read(paper)
        parsed_path = Path(str(result["parsed"]))
    return ToolOutcome(
        output={"paper": paper.to_dict(), "parsed": str(parsed_path)},
        artifact_paths=[str(parsed_path)],
        message="Extracted paper text, sections, and evidence",
    )


def _write(ctx: ToolContext) -> ToolOutcome:
    paper = dict(ctx.outputs["ingest"]["paper"])
    inputs = ctx.request.inputs
    writer = getattr(ctx.workflow, "writer", None)
    if writer is not None and hasattr(writer, "reset_usage"):
        writer.reset_usage()
    if writer is not None and ctx.request.model:
        writer.model = ctx.request.model
    result = ctx.workflow.run_writing_agent(
        str(paper["paper_id"]),
        paper_url=str(inputs["paper_url"]) if inputs.get("paper_url") else None,
        paper_title=str(inputs["paper_title"]) if inputs.get("paper_title") else None,
        notes_path=Path(str(inputs["notes"])) if inputs.get("notes") else None,
        article_path=Path(str(inputs["input_article"])) if inputs.get("input_article") else None,
        writing_brief=str(inputs.get("writing_brief", "")),
        revision_instruction=str(inputs.get("revision_instruction", "")),
        target_audience=str(inputs.get("target_audience", "AI方向研究生和算法岗候选人")),
        style_mode=str(inputs.get("style_mode", "balanced")),
        target_score=int(inputs.get("target_score", 85)),
        max_revisions=int(inputs.get("max_revisions", 3)),
        upload_images=False,
        force_local=bool(inputs.get("offline_example")),
    )
    final = dict(result.get("final", {}))
    artifact_paths = [
        str(path)
        for path in (final.get("article_json"), final.get("markdown"), final.get("html"), final.get("review"), final.get("publish_package"))
        if path and Path(str(path)).is_file()
    ]
    usage_data = dict(getattr(writer, "usage", {}))
    usage = Usage(
        input_tokens=int(usage_data.get("input_tokens") or 0),
        output_tokens=int(usage_data.get("output_tokens") or 0),
        cost=float(usage_data["cost"]) if usage_data.get("cost") is not None else None,
    )
    fallback_used = bool(result.get("model_fallbacks")) or bool(final.get("model_fallback_used"))
    return ToolOutcome(
        output={"writing": result, "final": final, "paper": paper},
        artifact_paths=artifact_paths,
        quality={
            "technical_score": final.get("technical_score"),
            "wechat_score": final.get("wechat_score"),
            "content_ready": final.get("content_ready", False),
            "publish_ready": final.get("publish_ready", False),
        },
        usage=usage,
        message=(
            "Model timed out; generated a local evidence draft for human review"
            if fallback_used
            else "Generated and revised the article"
        ),
    )


def _review(ctx: ToolContext) -> ToolOutcome:
    final = dict(ctx.outputs["write"].get("final", {}))
    review_payload = read_json(Path(str(final.get("review", ""))), {}) if final.get("review") else {}
    technical_review = dict(review_payload.get("technical_review", {})) if isinstance(review_payload, dict) else {}
    wechat_review = dict(review_payload.get("wechat_review", {})) if isinstance(review_payload, dict) else {}
    issues = [
        *list(technical_review.get("blocking_issues", [])),
        *list(wechat_review.get("publication_blocking_issues", [])),
    ]
    quality = {
        "technical_score": final.get("technical_score"),
        "wechat_score": final.get("wechat_score"),
        "content_ready": bool(final.get("content_ready")),
        "publish_ready": bool(final.get("publish_ready")),
        "target_score": int(ctx.request.inputs.get("target_score", 85)),
        "issues": [str(item) for item in issues[:3]],
    }
    if not quality["content_ready"] and ctx.request.workflow == "paper-to-wechat":
        raise QualityGateError("Article did not pass the technical and WeChat content gates.")
    review_path = final.get("review")
    return ToolOutcome(
        output={"quality": quality, "final": final, "paper": ctx.outputs["write"].get("paper", {})},
        artifact_paths=[str(review_path)] if review_path and Path(str(review_path)).is_file() else [],
        quality=quality,
        message=(
            "Article passed both content gates"
            if quality["content_ready"]
            else "Generated article requires human review before publication"
        ),
    )


def _package(ctx: ToolContext) -> ToolOutcome:
    final = dict(ctx.outputs["review"]["final"])
    package = final.get("publish_package")
    return ToolOutcome(
        output={"final": final, "quality": ctx.outputs["review"]["quality"], "paper": ctx.outputs["review"].get("paper", {})},
        artifact_paths=[str(package)] if package and Path(str(package)).is_file() else [],
        quality=dict(ctx.outputs["review"]["quality"]),
        message="Prepared the publication package",
    )


def _publish(ctx: ToolContext) -> ToolOutcome:
    final = dict(ctx.outputs["package"]["final"])
    article_json = Path(str(final.get("article_json", "")))
    if not article_json.is_file():
        raise RuntimeError(f"Final article not found: {article_json}")
    paper_id = str(ctx.outputs["package"].get("paper", {}).get("paper_id", "article")).replace("/", "_")
    result_path = DATA_DIR / "wechat" / ("dry_run" if ctx.request.dry_run else "") / f"{paper_id}.json"
    existing: dict[str, object] = read_json(result_path, {}) if result_path.is_file() else {}
    result = (
        existing
        if existing.get("media_id")
        else ctx.workflow.publish_existing_article(article_json, real_wechat=not ctx.request.dry_run, publish=False)
    )
    media_id = result.get("media_id")
    output = {"draft": result, "external_id": media_id, "dry_run": ctx.request.dry_run}
    return ToolOutcome(output=output, message="Created dry-run output" if ctx.request.dry_run else "Created WeChat draft")


def _prepare_existing(ctx: ToolContext) -> ToolOutcome:
    article_json = Path(str(ctx.request.inputs.get("article_json", "")))
    if not article_json.is_file():
        raise RuntimeError("没有找到已审阅文章，无法创建微信草稿。")
    quality = dict(ctx.request.inputs.get("quality", {}))
    if not quality.get("publish_ready"):
        raise QualityGateError("文章尚未通过发布质量检查。")
    return ToolOutcome(
        output={"article_json": str(article_json), "quality": quality},
        artifact_paths=[str(article_json)],
        quality=quality,
        message="已校验当前文章",
    )


def _publish_existing(ctx: ToolContext) -> ToolOutcome:
    article_json = Path(str(ctx.outputs["prepare"]["article_json"]))
    result = ctx.workflow.publish_existing_article(article_json, real_wechat=True, publish=False)
    return ToolOutcome(
        output={"draft": result, "external_id": result.get("media_id"), "dry_run": False},
        message="已创建微信草稿",
    )


def _blogs(ctx: ToolContext) -> ToolOutcome:
    inputs = ctx.request.inputs
    result = ctx.workflow.collect_blogs(
        str(inputs.get("topic", "agents")), None, int(inputs.get("days", 7)), int(inputs.get("top_k", 5)) * 3
    )
    return ToolOutcome(output=result, artifact_paths=[str(DATA_DIR / "blogs" / "latest.json")], message=f"Collected {result.get('count', 0)} blog posts")


def _github(ctx: ToolContext) -> ToolOutcome:
    inputs = ctx.request.inputs
    result = ctx.workflow.collect_github_stars(
        str(inputs["language"]) if inputs.get("language") else None, "weekly", int(inputs.get("top_k", 5)) * 2
    )
    return ToolOutcome(output=result, artifact_paths=[str(DATA_DIR / "github" / "latest.json")], message=f"Collected {result.get('count', 0)} repositories")


def _compose_digest(ctx: ToolContext) -> ToolOutcome:
    top_k = int(ctx.request.inputs.get("top_k", 5))
    payload = {
        "topic": ctx.request.inputs.get("topic", "agents"),
        "papers": list(ctx.outputs["rank"].get("ranked", []))[:top_k],
        "blogs": list(ctx.outputs["blogs"].get("posts", []))[:top_k],
        "github": list(ctx.outputs["github"].get("repos", []))[:top_k],
    }
    return ToolOutcome(output={"digest": payload}, message="Composed daily digest")


def _notify(ctx: ToolContext) -> ToolOutcome:
    digest = dict(ctx.outputs["digest"]["digest"])
    lines = [f"SmearglePaper {digest.get('topic')} digest", f"Papers: {len(digest.get('papers', []))}", f"Blogs: {len(digest.get('blogs', []))}", f"GitHub: {len(digest.get('github', []))}"]
    result = ctx.workflow.send_notification("SmearglePaper Daily Digest", "\n".join(lines))
    return ToolOutcome(output={"notification": result}, message="Sent digest notification")


def _offline_paper() -> PaperMeta:
    return PaperMeta(
        paper_id="offline.transformer",
        title="A Small Offline Transformer Walkthrough",
        authors=["SmearglePaper Example"],
        abstract=(
            "This bundled example explains a compact encoder-decoder Transformer. "
            "It is synthetic evidence for validating the local workflow and must not be presented as a real paper."
        ),
        source="bundled-example",
        url="https://example.invalid/smearglepaper/offline-transformer",
        pdf_url=None,
        published_at="2026-07-16",
        categories=["cs.AI"],
    )


def _offline_parsed(paper: PaperMeta) -> dict[str, object]:
    introduction = (
        "The example asks whether a sequence transduction system can be explained with only attention, "
        "feed-forward layers, residual connections, and positional information."
    )
    method = (
        "The encoder and decoder each contain two blocks. Every block uses four attention heads and a "
        "128-dimensional hidden representation. The decoder masks future positions during training."
    )
    experiment = (
        "On a synthetic copy task with 10000 generated sequences, the described configuration reaches "
        "96 percent exact-match accuracy. This result applies only to the bundled synthetic task."
    )
    return {
        "paper": paper.to_dict(),
        "text": "\n\n".join((introduction, method, experiment)),
        "sections": [
            {"title": "Introduction", "text": introduction, "page_start": 1, "page_end": 1},
            {"title": "Method", "text": method, "page_start": 2, "page_end": 2},
            {"title": "Synthetic Experiment", "text": experiment, "page_start": 3, "page_end": 3},
        ],
        "figures": [],
        "visuals": [],
        "offline_example": True,
    }
