from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from smearglepaper.models import PaperMeta
from smearglepaper.runtime import AgentRuntime, RunRequest


class FakePaperWorkflow:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.publish_calls = 0
        self.writing_calls: list[dict[str, object]] = []

    def collect(self, topic, query, days, sources, max_results):
        return [
            PaperMeta(
                f"2607.1000{index}",
                title,
                ["Researcher"],
                f"{title} proposes a reliable method for agent evaluation with detailed experiments.",
                "arxiv",
                f"https://arxiv.org/abs/2607.1000{index}",
                f"https://arxiv.org/pdf/2607.1000{index}",
                "2026-07-20T00:00:00Z",
                categories=["cs.AI"],
            ).to_dict()
            for index, title in enumerate(("Reliable Agents", "Efficient Agents", "Safe Agents"), 1)
        ]

    def rank(self, papers, top_k=5, query=None):
        return [
            {"paper": paper.to_dict(), "score": 9.5 - index, "reasons": ["与主题相关"]}
            for index, paper in enumerate(papers[:top_k])
        ]

    def _resolve_paper(self, *args):
        return PaperMeta(
            "1706.03762",
            "Attention Is All You Need",
            ["Vaswani et al."],
            "Transformer abstract",
            "arxiv",
            "https://arxiv.org/abs/1706.03762",
            "https://arxiv.org/pdf/1706.03762",
            "2017-06-12",
        )

    def read(self, paper):
        from smearglepaper import config as legacy_config

        path = legacy_config.DATA_DIR / "parsed" / f"{paper.paper_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"paper": {"paper_id": "1706.03762"}}', encoding="utf-8")
        return {"parsed": str(path)}

    def run_writing_agent(self, paper_id, **kwargs):
        self.writing_calls.append(dict(kwargs))
        article = self.root / "article.json"
        markdown = self.root / "article.md"
        html = self.root / "article.html"
        review = self.root / "review.json"
        package = self.root / "publish_package.md"
        for path, content in (
            (article, "{}"),
            (markdown, "# Article"),
            (html, "<h1>Article</h1>"),
            (review, "{}"),
            (package, "# Package"),
        ):
            path.write_text(content, encoding="utf-8")
        return {
            "status": "success",
            "final": {
                "article_json": str(article),
                "markdown": str(markdown),
                "html": str(html),
                "review": str(review),
                "publish_package": str(package),
                "technical_score": 92,
                "wechat_score": 90,
                "content_ready": True,
                "publish_ready": True,
            },
        }

    def publish_existing_article(self, article_json, real_wechat, publish):
        self.publish_calls += 1
        return {"ok": True, "media_id": "draft-1", "dry_run": not real_wechat}


class ReviewRequiredWorkflow(FakePaperWorkflow):
    def run_writing_agent(self, paper_id, **kwargs):
        result = super().run_writing_agent(paper_id, **kwargs)
        result["status"] = "needs_human_review"
        result["model_fallbacks"] = [
            {"stage": "outline-planner", "type": "TimeoutError", "message": "provider timeout"}
        ]
        result["final"].update(
            {
                "technical_score": 72,
                "wechat_score": 68,
                "content_ready": False,
                "publish_ready": False,
                "model_fallback_used": True,
            }
        )
        return result


class FailingRevisionWorkflow(FakePaperWorkflow):
    def run_writing_agent(self, paper_id, **kwargs):
        if self.writing_calls:
            raise RuntimeError("revision failed")
        return super().run_writing_agent(paper_id, **kwargs)


class BuiltInWorkflowTests(unittest.TestCase):
    def test_topic_flow_waits_for_candidate_and_resumes_selected_paper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = FakePaperWorkflow(root)
            runtime = AgentRuntime(root / "runtime", workflow=workflow)  # type: ignore[arg-type]
            waiting = runtime.run(RunRequest("paper-to-article", {"topic": "agents", "candidate_count": 3}))
            manifest = runtime.get_run(waiting.run_id)
            self.assertEqual(waiting.status, "waiting_input")
            self.assertEqual(manifest["schema_version"], 2)
            self.assertEqual(len(manifest["interaction"]["options"]), 3)

            runtime.resolve_interaction(waiting.run_id, "2607.10002")
            completed = runtime.resume(waiting.run_id)
            self.assertEqual(completed.status, "completed")
            selected = runtime.store.load_checkpoint(waiting.run_id, "select")["output"]["paper"]
            self.assertEqual(selected["paper_id"], "2607.10002")
            event_types = [event.type for event in runtime.events(waiting.run_id)]
            self.assertIn("interaction.required", event_types)
            self.assertIn("interaction.resolved", event_types)

    def test_revision_reuses_research_steps_and_records_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = FakePaperWorkflow(root)
            runtime = AgentRuntime(root / "runtime", workflow=workflow)  # type: ignore[arg-type]
            first = runtime.run(RunRequest("paper-to-article", {"paper_url": "https://arxiv.org/abs/1706.03762"}))
            manifest = runtime.get_run(first.run_id)
            article_md = next(
                item["path"]
                for item in manifest["artifacts"]
                if item["producer"] == "write" and item["path"].endswith(".md") and "publish_package" not in item["path"]
            )
            before = len(runtime.events(first.run_id))
            runtime.update_inputs(
                first.run_id,
                {"input_article": article_md, "revision_instruction": "标题更克制", "revision_number": 2},
                restart_step="write",
            )
            revised = runtime.execute(first.run_id)
            self.assertEqual(revised.status, "completed")
            tools = [
                event.payload.get("tool")
                for event in runtime.events(first.run_id)[before:]
                if event.type == "tool.started"
            ]
            self.assertNotIn("paper.collect", tools)
            self.assertNotIn("paper.ingest", tools)
            self.assertEqual(workflow.writing_calls[-1]["revision_instruction"], "标题更克制")

    def test_failed_revision_restores_previous_article_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = FailingRevisionWorkflow(root)
            runtime = AgentRuntime(root / "runtime", workflow=workflow)  # type: ignore[arg-type]
            first = runtime.run(RunRequest("paper-to-article", {"paper_url": "https://arxiv.org/abs/1706.03762"}))
            before = runtime.get_run(first.run_id)
            article_md = next(
                item["path"]
                for item in before["artifacts"]
                if item["producer"] == "write" and item["path"].endswith(".md") and "publish_package" not in item["path"]
            )
            runtime.update_inputs(
                first.run_id,
                {"input_article": article_md, "revision_instruction": "标题更克制", "revision_number": 2},
                restart_step="write",
            )
            failed = runtime.execute(first.run_id)
            manifest = runtime.get_run(first.run_id)
            self.assertEqual(failed.status, "failed")
            self.assertTrue(manifest["fallback_revision_active"])
            active_html = [
                item
                for item in manifest["artifacts"]
                if item["producer"] == "write" and item["path"].endswith(".html") and item["status"] != "stale"
            ]
            self.assertEqual(len(active_html), 1)

    def test_paper_to_article_with_fake_providers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = FakePaperWorkflow(root)
            runtime = AgentRuntime(root / "runtime", workflow=workflow)  # type: ignore[arg-type]
            result = runtime.run(
                RunRequest("paper-to-article", {"paper_url": "https://arxiv.org/abs/1706.03762"})
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.quality["technical_score"], 92)
            self.assertGreaterEqual(len(result.artifacts), 8)
            self.assertNotIn("paper.collect", [event.payload.get("tool") for event in runtime.events(result.run_id)])

    def test_real_wechat_waits_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = FakePaperWorkflow(root)
            runtime = AgentRuntime(root / "runtime", workflow=workflow)  # type: ignore[arg-type]
            result = runtime.run(
                RunRequest("paper-to-wechat", {"paper_url": "https://arxiv.org/abs/1706.03762"}, dry_run=False)
            )
            self.assertEqual(result.status, "waiting_approval")
            self.assertEqual(workflow.publish_calls, 0)
            runtime.resolve_approval(result.run_id, "approve-publish", True)
            completed = runtime.resume(result.run_id)
            self.assertEqual(completed.status, "completed")
            self.assertEqual(workflow.publish_calls, 1)
            runtime.resume(result.run_id)
            self.assertEqual(workflow.publish_calls, 1)

    def test_existing_article_draft_requires_approval_without_rewriting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "article.json"
            article.write_text("{}", encoding="utf-8")
            workflow = FakePaperWorkflow(root)
            runtime = AgentRuntime(root / "runtime", workflow=workflow)  # type: ignore[arg-type]
            waiting = runtime.run(
                RunRequest(
                    "publish-existing",
                    {"article_json": str(article), "quality": {"publish_ready": True}},
                    dry_run=False,
                )
            )
            self.assertEqual(waiting.status, "waiting_approval")
            self.assertEqual(workflow.writing_calls, [])
            runtime.resolve_approval(waiting.run_id, "approve-publish", True)
            completed = runtime.resume(waiting.run_id)
            self.assertEqual(completed.status, "completed")
            self.assertEqual(workflow.publish_calls, 1)

    def test_legacy_outputs_are_isolated_in_runtime_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = FakePaperWorkflow(root)
            runtime_workspace = root / "runtime"
            runtime = AgentRuntime(runtime_workspace, workflow=workflow)  # type: ignore[arg-type]
            result = runtime.run(
                RunRequest("paper-to-article", {"paper_url": "https://arxiv.org/abs/1706.03762"})
            )
            self.assertEqual(result.status, "completed")
            self.assertTrue((runtime_workspace / "data" / "parsed" / "1706.03762.json").exists())
            self.assertTrue(all(str(item["path"]).startswith(str(runtime_workspace.resolve() / "runs")) for item in result.artifacts))

    def test_model_timeout_keeps_article_run_but_still_blocks_wechat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = ReviewRequiredWorkflow(root)
            article_runtime = AgentRuntime(root / "article-runtime", workflow=workflow)  # type: ignore[arg-type]
            article = article_runtime.run(
                RunRequest("paper-to-article", {"paper_url": "https://arxiv.org/abs/1706.03762"})
            )
            self.assertEqual(article.status, "completed")
            self.assertFalse(article.quality["content_ready"])

            wechat_runtime = AgentRuntime(root / "wechat-runtime", workflow=workflow)  # type: ignore[arg-type]
            wechat = wechat_runtime.run(
                RunRequest("paper-to-wechat", {"paper_url": "https://arxiv.org/abs/1706.03762"})
            )
            self.assertEqual(wechat.status, "failed")
            self.assertEqual(wechat_runtime.get_run(wechat.run_id)["failure_code"], 6)
            self.assertEqual(workflow.publish_calls, 0)


if __name__ == "__main__":
    unittest.main()
