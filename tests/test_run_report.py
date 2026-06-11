from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smearglepaper.run_report import RunReport, generate_run_id
from smearglepaper.cli import build_parser


class RunReportTests(unittest.TestCase):
    def test_run_id_format(self) -> None:
        run_id = generate_run_id()
        self.assertTrue(run_id.startswith("run_"))
        # Format: run_YYYYMMDD_HHMMSS
        parts = run_id.split("_")
        self.assertEqual(len(parts), 3)  # run, date, time
        self.assertEqual(len(parts[1]), 8)  # YYYYMMDD
        self.assertEqual(len(parts[2]), 6)  # HHMMSS

    def test_run_report_json_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("smearglepaper.run_report.DATA_DIR", Path(tmp)):
                report = RunReport(run_id="run_test_001", workflow="test", query="test query", dry_run=True)
                step = report.start_step("test-step")
                report.complete_step(step, "success", artifacts=["data/test.json"])
                report.finish("success", next_action="Done.")
                report.save()

                json_path = Path(tmp) / "runs" / "run_test_001" / "run_report.json"
                self.assertTrue(json_path.exists())

                data = json.loads(json_path.read_text())
                self.assertEqual(data["run_id"], "run_test_001")
                self.assertEqual(data["workflow"], "test")
                self.assertEqual(data["status"], "success")
                self.assertTrue(data["dry_run"])
                self.assertEqual(len(data["steps"]), 1)
                self.assertEqual(data["steps"][0]["name"], "test-step")
                self.assertEqual(data["steps"][0]["status"], "success")

    def test_run_report_md_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("smearglepaper.run_report.DATA_DIR", Path(tmp)):
                report = RunReport(run_id="run_test_002", workflow="test", query="test query")
                step = report.start_step("collect-arxiv")
                report.complete_step(step, "success", artifacts=["data/papers/latest.json"])
                report.finish("success")
                report.save()

                md_path = Path(tmp) / "runs" / "run_test_002" / "run_report.md"
                self.assertTrue(md_path.exists())

                content = md_path.read_text()
                self.assertIn("Step Timeline", content)
                self.assertIn("collect-arxiv", content)
                self.assertIn("success", content)

    def test_resume_skipped_steps_recorded(self) -> None:
        report = RunReport(run_id="run_test_003", workflow="agent-run", resume=True)
        step = report.start_step("collect-arxiv")
        report.complete_step(step, "skipped", "artifact_exists", ["data/papers/latest.json"])
        report.finish("success")

        data = report.to_dict()
        self.assertTrue(data["resume"])
        self.assertEqual(data["steps"][0]["status"], "skipped")
        self.assertEqual(data["steps"][0]["reason"], "artifact_exists")

    def test_quality_gate_recorded(self) -> None:
        report = RunReport(run_id="run_test_004", workflow="agent-run")
        step = report.start_step("review-article")
        report.complete_step(
            step,
            "failed_quality_gate",
            "score_below_threshold",
            ["data/reviews/test.json"],
            quality={"score": 45, "threshold": 60, "pass": False},
        )
        report.finish("success")

        data = report.to_dict()
        self.assertEqual(data["steps"][0]["status"], "failed_quality_gate")
        self.assertEqual(data["steps"][0]["quality"]["score"], 45)
        self.assertFalse(data["steps"][0]["quality"]["pass"])

    def test_improve_triggered_by_review_failure(self) -> None:
        report = RunReport(run_id="run_test_005", workflow="agent-run")
        review_step = report.start_step("review-article")
        report.complete_step(review_step, "failed_quality_gate", "score_below_threshold",
                             quality={"score": 45, "threshold": 60, "pass": False})
        improve_step = report.start_step("improve-article")
        report.complete_step(improve_step, "success", "triggered_by_review_failure",
                             ["data/articles/test.optimized.json"])
        report.finish("success")

        data = report.to_dict()
        self.assertEqual(data["steps"][1]["reason"], "triggered_by_review_failure")

    def test_dry_run_recorded(self) -> None:
        report = RunReport(run_id="run_test_006", workflow="agent-run", dry_run=True)
        report.finish("success")

        data = report.to_dict()
        self.assertTrue(data["dry_run"])

    def test_step_timing(self) -> None:
        report = RunReport(run_id="run_test_007", workflow="test")
        step = report.start_step("test-step")
        report.complete_step(step, "success")

        self.assertIsNotNone(step.started_at)
        self.assertIsNotNone(step.ended_at)
        self.assertGreaterEqual(step.duration_seconds, 0)

    def test_error_recorded(self) -> None:
        report = RunReport(run_id="run_test_008", workflow="test")
        step = report.start_step("failing-step")
        report.complete_step(step, "failed", error={
            "type": "RuntimeError",
            "message": "Something went wrong",
            "retryable": True,
        })
        report.finish("failed")

        data = report.to_dict()
        self.assertEqual(data["steps"][0]["status"], "failed")
        self.assertEqual(data["steps"][0]["error"]["type"], "RuntimeError")
        self.assertTrue(data["steps"][0]["error"]["retryable"])

    def test_wechat_recorded(self) -> None:
        report = RunReport(run_id="run_test_009", workflow="agent-run", dry_run=True)
        report.wechat = {"draft_created": True, "draft_id": "MEDIA_123", "published": False, "dry_run": True}
        report.finish("success")

        data = report.to_dict()
        self.assertTrue(data["wechat"]["draft_created"])
        self.assertEqual(data["wechat"]["draft_id"], "MEDIA_123")
        self.assertFalse(data["wechat"]["published"])

    def test_markdown_includes_failed_steps(self) -> None:
        report = RunReport(run_id="run_test_010", workflow="test")
        step = report.start_step("review-article")
        report.complete_step(step, "failed_quality_gate", "score_below_threshold",
                             quality={"score": 40, "threshold": 60, "pass": False})
        report.finish("failed", next_action="Fix the article quality.")

        md = report.to_markdown()
        self.assertIn("Failed Steps", md)
        self.assertIn("review-article", md)
        self.assertIn("score_below_threshold", md)


class CLIAliasTests(unittest.TestCase):
    def test_check_agents_alias_to_check_status(self) -> None:
        """check-agents should resolve to check-status via alias."""
        args = build_parser().parse_args(["check-agents"])
        # After alias resolution, _alias_target should be check-status
        self.assertEqual(getattr(args, "_alias_target", None), "check-status")

    def test_publish_wechat_alias_to_create_wechat_draft(self) -> None:
        """publish-wechat should resolve to create-wechat-draft via alias."""
        args = build_parser().parse_args(["publish-wechat", "--article-json", "test.json", "--draft-only"])
        self.assertEqual(getattr(args, "_alias_target", None), "create-wechat-draft")

    def test_agent_check_alias_to_check_status(self) -> None:
        """agent-check should resolve to check-status via alias."""
        args = build_parser().parse_args(["agent-check"])
        self.assertEqual(getattr(args, "_alias_target", None), "check-status")

    def test_daily_digest_resume_flag(self) -> None:
        """daily-digest should accept --resume flag."""
        args = build_parser().parse_args(["daily-digest", "--resume"])
        self.assertTrue(args.resume)

    def test_agent_run_resume_flag(self) -> None:
        """agent-run should accept --resume flag."""
        args = build_parser().parse_args(["agent-run", "--resume"])
        self.assertTrue(args.resume)


if __name__ == "__main__":
    unittest.main()
