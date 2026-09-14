from __future__ import annotations

import unittest

from smearglepaper.tui_presentation import build_run_presentation, compact_artifact_name


def _manifest(workflow: str = "paper-research", status: str = "completed") -> dict[str, object]:
    return {
        "workflow": workflow,
        "status": status,
        "request": {"inputs": {"topic": "agents"}, "dry_run": True},
        "steps": [
            {"id": "select", "status": "completed", "error": None},
            {"id": "ingest", "status": "completed", "error": None},
        ],
        "artifacts": [],
        "quality": {},
    }


class RunPresentationTests(unittest.TestCase):
    def test_compact_artifact_name_preserves_extension(self) -> None:
        compact = compact_artifact_name("package-write-publish_package.md", 23)
        self.assertLessEqual(len(compact), 23)
        self.assertTrue(compact.endswith(".md"))

    def test_workflow_headlines(self) -> None:
        expected = {
            "paper-research": "论文证据已准备好",
            "paper-to-article": "文章已生成，等待你审阅",
            "paper-to-wechat": "公众号发布包已准备好",
            "daily-digest": "研究日报已生成",
        }
        for workflow, headline in expected.items():
            with self.subTest(workflow=workflow):
                self.assertEqual(build_run_presentation(_manifest(workflow)).headline, headline)

    def test_subject_prefers_write_then_ingest_then_select(self) -> None:
        checkpoints = {
            "select": {"output": {"paper": {"title": "Selected title"}}},
            "ingest": {"output": {"paper": {"title": "Ingested title"}}},
            "write": {"output": {"paper": {"title": "Written title"}}},
        }
        presentation = build_run_presentation(_manifest(), checkpoints)
        self.assertEqual(presentation.subject, "Written title")

    def test_artifacts_hide_checkpoints_dedupe_and_prioritize_outputs(self) -> None:
        manifest = _manifest("paper-to-article")
        manifest["artifacts"] = [
            {"path": "/runs/write-write.json", "producer": "write", "type": "application/json"},
            {"path": "/runs/write-article.md", "producer": "write", "type": "text/markdown"},
            {"path": "/elsewhere/write-article.md", "producer": "write", "type": "text/markdown"},
            {"path": "/runs/package-article.html", "producer": "package", "type": "text/html"},
            {"path": "/runs/ingest-paper.json", "producer": "ingest", "type": "application/json"},
            {"path": "/runs/rank-latest.json", "producer": "rank", "type": "application/json"},
        ]
        presentation = build_run_presentation(manifest)
        self.assertEqual(
            [item.name for item in presentation.artifacts],
            ["package-article.html", "write-article.md", "ingest-paper.json", "rank-latest.json"],
        )
        self.assertNotIn("write-write.json", [item.name for item in presentation.artifacts])

    def test_failed_run_uses_step_error_and_recovery_action(self) -> None:
        manifest = _manifest(status="failed")
        manifest["steps"] = [
            {"id": "ingest", "status": "failed", "error": {"message": "PDF download failed"}}
        ]
        presentation = build_run_presentation(manifest)
        self.assertIn("PDF 下载或解析失败", presentation.headline)
        self.assertEqual(presentation.error, "PDF download failed")
        self.assertEqual(presentation.next_action, "检查失败步骤后重试")
        self.assertIn("进度 0/1 步", presentation.summary_text)

    def test_completed_article_exposes_user_phase_verdict_and_actions(self) -> None:
        manifest = _manifest("paper-to-article")
        manifest["quality"] = {"content_ready": True, "publish_ready": False, "issues": ["实验数据缺少出处"]}
        presentation = build_run_presentation(manifest)
        self.assertEqual(presentation.phase, "文章已完成")
        self.assertEqual(presentation.quality_verdict, "建议修改")
        self.assertEqual(presentation.issues, ("实验数据缺少出处",))
        self.assertEqual([action.id for action in presentation.actions], ["revise", "preview", "open", "draft"])
        self.assertFalse(presentation.actions[-1].enabled)

    def test_waiting_input_presents_candidate_selection(self) -> None:
        manifest = _manifest("paper-to-article", status="waiting_input")
        manifest["interaction"] = {
            "type": "candidate_selection",
            "status": "pending",
            "options": [{"paper_id": "2601.00001", "title": "Candidate"}],
        }
        presentation = build_run_presentation(manifest)
        self.assertEqual(presentation.phase, "选择材料")
        self.assertEqual(presentation.headline, "请选择一篇论文继续")
        self.assertEqual(presentation.actions[0].id, "select")


if __name__ == "__main__":
    unittest.main()
