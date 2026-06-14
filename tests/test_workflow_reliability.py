from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from smearglepaper.models import Article, PaperMeta
from smearglepaper.reader import PaperReader, extract_pdf_text_pages, extract_pdf_visuals
from smearglepaper.run_report import RunReport
from smearglepaper.storage import read_json
from smearglepaper.workflow import SmearglePaperWorkflow


def _paper() -> PaperMeta:
    return PaperMeta(
        paper_id="2401.00001",
        title="Reliable Agent Workflows",
        authors=["Ada"],
        abstract="A paper about reliable workflows.",
        source="arxiv",
        url="https://arxiv.org/abs/2401.00001",
        pdf_url="https://arxiv.org/pdf/2401.00001",
        published_at="2026-06-01T00:00:00Z",
    )


class ReaderReliabilityTests(unittest.TestCase):
    def test_pdf_failure_raises_without_writing_parsed_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with (
                patch("smearglepaper.reader.DATA_DIR", data_dir),
                patch("smearglepaper.reader.download_file", side_effect=OSError("network down")),
            ):
                with self.assertRaisesRegex(RuntimeError, "PDF download/parse failed"):
                    PaperReader().read(_paper())

            self.assertFalse((data_dir / "parsed" / "2401.00001.json").exists())

    def test_pdf_extraction_keeps_all_pages_and_structured_table_details(self) -> None:
        import fitz

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            pdf_path = data_dir / "paper.pdf"
            document = fitz.open()
            for page_number in range(1, 3):
                page = document.new_page()
                page.insert_text((72, 72), f"Page {page_number} evidence")
                if page_number == 2:
                    page.insert_text((72, 110), "Table 1: Main results")
                    for x in (72, 180, 288):
                        page.draw_line((x, 130), (x, 210))
                    for y in (130, 170, 210):
                        page.draw_line((72, y), (288, y))
                    page.insert_text((85, 155), "Model A")
                    page.insert_text((195, 155), "90.0")
            document.save(pdf_path)
            document.close()

            text, pages = extract_pdf_text_pages(pdf_path)
            with patch("smearglepaper.reader.DATA_DIR", data_dir):
                visuals = extract_pdf_visuals(pdf_path, "test", max_figures=0, max_tables=2)

            self.assertEqual(len(pages), 2)
            self.assertIn("Page 2 evidence", text)
            self.assertEqual(visuals[0]["kind"], "table")
            self.assertEqual(visuals[0]["page"], 2)
            self.assertIn("Table 1", visuals[0]["caption"])
            self.assertTrue(Path(str(visuals[0]["path"])).exists())


class WorkflowReliabilityTests(unittest.TestCase):
    def test_write_article_preserves_arxiv_id_dots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            workflow = SmearglePaperWorkflow()
            with (
                patch("smearglepaper.workflow.DATA_DIR", data_dir),
                patch.object(workflow.writer, "write", return_value="# Test article"),
                patch("smearglepaper.workflow.create_cover", return_value=str(data_dir / "cover.png")),
            ):
                result = workflow.write_article(_paper(), parsed={})

            self.assertEqual(result["article_json"], str(data_dir / "articles" / "2401.00001.json"))
            self.assertTrue((data_dir / "articles" / "2401.00001.md").exists())

    def test_write_article_uses_structured_visual_caption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            workflow = SmearglePaperWorkflow()
            visual_path = data_dir / "table.png"
            visual_path.write_bytes(b"image")
            parsed = {
                "figures": [str(visual_path)],
                "visuals": [
                    {
                        "kind": "table",
                        "page": 8,
                        "caption": "Table 2: Main results",
                        "path": str(visual_path),
                    }
                ],
            }
            with (
                patch("smearglepaper.workflow.DATA_DIR", data_dir),
                patch.object(workflow.writer, "write", return_value="# Test\n\n## 实验\n\n见 Table 2。"),
                patch("smearglepaper.workflow.create_cover", return_value=str(data_dir / "cover.png")),
            ):
                result = workflow.write_article(_paper(), parsed=parsed)

            markdown = Path(str(result["markdown"])).read_text(encoding="utf-8")
            self.assertIn("Table 2: Main results，原论文第 8 页", markdown)

    def test_run_writing_agent_loads_parsed_paper_and_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            parsed_path = data_dir / "parsed" / "2401.00001.json"
            parsed_path.parent.mkdir(parents=True)
            parsed_path.write_text('{"paper":' + __import__("json").dumps(_paper().to_dict()) + ',"text":"evidence"}', encoding="utf-8")
            notes_path = data_dir / "notes.md"
            notes_path.write_text("reader notes", encoding="utf-8")
            agent = Mock()
            agent.run.return_value = {"status": "success"}
            with (
                patch("smearglepaper.workflow.DATA_DIR", data_dir),
                patch("smearglepaper.writing_agent.PaperWritingAgent", return_value=agent),
            ):
                result = SmearglePaperWorkflow().run_writing_agent("2401.00001", notes_path=notes_path)

            self.assertEqual(result["status"], "success")
            self.assertEqual(agent.run.call_args.kwargs["notes"], "reader notes")

    def test_run_writing_agent_accepts_url_and_ingests_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            parsed_path = data_dir / "parsed" / "2401.00001.json"
            parsed_path.parent.mkdir(parents=True)

            def ingest(_paper_meta: PaperMeta) -> dict[str, object]:
                parsed_path.write_text(
                    '{"paper":' + __import__("json").dumps(_paper().to_dict()) + ',"text":"evidence"}',
                    encoding="utf-8",
                )
                return {"parsed": str(parsed_path)}

            workflow = SmearglePaperWorkflow()
            agent = Mock()
            agent.run.return_value = {"status": "success"}
            with (
                patch("smearglepaper.workflow.DATA_DIR", data_dir),
                patch.object(workflow, "_resolve_paper", return_value=_paper()) as resolve,
                patch.object(workflow, "read", side_effect=ingest) as read,
                patch("smearglepaper.writing_agent.PaperWritingAgent", return_value=agent),
            ):
                result = workflow.run_writing_agent(
                    paper_url="https://arxiv.org/abs/2401.00001",
                    paper_title="Display title",
                )

            self.assertEqual(result["status"], "success")
            resolve.assert_called_once()
            read.assert_called_once()
            self.assertEqual(agent.run.call_args.args[0].title, "Display title")

    def test_prepare_agent_assets_promotes_content_ready_article(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            image = data_dir / "figure.png"
            image.write_bytes(b"image")
            article_path = data_dir / "draft.json"
            article = Article(
                paper=_paper(),
                title="A careful title",
                digest="digest",
                markdown=f"# A careful title\n\nWhy does it work?\n\n![Figure]({image})",
                html="",
                figure_paths=[str(image)],
            )
            article_path.write_text(__import__("json").dumps(article.to_dict()), encoding="utf-8")
            client = Mock()
            client.upload_markdown_images.return_value = (
                "# A careful title\n\nWhy does it work?\n\n![Figure](https://mmbiz.qpic.cn/figure.png)",
                {str(image): "https://mmbiz.qpic.cn/figure.png"},
            )
            technical = {"score": 90, "blocking_issues": [], "revision_instructions": []}
            before = {"score": 90, "revision_instructions": [], "publication_blocking_issues": [str(image)]}
            after = {"score": 90, "revision_instructions": [], "publication_blocking_issues": []}
            with (
                patch("smearglepaper.workflow.DATA_DIR", data_dir),
                patch("smearglepaper.workflow.WechatClient", return_value=client),
                patch("smearglepaper.agent_reviews.technical_review", side_effect=[(technical, ""), (technical, "")]),
                patch("smearglepaper.agent_reviews.wechat_review", side_effect=[(before, ""), (after, "")]),
            ):
                result = SmearglePaperWorkflow().prepare_agent_assets(article_path)

            self.assertTrue(result["publish_ready"])
            self.assertEqual(result["replacement_count"], 1)
            self.assertTrue((data_dir / "articles" / "2401.00001.publish-ready.md").exists())

    def test_agent_run_saves_initial_report_before_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            workflow = SmearglePaperWorkflow()
            with (
                patch("smearglepaper.run_report.DATA_DIR", data_dir),
                patch.object(workflow, "_run_agent_pipeline", side_effect=RuntimeError("stopped")),
            ):
                with self.assertRaisesRegex(RuntimeError, "stopped"):
                    workflow.agent_run(None, None, None, 7, 1, 10, False, False, False, True)

            reports = list((data_dir / "runs").glob("*/run_report.json"))
            self.assertEqual(len(reports), 1)

    def test_review_article_writes_review_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            article_path = data_dir / "articles" / "test.json"
            article_path.parent.mkdir(parents=True)
            article_path.write_text(
                '{"paper":{"paper_id":"test","title":"Test","authors":[],"abstract":"","source":"local",'
                '"url":"https://arxiv.org/abs/test","pdf_url":null,"published_at":""},'
                '"title":"A sufficiently detailed title","digest":"","markdown":"# A sufficiently detailed title",'
                '"html":"","figure_paths":[]}',
                encoding="utf-8",
            )
            with patch("smearglepaper.workflow.DATA_DIR", data_dir):
                result = SmearglePaperWorkflow().review_article(article_path)

            self.assertTrue((data_dir / "reviews" / "test.json").exists())
            self.assertEqual(result["output"], str(data_dir / "reviews" / "test.json"))

    def test_check_artifacts_returns_cli_command_as_next_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("smearglepaper.workflow.DATA_DIR", Path(tmp)):
                result = SmearglePaperWorkflow().check_artifacts("test")

            self.assertEqual(result["next_step"], "ingest-paper")
            self.assertIn("wechat_draft", result["artifacts"])

    def test_publish_existing_article_writes_draft_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            article_path = data_dir / "articles" / "test.json"
            article_path.parent.mkdir(parents=True)
            article_path.write_text(
                '{"paper":{"paper_id":"test","title":"Test","authors":[],"abstract":"","source":"local",'
                '"url":"https://arxiv.org/abs/test","pdf_url":null,"published_at":""},'
                '"title":"Test","digest":"","markdown":"# Test","html":"","figure_paths":[]}',
                encoding="utf-8",
            )
            with patch("smearglepaper.workflow.DATA_DIR", data_dir):
                result = SmearglePaperWorkflow().publish_existing_article(article_path, real_wechat=False, publish=False)

            self.assertTrue(result["ok"])
            self.assertTrue((data_dir / "wechat" / "dry_run" / "test.json").exists())

    def test_resume_rereviews_when_article_is_regenerated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            parsed_path = data_dir / "parsed" / "2401.00001.json"
            review_path = data_dir / "reviews" / "2401.00001.json"
            parsed_path.parent.mkdir(parents=True)
            review_path.parent.mkdir(parents=True)
            parsed_path.write_text("{}", encoding="utf-8")
            review_path.write_text('{"score": 10, "threshold": 60, "pass": false}', encoding="utf-8")
            workflow = SmearglePaperWorkflow()
            fresh_review = {"score": 80, "threshold": 60, "pass": True}
            with (
                patch("smearglepaper.workflow.DATA_DIR", data_dir),
                patch.object(workflow, "_resolve_paper", return_value=_paper()),
                patch.object(
                    workflow,
                    "write_article",
                    return_value={
                        "paper_id": _paper().paper_id,
                        "article_json": str(data_dir / "articles" / "2401.00001.json"),
                    },
                ),
                patch("smearglepaper.workflow.review_article_file", return_value=fresh_review) as review_call,
            ):
                report = RunReport(run_id="resume-review", workflow="agent-run", resume=True)
                workflow._run_agent_pipeline(report, None, None, _paper().url, 7, 1, 10, False, False, False, True, True)

            review_call.assert_called_once()
            review_step = next(step for step in report.steps if step.name == "review-article")
            self.assertEqual(review_step.status, "success")

    def _run_pipeline(
        self,
        data_dir: Path,
        *,
        review_passed: bool,
        improve: bool,
        create_draft: bool,
        optimized_review_passed: bool = True,
    ) -> tuple[RunReport, Mock]:
        workflow = SmearglePaperWorkflow()
        report = RunReport(run_id="run_reliability", workflow="agent-run", dry_run=True)
        article_json = data_dir / "articles" / "2401.00001.json"
        parsed_json = data_dir / "parsed" / "2401.00001.json"
        wechat_client = Mock()
        wechat_client.create_draft.return_value = {
            "ok": True,
            "action": "dry_run_create_draft",
            "media_id": "dry_run_media_id",
        }

        review = {"score": 80 if review_passed else 40, "threshold": 60, "pass": review_passed}
        optimized_json = data_dir / "articles" / "2401.00001.optimized.json"
        optimized_review = {
            "score": 85 if optimized_review_passed else 45,
            "threshold": 60,
            "pass": optimized_review_passed,
        }
        with (
            patch("smearglepaper.workflow.DATA_DIR", data_dir),
            patch.object(workflow, "_resolve_paper", return_value=_paper()),
            patch.object(workflow, "read", return_value={"parsed": str(parsed_json)}),
            patch.object(
                workflow,
                "write_article",
                return_value={"paper_id": _paper().paper_id, "article_json": str(article_json)},
            ),
            patch("smearglepaper.workflow.review_article_file", return_value=review),
            patch(
                "smearglepaper.workflow.improve_article_file",
                return_value={"outputs": {"json": str(optimized_json)}, "after": optimized_review},
            ),
            patch("smearglepaper.workflow.Article.from_dict", return_value=Mock()),
            patch("smearglepaper.workflow.WechatClient", return_value=wechat_client),
        ):
            workflow._run_agent_pipeline(
                report=report,
                topic=None,
                query=None,
                paper_url=_paper().url,
                days=7,
                top_k=1,
                max_results=10,
                collect_blogs=False,
                improve=improve,
                create_draft=create_draft,
                dry_run=True,
                resume=False,
            )

        return report, wechat_client

    def test_quality_gate_blocks_wechat_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report, client = self._run_pipeline(
                Path(tmp),
                review_passed=False,
                improve=False,
                create_draft=True,
            )

            client.create_draft.assert_not_called()
            draft_step = next(step for step in report.steps if step.name == "create-wechat-draft")
            self.assertEqual(draft_step.status, "skipped")
            self.assertEqual(draft_step.reason, "quality_gate_failed")
            self.assertFalse(report.article_quality["pass"])

    def test_wechat_result_is_written_and_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            report, client = self._run_pipeline(
                data_dir,
                review_passed=True,
                improve=False,
                create_draft=True,
            )

            client.create_draft.assert_called_once()
            wechat_path = data_dir / "wechat" / "2401.00001.json"
            self.assertTrue(wechat_path.exists())
            self.assertEqual(read_json(wechat_path, {})["media_id"], "dry_run_media_id")
            self.assertEqual(report.final_article_json, str(data_dir / "articles" / "2401.00001.json"))

    def test_failed_optimized_article_still_blocks_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report, client = self._run_pipeline(
                Path(tmp),
                review_passed=False,
                improve=True,
                create_draft=True,
                optimized_review_passed=False,
            )

            client.create_draft.assert_not_called()
            improve_step = next(step for step in report.steps if step.name == "improve-article")
            self.assertEqual(improve_step.status, "failed_quality_gate")
            self.assertEqual(improve_step.reason, "optimized_article_below_threshold")
            self.assertFalse(report.article_quality["pass"])


if __name__ == "__main__":
    unittest.main()
