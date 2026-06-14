from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from smearglepaper.models import PaperMeta
from smearglepaper.agent_reviews import technical_review, wechat_review
from smearglepaper.storage import read_json
from smearglepaper.writing_agent import PaperWritingAgent, _apply_guardrail_repairs, build_evidence_packet


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


def _parsed() -> dict[str, object]:
    return {
        "paper": _paper().to_dict(),
        "text": "1 Introduction\nWe propose a reliable workflow. Experiments show 91.2 accuracy.",
        "sections": [{"id": "1", "title": "Introduction", "page_start": 1, "page_end": 1}],
        "evidence_ledger": [
            {
                "kind": "result",
                "page": 1,
                "section_title": "Introduction",
                "excerpt": "Experiments show 91.2 accuracy.",
                "visual_ids": [],
            }
        ],
        "visuals": [],
    }


class WritingAgentTests(unittest.TestCase):
    def test_evidence_packet_contains_notes_and_traceable_evidence(self) -> None:
        packet = build_evidence_packet(_paper(), _parsed(), "My note")
        self.assertIn("My note", packet)
        self.assertIn("第 1 页", packet)
        self.assertIn("91.2 accuracy", packet)

    def test_guardrail_repairs_preserve_boundary_and_migration_sections(self) -> None:
        repaired = _apply_guardrail_repairs("# Title\n\n## 后续影响\n\n后来影响很大。")
        self.assertIn("并非原论文自身的实验结论", repaired)
        self.assertIn("项目迁移", repaired)

    def test_guardrail_repairs_risky_title(self) -> None:
        repaired = _apply_guardrail_repairs("# Transformer全解析：纯注意力架构\n\n正文", _paper())
        self.assertTrue(repaired.startswith("# 重读《Reliable Agent Workflows》"))
        self.assertIn("究竟解决了什么问题", repaired)

    def test_wechat_review_accepts_blockquote_opening(self) -> None:
        markdown = (
            "# 一个克制且明确的问题标题\n\n"
            "> 为什么这个问题值得读？本文将解释核心证据。\n\n"
            "## 问题\n正文\n## 设计\n正文\n## 证据\n正文\n## 边界\n论文没有证明全部任务。\n"
            "## 总结\n结论。\n## 面试\n项目迁移。"
        )
        report, _ = wechat_review(markdown)
        self.assertFalse(report["opening_suggestions"])

    def test_agent_persists_stages_and_revises_until_target(self) -> None:
        writer = Mock()
        writer._call_model.side_effect = [
            "# Strategy\n\nUse the evidence.",
            "# First draft\n\nhttps://arxiv.org/abs/2401.00001",
            "# Revised evidence-first article\n\nhttps://arxiv.org/abs/2401.00001",
        ]
        first_technical = {"score": 60, "blocking_issues": ["unsupported"], "revision_instructions": ["fix"]}
        second_technical = {"score": 92, "blocking_issues": [], "revision_instructions": []}
        first_wechat = {"score": 80, "revision_instructions": ["improve opening"]}
        second_wechat = {"score": 90, "revision_instructions": []}

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with (
                patch("smearglepaper.writing_agent.DATA_DIR", data_dir),
                patch("smearglepaper.writing_agent.create_cover", return_value=str(data_dir / "cover.png")),
                patch(
                    "smearglepaper.writing_agent.technical_review",
                    side_effect=[(first_technical, "# Technical 1"), (second_technical, "# Technical 2")],
                ),
                patch(
                    "smearglepaper.writing_agent.wechat_review",
                    side_effect=[(first_wechat, "# WeChat 1"), (second_wechat, "# WeChat 2")],
                ),
            ):
                result = PaperWritingAgent(writer=writer, model_available=True).run(
                    _paper(),
                    _parsed(),
                    target_score=85,
                    max_revisions=2,
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(len(result["attempts"]), 2)
            self.assertEqual(result["final"]["score"], 90)
            self.assertEqual(result["final"]["technical_score"], 92)
            self.assertEqual(result["final"]["wechat_score"], 90)
            self.assertTrue(Path(str(result["manifest"])).exists())
            self.assertTrue(Path(str(result["final"]["article_json"])).exists())
            self.assertTrue(Path(str(result["final"]["publish_package"])).exists())
            run_dir = Path(str(result["run_dir"]))
            self.assertTrue((run_dir / "diagnosis" / "article_diagnosis.md").exists())
            self.assertTrue((run_dir / "intermediate" / "style_report.md").exists())
            self.assertTrue((run_dir / "intermediate" / "outline.md").exists())
            self.assertTrue((run_dir / "drafts" / "draft_v1.md").exists())
            self.assertTrue((run_dir / "drafts" / "draft_v2.md").exists())
            self.assertTrue((run_dir / "reviews" / "technical_review.md").exists())
            self.assertTrue((run_dir / "reviews" / "wechat_review.md").exists())
            self.assertTrue((run_dir / "reviews" / "revision_report.md").exists())
            manifest = read_json(Path(str(result["manifest"])), {})
            self.assertEqual(manifest["agent"], "Paper Writing Agent")
            self.assertEqual(writer._call_model.call_count, 3)

    def test_agent_persists_failed_llm_stage(self) -> None:
        writer = Mock()
        writer._call_model.side_effect = RuntimeError("provider timeout")

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            with (
                patch("smearglepaper.writing_agent.DATA_DIR", data_dir),
                self.assertRaisesRegex(RuntimeError, "provider timeout"),
            ):
                PaperWritingAgent(writer=writer, model_available=True).run(_paper(), _parsed())

            manifests = list((Path(tmp) / "workspace" / "paper_writing").glob("*/manifest.json"))
            self.assertEqual(len(manifests), 1)
            manifest = read_json(manifests[0], {})
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(manifest["active_stage"], "outline-planner")
            self.assertEqual(manifest["error"]["type"], "RuntimeError")

    def test_transformer_technical_review_flags_required_risks(self) -> None:
        paper = _paper()
        paper.paper_id = "1706.03762"
        markdown = "# Transformer\n\n纯注意力模型。Table 3 和 Table 4 说明模型可以全面推广。\n\n后续影响：后来成为 GPT 的基础。"
        report, _ = technical_review(markdown, paper, _parsed())
        messages = " ".join(report["minor_issues"])
        self.assertIn("FFN", messages)
        self.assertIn("自回归生成并行", messages)
        self.assertIn("Table 3 和 Table 4", messages)
        self.assertIn("后续影响", messages)

    def test_wechat_review_flags_overbroad_analysis_title(self) -> None:
        report, _ = wechat_review("# 拥抱注意力：Transformer架构的全面解析\n\n为什么它重要？")
        self.assertTrue(report["title_suggestions"])

    def test_reviews_block_truncated_article_and_local_images(self) -> None:
        markdown = "# 为什么Transformer能取代RNN？\n\n## 结论\n\n![图](C:\\tmp\\figure.png)\n\n* **能"
        technical, _ = technical_review(markdown, _paper(), _parsed())
        wechat, _ = wechat_review(markdown)

        self.assertTrue(any("截断" in issue for issue in technical["blocking_issues"]))
        self.assertTrue(wechat["title_suggestions"])
        self.assertTrue(wechat["publication_blocking_issues"])

    def test_technical_review_does_not_treat_marked_history_as_original_claim(self) -> None:
        markdown = (
            "# 一个克制的论文标题\n\n"
            "论文没有证明所有任务都有效，结论存在边界。\n\n"
            "## 历史影响\n\n"
            "以下并非原论文结论。后续工作彻底改变了研究范式。"
        )
        report, _ = technical_review(markdown, _paper(), _parsed())
        self.assertFalse(any("过度结论" in issue for issue in report["minor_issues"]))

    def test_technical_review_does_not_match_final_result_as_ending_claim(self) -> None:
        markdown = "# 克制标题\n\n论文没有证明所有任务都有效，结论存在边界。输出最终结果。"
        report, _ = technical_review(markdown, _paper(), _parsed())
        self.assertFalse(any("过度结论" in issue for issue in report["minor_issues"]))

    def test_agent_revises_when_scores_pass_but_review_instructions_remain(self) -> None:
        writer = Mock()
        writer._call_model.side_effect = ["# Strategy", "# Draft", "# Revised"]
        first_technical = {"score": 90, "blocking_issues": [], "revision_instructions": ["fix scope"]}
        clean_technical = {"score": 90, "blocking_issues": [], "revision_instructions": []}
        first_wechat = {"score": 90, "revision_instructions": []}
        clean_wechat = {"score": 90, "revision_instructions": []}

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            with (
                patch("smearglepaper.writing_agent.DATA_DIR", data_dir),
                patch("smearglepaper.writing_agent.create_cover", return_value=str(data_dir / "cover.png")),
                patch(
                    "smearglepaper.writing_agent.technical_review",
                    side_effect=[(first_technical, "# Review"), (clean_technical, "# Review")],
                ),
                patch(
                    "smearglepaper.writing_agent.wechat_review",
                    side_effect=[(first_wechat, "# Review"), (clean_wechat, "# Review")],
                ),
            ):
                result = PaperWritingAgent(writer=writer, model_available=True).run(
                    _paper(), _parsed(), target_score=85, max_revisions=2
                )

        self.assertEqual(len(result["attempts"]), 2)
        self.assertTrue(result["final"]["ready"])

    def test_agent_does_not_rewrite_only_for_local_image_publish_blocker(self) -> None:
        writer = Mock()
        writer._call_model.side_effect = ["# Strategy", "# Draft"]
        technical = {"score": 90, "blocking_issues": [], "revision_instructions": []}
        wechat = {
            "score": 90,
            "revision_instructions": [],
            "publication_blocking_issues": ["replace local image"],
        }

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            with (
                patch("smearglepaper.writing_agent.DATA_DIR", data_dir),
                patch("smearglepaper.writing_agent.create_cover", return_value=str(data_dir / "cover.png")),
                patch("smearglepaper.writing_agent.technical_review", return_value=(technical, "# Review")),
                patch("smearglepaper.writing_agent.wechat_review", return_value=(wechat, "# Review")),
            ):
                result = PaperWritingAgent(writer=writer, model_available=True).run(
                    _paper(), _parsed(), target_score=85, max_revisions=3
                )

        self.assertEqual(len(result["attempts"]), 1)
        self.assertEqual(writer._call_model.call_count, 2)
        self.assertEqual(result["status"], "needs_human_review")
        self.assertTrue(result["final"]["content_ready"])
        self.assertFalse(result["final"]["publish_ready"])

    def test_agent_uploads_images_after_content_is_ready(self) -> None:
        writer = Mock()
        writer._call_model.side_effect = ["# Strategy", "# Complete article\n\n![图](C:\\tmp\\figure.png)"]
        technical = {"score": 90, "blocking_issues": [], "revision_instructions": []}
        before_upload = {
            "score": 90,
            "revision_instructions": [],
            "publication_blocking_issues": ["replace local image"],
        }
        after_upload = {"score": 90, "revision_instructions": [], "publication_blocking_issues": []}
        client = Mock()
        client.upload_markdown_images.return_value = (
            "# Complete article\n\n![图](https://mmbiz.qpic.cn/figure.png)",
            {"C:\\tmp\\figure.png": "https://mmbiz.qpic.cn/figure.png"},
        )

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            with (
                patch("smearglepaper.writing_agent.DATA_DIR", data_dir),
                patch("smearglepaper.writing_agent.create_cover", return_value=str(data_dir / "cover.png")),
                patch(
                    "smearglepaper.writing_agent.technical_review",
                    side_effect=[(technical, "# Review"), (technical, "# Review")],
                ),
                patch(
                    "smearglepaper.writing_agent.wechat_review",
                    side_effect=[(before_upload, "# Review"), (after_upload, "# Review")],
                ),
                patch("smearglepaper.writing_agent.WechatClient", return_value=client),
            ):
                result = PaperWritingAgent(writer=writer, model_available=True).run(
                    _paper(), _parsed(), target_score=85, max_revisions=0, upload_images=True
                )
            final_markdown = Path(str(result["final"]["markdown"])).read_text(encoding="utf-8")

        self.assertTrue(result["final"]["content_ready"])
        self.assertTrue(result["final"]["publish_ready"])
        self.assertEqual(result["final"]["asset_upload"]["replacement_count"], 1)
        self.assertIn("https://mmbiz.qpic.cn/figure.png", final_markdown)


if __name__ == "__main__":
    unittest.main()
