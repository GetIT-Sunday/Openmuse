from __future__ import annotations

import re
from pathlib import Path

from .candidate_selection import RANKING_PROFILES, select_candidates
from .models import PaperMeta
from .product_tasks import ProductTask, ProductTaskRepository
from .workflow import SmearglePaperWorkflow


class ProductAgent:
    """Durable product-level orchestrator above the existing writing workflow."""

    def __init__(
        self,
        workflow: SmearglePaperWorkflow | None = None,
        repository: ProductTaskRepository | None = None,
    ) -> None:
        self.workflow = workflow or SmearglePaperWorkflow()
        self.repository = repository or ProductTaskRepository()

    def create(
        self,
        request: str,
        *,
        paper_id: str | None = None,
        paper_url: str | None = None,
        article_json: Path | None = None,
        ranking_profile: str = "balanced",
        days: int = 30,
        candidate_count: int = 3,
    ) -> dict[str, object]:
        if ranking_profile not in RANKING_PROFILES:
            raise ValueError(f"Unknown ranking profile: {ranking_profile}")
        paper_url = paper_url or _extract_url(request)
        paper_id = paper_id or _paper_id_from_url(paper_url)
        intent = "explain_paper" if paper_id or paper_url or article_json else "explore_topic"
        task = ProductTask.create(
            intent,
            {
                "message": request,
                "paper_id": paper_id,
                "paper_url": paper_url,
                "article_json": str(article_json) if article_json else None,
                "ranking_profile": ranking_profile,
                "days": days,
                "candidate_count": candidate_count,
            },
        )
        task.transition("planning", "task-planner")
        if article_json:
            task.artifacts["final_article_json"] = str(article_json)
            task.transition("reviewing", "imported-article-review")
            task.transition("awaiting_publish_approval", "publish-approval")
        elif paper_id or paper_url:
            task.selected_paper = {"paper_id": paper_id, "url": paper_url}
            task.transition("researching", "paper-research")
        else:
            task.plan = {"next": "candidate-discovery", "query": request, "ranking_profile": ranking_profile}
            task.transition("discovering", "candidate-discovery")
        manifest = self.repository.save(task)
        return self._result(task, manifest)

    def resume(self, task_id: str, *, real_wechat: bool = False) -> dict[str, object]:
        task = self.repository.load(task_id)
        try:
            if task.status in {"researching", "writing", "reviewing"}:
                return self._run_writing(task)
            if task.status == "discovering":
                return self._discover(task)
            if task.status == "creating_draft":
                return self._create_or_update_draft(task, real_wechat=real_wechat)
            if task.status in {"awaiting_topic_approval", "awaiting_publish_approval"}:
                return self._result(task, self.repository.path(task.task_id))
            return self._result(task, self.repository.path(task.task_id))
        except Exception as exc:
            task.transition(
                "failed",
                task.active_stage,
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            manifest = self.repository.save(task)
            raise RuntimeError(f"Product task {task.task_id} failed at {task.active_stage}: {exc}") from exc

    def approve(self, task_id: str, gate: str, *, paper_id: str | None = None) -> dict[str, object]:
        task = self.repository.load(task_id)
        artifact = str(task.artifacts.get("final_article_json", "")) if gate == "publish" else None
        if gate == "topic":
            selected = _select_candidate(task.candidates, paper_id)
            task.selected_paper = selected
            task.request["paper_id"] = selected["paper_id"]
            task.request["paper_url"] = selected["url"]
            task.approve(gate, detail={"paper_id": selected["paper_id"]})
            task.transition("researching", "paper-research")
        else:
            if not artifact:
                raise ValueError("Publish approval requires a final article artifact.")
            task.approve(gate, artifact=artifact)
            task.transition("creating_draft", "create-wechat-draft")
        manifest = self.repository.save(task)
        return self._result(task, manifest)

    def show(self, task_id: str) -> dict[str, object]:
        return self.repository.load(task_id).to_dict()

    def list(self) -> dict[str, object]:
        rows = self.repository.list()
        return {"count": len(rows), "tasks": rows}

    def _discover(self, task: ProductTask) -> dict[str, object]:
        query = str(task.plan.get("query") or task.request.get("message") or "")
        days = int(task.request.get("days", 30))
        candidate_count = int(task.request.get("candidate_count", 3))
        profile = str(task.request.get("ranking_profile", "balanced"))
        collected = self.workflow.collect(None, query, days, ["arxiv"], max(candidate_count * 10, 20))
        papers = [PaperMeta.from_dict(item) for item in collected]
        ranked = self.workflow.rank(papers, top_k=len(papers), query=query)
        task.candidates = select_candidates(ranked, profile=profile, limit=candidate_count)
        task.artifacts["candidate_count"] = len(task.candidates)
        if not task.candidates:
            task.transition(
                "needs_attention",
                "candidate-ranking",
                error={"type": "NoCandidates", "message": "No unpublished candidate papers were found."},
            )
        else:
            task.transition("awaiting_topic_approval", "topic-approval")
        manifest = self.repository.save(task)
        return self._result(task, manifest)

    def _run_writing(self, task: ProductTask) -> dict[str, object]:
        task.transition("writing", "paper-writing-agent")
        self.repository.save(task)
        result = self.workflow.run_writing_agent(
            str(task.request.get("paper_id") or "") or None,
            paper_url=str(task.request.get("paper_url") or "") or None,
        )
        final = result.get("final", {})
        if not isinstance(final, dict):
            raise RuntimeError("Writing Agent returned no final artifact.")
        task.writing_run_id = str(result.get("run_id") or "")
        task.artifacts["writing_manifest"] = str(result.get("manifest") or "")
        task.artifacts["final_article_json"] = str(final.get("article_json") or "")
        task.artifacts["publish_package"] = str(final.get("publish_package") or "")
        task.artifacts["content_ready"] = bool(final.get("content_ready"))
        task.artifacts["publish_ready"] = bool(final.get("publish_ready"))
        task.transition("reviewing", "quality-gate")
        if final.get("publish_ready"):
            task.transition("awaiting_publish_approval", "publish-approval")
        else:
            task.transition(
                "needs_attention",
                "quality-gate",
                error={"type": "QualityGate", "message": "Article is not publish_ready."},
            )
        manifest = self.repository.save(task)
        return self._result(task, manifest)

    def _create_or_update_draft(self, task: ProductTask, *, real_wechat: bool) -> dict[str, object]:
        approval = task.latest_approval("publish")
        article_json = Path(str(task.artifacts.get("final_article_json", "")))
        if not approval or approval.get("artifact") != str(article_json):
            raise RuntimeError("A current publish approval is required.")
        if not article_json.exists():
            raise RuntimeError(f"Approved article artifact not found: {article_json}")
        media_id = str(task.wechat.get("media_id") or "")
        if media_id:
            report = self.workflow.update_existing_draft(article_json, media_id, real_wechat=real_wechat)
        else:
            report = self.workflow.publish_existing_article(article_json, real_wechat=real_wechat, publish=False)
        if not report.get("ok"):
            raise RuntimeError(f"WeChat draft operation failed: {report}")
        task.wechat["media_id"] = report.get("media_id")
        task.artifacts["wechat_draft"] = report
        task.transition("draft_created", "complete")
        manifest = self.repository.save(task)
        return self._result(task, manifest)

    @staticmethod
    def _result(task: ProductTask, manifest: Path) -> dict[str, object]:
        return {
            "task_id": task.task_id,
            "intent": task.intent,
            "status": task.status,
            "active_stage": task.active_stage,
            "next_action": _next_action(task),
            "manifest": str(manifest),
            "artifacts": task.artifacts,
            "candidates": task.candidates,
            "wechat": task.wechat,
            "error": task.error,
        }


def _next_action(task: ProductTask) -> str:
    return {
        "discovering": "Run candidate discovery (planned for Phase 2).",
        "researching": f"Resume task {task.task_id} to start the writing workflow.",
        "awaiting_topic_approval": f"Approve topic for task {task.task_id}.",
        "awaiting_publish_approval": f"Review preview and approve publish for task {task.task_id}.",
        "creating_draft": f"Resume task {task.task_id} to create or update the WeChat draft.",
        "needs_attention": "Resolve the reported issue, then resume the task.",
        "draft_created": "Review the WeChat draft in the Official Account backend.",
    }.get(task.status, "")


def _extract_url(message: str) -> str | None:
    match = re.search(r"https?://[^\s]+", message)
    return match.group(0).rstrip(".,;，。；") if match else None


def _paper_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    return url.rstrip("/").split("/")[-1].removesuffix(".pdf")


def _select_candidate(candidates: list[dict[str, object]], paper_id: str | None) -> dict[str, object]:
    if not candidates:
        raise ValueError("No candidates are available for approval.")
    if not paper_id:
        return candidates[0]
    for candidate in candidates:
        if candidate.get("paper_id") == paper_id or candidate.get("canonical_id") == paper_id:
            return candidate
    raise ValueError(f"Candidate paper not found: {paper_id}")
