from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from smearglepaper.candidate_selection import canonical_paper_id, select_candidates
from smearglepaper.models import PaperMeta
from smearglepaper.product_agent import ProductAgent
from smearglepaper.product_tasks import ProductTask, ProductTaskRepository


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
            workflow.run_writing_agent.return_value = {
                "run_id": "run-1",
                "manifest": "writing-manifest.json",
                "final": {
                    "article_json": "publish-ready.json",
                    "publish_package": "package.md",
                    "content_ready": True,
                    "publish_ready": True,
                },
            }
            agent = ProductAgent(repository=repository, workflow=workflow)
            created = agent.create("解读论文", paper_id="1706.03762")
            result = agent.resume(str(created["task_id"]))

            self.assertEqual(result["status"], "awaiting_publish_approval")
            self.assertEqual(result["artifacts"]["final_article_json"], "publish-ready.json")

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
            article = root / "publish-ready.json"
            article.write_text("{}", encoding="utf-8")
            repository = ProductTaskRepository(root / "tasks")
            workflow = Mock()
            workflow.publish_existing_article.return_value = {"ok": True, "media_id": "draft-1"}
            workflow.update_existing_draft.return_value = {"ok": True, "media_id": "draft-1"}
            agent = ProductAgent(repository=repository, workflow=workflow)

            created = agent.create("使用现有文章", article_json=article)
            approved = agent.approve(str(created["task_id"]), "publish")
            first = agent.resume(str(created["task_id"]))
            task = repository.load(str(created["task_id"]))
            task.transition("creating_draft", "create-wechat-draft")
            repository.save(task)
            second = agent.resume(str(created["task_id"]))

            self.assertEqual(approved["status"], "creating_draft")
            self.assertEqual(first["status"], "draft_created")
            self.assertEqual(second["status"], "draft_created")
            workflow.publish_existing_article.assert_called_once()
            workflow.update_existing_draft.assert_called_once()


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
