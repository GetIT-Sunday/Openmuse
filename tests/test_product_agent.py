from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from smearglepaper.candidate_selection import canonical_paper_id, select_candidates
from smearglepaper.models import Article, PaperMeta
from smearglepaper.product_agent import ProductAgent
from smearglepaper.product_tasks import ProductTask, ProductTaskRepository
from smearglepaper.storage import read_json, write_json


def _paper() -> PaperMeta:
    return PaperMeta(
        "1706.03762",
        "Attention Is All You Need",
        [],
        "",
        "arxiv",
        "https://arxiv.org/abs/1706.03762",
        None,
        "",
    )


def _write_article(path: Path, markdown: str = "# A careful paper title\n\nWhy does this method work?") -> Path:
    write_json(path, Article(_paper(), "A careful paper title", "digest", markdown, "<p>article</p>").to_dict())
    return path


READY_TECHNICAL = {"score": 90, "blocking_issues": [], "revision_instructions": []}
READY_WECHAT = {"score": 90, "revision_instructions": [], "publication_blocking_issues": []}


class ProductTaskTests(unittest.TestCase):
    def test_rejects_invalid_transition(self) -> None:
        task = ProductTask.create("explain_paper", {})
        with self.assertRaisesRegex(ValueError, "Invalid task transition"):
            task.transition("published", "complete")

    def test_repository_persists_and_lists_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = ProductTaskRepository(Path(tmp))
            task = ProductTask.create("explain_paper", {"paper_id": "1706.03762"})
            repository.save(task)

            self.assertEqual(repository.load(task.task_id).task_id, task.task_id)
            self.assertEqual(repository.list()[0]["status"], "created")
            self.assertEqual(task.revision, 1)

    def test_repository_rejects_stale_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = ProductTaskRepository(Path(tmp))
            task = ProductTask.create("explain_paper", {})
            repository.save(task)
            stale = repository.load(task.task_id)
            repository.save(task)

            with self.assertRaisesRegex(RuntimeError, "revision conflict"):
                repository.save(stale)

    def test_atomic_json_write_preserves_previous_file_on_replace_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            write_json(path, {"version": 1})

            with patch("smearglepaper.storage.os.replace", side_effect=OSError("interrupted")):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    write_json(path, {"version": 2})

            self.assertEqual(read_json(path, {})["version"], 1)


class ProductAgentTests(unittest.TestCase):
    def test_paper_url_creates_resumable_research_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = ProductAgent(repository=ProductTaskRepository(Path(tmp)), workflow=Mock())
            result = agent.create("解读 https://arxiv.org/abs/1706.03762")

            self.assertEqual(result["intent"], "explain_paper")
            self.assertEqual(result["status"], "researching")

    def test_resume_runs_writing_and_waits_for_publish_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = ProductTaskRepository(Path(tmp))
            workflow = Mock()
            article = _write_article(Path(tmp) / "publish-ready.json")
            workflow.run_writing_agent.return_value = {
                "run_id": "run-1",
                "manifest": "writing-manifest.json",
                "final": {
                    "article_json": str(article),
                    "publish_package": "package.md",
                    "content_ready": True,
                    "publish_ready": True,
                },
            }
            agent = ProductAgent(repository=repository, workflow=workflow)
            created = agent.create("解读论文", paper_id="1706.03762")
            result = agent.resume(str(created["task_id"]))

            self.assertEqual(result["status"], "awaiting_publish_approval")
            self.assertEqual(result["artifacts"]["final_article_json"], str(article))

    def test_topic_task_discovers_candidates_and_waits_for_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = ProductTaskRepository(Path(tmp))
            workflow = Mock()
            paper = PaperMeta(
                "2606.00001v1",
                "Reliable Memory Agents",
                ["Ada"],
                "We introduce a reliable memory architecture for language model agents.",
                "arxiv",
                "https://arxiv.org/abs/2606.00001v1",
                "https://arxiv.org/pdf/2606.00001v1",
                "2026-06-14T00:00:00Z",
                categories=["cs.AI"],
            )
            workflow.collect.return_value = [paper.to_dict()]
            workflow.rank.return_value = [{"score": 8.0, "paper": paper.to_dict(), "reasons": ["topic_terms=4"]}]
            agent = ProductAgent(repository=repository, workflow=workflow)

            created = agent.create("找近期值得解读的 Agent Memory 论文", ranking_profile="frontier")
            discovered = agent.resume(str(created["task_id"]))
            approved = agent.approve(str(created["task_id"]), "topic", paper_id="2606.00001v1")

            self.assertEqual(discovered["status"], "awaiting_topic_approval")
            self.assertEqual(discovered["candidates"][0]["ranking_profile"], "frontier")
            self.assertEqual(approved["status"], "researching")
            self.assertEqual(repository.load(str(created["task_id"])).request["paper_id"], "2606.00001v1")

    def test_publish_approval_then_dry_run_draft_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = _write_article(root / "publish-ready.json")
            repository = ProductTaskRepository(root / "tasks")
            workflow = Mock()
            workflow.publish_existing_article.return_value = {"ok": True, "media_id": "draft-1"}
            workflow.update_existing_draft.return_value = {"ok": True, "media_id": "draft-1"}
            agent = ProductAgent(repository=repository, workflow=workflow)

            created = agent.create("使用现有文章", article_json=article)
            with (
                patch("smearglepaper.product_agent.technical_review", return_value=(READY_TECHNICAL, "")),
                patch("smearglepaper.product_agent.wechat_review", return_value=(READY_WECHAT, "")),
            ):
                reviewed = agent.resume(str(created["task_id"]))
            approved = agent.approve(str(created["task_id"]), "publish")
            first = agent.resume(str(created["task_id"]))
            second = agent.resume(str(created["task_id"]), real_wechat=True)

            self.assertEqual(reviewed["status"], "awaiting_publish_approval")
            self.assertEqual(approved["status"], "creating_draft")
            self.assertEqual(first["status"], "draft_simulated")
            self.assertEqual(second["status"], "draft_created")
            self.assertEqual(workflow.publish_existing_article.call_count, 2)
            workflow.update_existing_draft.assert_not_called()
            task = repository.load(str(created["task_id"]))
            self.assertEqual(task.wechat["real"]["media_id"], "draft-1")
            self.assertEqual(task.wechat["dry_run"]["media_id"], "draft-1")

    def test_changed_article_invalidates_publish_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = _write_article(root / "publish-ready.json")
            repository = ProductTaskRepository(root / "tasks")
            workflow = Mock()
            agent = ProductAgent(repository=repository, workflow=workflow)
            created = agent.create("使用现有文章", article_json=article)
            with (
                patch("smearglepaper.product_agent.technical_review", return_value=(READY_TECHNICAL, "")),
                patch("smearglepaper.product_agent.wechat_review", return_value=(READY_WECHAT, "")),
            ):
                agent.resume(str(created["task_id"]))
            agent.approve(str(created["task_id"]), "publish")
            _write_article(article, markdown="# Changed article")

            result = agent.resume(str(created["task_id"]), real_wechat=True)

            self.assertEqual(result["status"], "reviewing")
            workflow.publish_existing_article.assert_not_called()
            self.assertIsNone(repository.load(str(created["task_id"])).latest_approval("publish"))

    def test_imported_article_must_pass_review_before_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            article = _write_article(Path(tmp) / "article.json")
            agent = ProductAgent(repository=ProductTaskRepository(Path(tmp) / "tasks"), workflow=Mock())
            created = agent.create("使用现有文章", article_json=article)

            result = agent.resume(str(created["task_id"]))

            self.assertEqual(result["status"], "needs_attention")
            with self.assertRaisesRegex(ValueError, "not waiting"):
                agent.approve(str(created["task_id"]), "publish")

    def test_retry_restores_recorded_recovery_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = ProductTaskRepository(Path(tmp))
            task = ProductTask.create("explore_topic", {})
            task.transition("planning", "task-planner")
            task.transition("discovering", "candidate-discovery")
            task.transition(
                "needs_attention",
                "candidate-ranking",
                error={"recovery_status": "discovering", "recovery_stage": "candidate-discovery"},
            )
            repository.save(task)

            result = ProductAgent(repository=repository, workflow=Mock()).retry(task.task_id)

            self.assertEqual(result["status"], "discovering")


class CandidateSelectionTests(unittest.TestCase):
    def test_canonical_paper_id_groups_arxiv_versions(self) -> None:
        self.assertEqual(canonical_paper_id("https://arxiv.org/abs/2606.00001v3"), "2606.00001")

    def test_selection_filters_history_and_explains_uncertainty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp) / "history.json"
            history.write_text('[{"paper_id":"2606.00001v1","ready":true}]', encoding="utf-8")
            old = PaperMeta("2606.00001v2", "Old", [], "abstract", "arxiv", "", None, "2026-06-01T00:00:00Z")
            new = PaperMeta("2606.00002v1", "New", [], "A useful abstract about agents.", "arxiv", "", None, "2026-06-14T00:00:00Z", categories=["cs.AI"])
            rows = [
                {"score": 10, "paper": old.to_dict(), "reasons": []},
                {"score": 8, "paper": new.to_dict(), "reasons": []},
            ]

            selected = select_candidates(rows, profile="balanced", history_path=history)

            self.assertEqual([item["paper_id"] for item in selected], ["2606.00002v1"])
            self.assertIn("citation_count_unavailable", selected[0]["uncertainty"])


if __name__ == "__main__":
    unittest.main()
