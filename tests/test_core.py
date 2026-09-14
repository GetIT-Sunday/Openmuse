from __future__ import annotations

import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from smearglepaper.agents import AGENTS, check_agents
from smearglepaper.blogs import _parse_rss
from smearglepaper.cli import build_parser
from smearglepaper.collector import ArxivCollector, parse_arxiv_feed, topic_queries
from smearglepaper.editor import _load_paper_evidence, optimized_paths
from smearglepaper.evidence import audit_article_evidence, build_paper_structure, format_evidence_context
from smearglepaper.figures import place_visuals
from smearglepaper.llm import (
    ArticleWriter,
    chat_completions_base_url,
    format_visual_evidence,
    openai_request_headers,
)
from smearglepaper.models import Article, PaperMeta
from smearglepaper.quality import review_article_file
from smearglepaper.ranker import rank_papers
from smearglepaper.renderer import WechatRenderer
from smearglepaper.scout import _paper_review_row
from smearglepaper.tools import SYSTEM_PROMPT, TOOLS
from smearglepaper.workflow import append_figures


class CoreTests(unittest.TestCase):
    def test_parse_arxiv_feed(self) -> None:
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <id>http://arxiv.org/abs/2401.00001v1</id>
            <title> Test Paper </title>
            <summary> A useful abstract. </summary>
            <published>2026-05-20T00:00:00Z</published>
            <updated>2026-05-20T00:00:00Z</updated>
            <author><name>Ada Lovelace</name></author>
            <category term="cs.AI"/>
            <link title="pdf" href="http://arxiv.org/pdf/2401.00001v1"/>
          </entry>
        </feed>"""
        papers = parse_arxiv_feed(xml)
        self.assertEqual(papers[0].paper_id, "2401.00001v1")
        self.assertEqual(papers[0].authors, ["Ada Lovelace"])
        self.assertEqual(papers[0].pdf_url, "http://arxiv.org/pdf/2401.00001v1")

    def test_rank_papers_prefers_relevance(self) -> None:
        papers = [
            PaperMeta("a", "Graph Theory", [], "math", "arxiv", "", None, "2026-05-20T00:00:00Z", categories=["math.CO"]),
            PaperMeta("b", "Large Language Model Reasoning", [], "reasoning agent", "arxiv", "", None, "2026-05-20T00:00:00Z", categories=["cs.AI"]),
        ]
        rows = rank_papers(papers, query="large language model reasoning", top_k=1)
        self.assertEqual(rows[0]["paper"]["paper_id"], "b")

    def test_renderer_outputs_html(self) -> None:
        html = WechatRenderer().render("# 标题\n\n## 小节\n\n### 细节\n\n正文 **重点**\n\n![图](figure.png)\n\n*图注*")
        self.assertIn("<html", html)
        self.assertIn("标题", html)
        self.assertIn("max-width:100%", html)
        self.assertIn("#916dd5", html)
        self.assertIn("box-shadow:rgba(153,153,153,.3)", html)
        self.assertIn("text-decoration-color:#d89cf6", html)

    def test_append_figures(self) -> None:
        markdown = append_figures("# Title\n\n## 方法拆解\n\n正文", ["/tmp/a.png", "/tmp/b.png"])
        self.assertIn("论文核心图", markdown)
        self.assertIn("](/tmp/a.png)", markdown)
        self.assertLess(markdown.index("](/tmp/a.png)"), markdown.index("](/tmp/b.png)"))

    def test_place_visuals_uses_original_caption_instead_of_file_order(self) -> None:
        markdown = "# Title\n\n## 实验结果\n\n如 Table 2 所示，模型表现更好。"
        result = place_visuals(
            markdown,
            [
                {
                    "kind": "table",
                    "page": 8,
                    "caption": "Table 2: Main results",
                    "path": "/tmp/table_1.png",
                }
            ],
        )
        self.assertIn("Table 2: Main results，原论文第 8 页", result)
        self.assertNotIn("图 1：论文原图", result)

    def test_fetch_arxiv_paper_by_id(self) -> None:
        xml = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
        <id>http://arxiv.org/abs/1706.03762</id><title>Attention Is All You Need</title>
        <summary>Transformer abstract.</summary><published>2017-06-12T00:00:00Z</published>
        <author><name>Ada</name></author></entry></feed>"""
        response = unittest.mock.MagicMock()
        response.read.return_value = xml
        response.__enter__.return_value = response
        with patch("urllib.request.urlopen", return_value=response):
            paper = ArxivCollector().fetch_by_id("1706.03762")
        self.assertIsNotNone(paper)
        self.assertEqual(paper.title, "Attention Is All You Need")

    def test_nlp_topic_presets(self) -> None:
        queries = topic_queries("nlp_semantics_syntax_pragmatics")
        self.assertGreaterEqual(len(queries), 3)
        self.assertTrue(all("cat:cs.CL" in query for query in queries))

    def test_agent_topic_preset(self) -> None:
        queries = topic_queries("agents")
        self.assertGreaterEqual(len(queries), 2)
        self.assertTrue(any("multi-agent" in query for query in queries))

    def test_parse_rss_blog_feed(self) -> None:
        xml = """<rss><channel><item><title>Agent Workflows</title><link>https://example.com/a</link><pubDate>Tue, 26 May 2026 00:00:00 GMT</pubDate><description>Tool use for agents.</description></item></channel></rss>"""
        posts = _parse_rss("Example", ET.fromstring(xml))
        self.assertEqual(posts[0].title, "Agent Workflows")
        self.assertEqual(posts[0].source, "Example")

    def test_chat_completions_base_url(self) -> None:
        self.assertEqual(chat_completions_base_url("https://example.com"), "https://example.com/v1")
        self.assertEqual(chat_completions_base_url("https://example.com/v1"), "https://example.com/v1")

    def test_dotenv_does_not_override_explicit_environment(self) -> None:
        from smearglepaper.config import load_dotenv

        with tempfile.TemporaryDirectory() as tmpdir:
            dotenv = Path(tmpdir) / ".env"
            dotenv.write_text("OPENAI_MODEL=from-dotenv\n", encoding="utf-8")
            with patch.dict("os.environ", {"OPENAI_MODEL": "from-process"}, clear=True):
                load_dotenv(dotenv)
                self.assertEqual(os.environ["OPENAI_MODEL"], "from-process")

    def test_openai_headers_include_gateway_compatible_user_agent(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            headers = openai_request_headers("secret")
        self.assertIn("Mozilla/5.0", headers["User-Agent"])
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["Authorization"], "Bearer secret")

    def test_openai_user_agent_can_be_overridden(self) -> None:
        with patch.dict("os.environ", {"LLM_USER_AGENT": "GatewayClient/1.0"}):
            headers = openai_request_headers()
        self.assertEqual(headers["User-Agent"], "GatewayClient/1.0")
        self.assertNotIn("Authorization", headers)

    def test_article_writer_uses_anthropic_configuration(self) -> None:
        paper = PaperMeta("a", "Test", [], "abstract", "arxiv", "", None, "2026-05-20T00:00:00Z")
        with (
            patch.dict(
                "os.environ",
                {"ANTHROPIC_API_KEY": "test", "ANTHROPIC_BASE_URL": "https://example.com"},
                clear=True,
            ),
            patch.object(ArticleWriter, "_call_model", side_effect=["notes", "outline", "# Article"]) as call,
        ):
            article = ArticleWriter().write(paper)

        self.assertEqual(article, "# Article")
        self.assertEqual(call.call_count, 3)

    def test_local_article_template_has_readable_chinese(self) -> None:
        paper = PaperMeta(
            "a",
            "Reliable Agent Workflows",
            ["Ada"],
            "A useful abstract.",
            "arxiv",
            "https://arxiv.org/abs/a",
            None,
            "2026-05-20T00:00:00Z",
        )
        with patch.dict("os.environ", {}, clear=True):
            article = ArticleWriter().write(paper)

        self.assertIn("一句话结论", article)
        self.assertIn("研究问题", article)
        self.assertNotIn("涓€", article)

    def test_agent_prompts_have_readable_chinese(self) -> None:
        self.assertIn("论文信息助手", SYSTEM_PROMPT)
        descriptions = [str(tool["function"]["description"]) for tool in TOOLS]
        self.assertTrue(all("涓€" not in description and "璁烘" not in description for description in descriptions))

    def test_user_facing_files_are_valid_utf8(self) -> None:
        root = Path(__file__).resolve().parents[1]
        paths = [
            root / "README.md",
            root / "CLAUDE.md",
            root / "src" / "smearglepaper" / "llm.py",
            root / "src" / "smearglepaper" / "quality.py",
            root / "src" / "smearglepaper" / "tools.py",
            root / "skills" / "daily-digest" / "SKILL.md",
            root / "skills" / "paper-deep-read" / "SKILL.md",
        ]
        for path in paths:
            text = path.read_text(encoding="utf-8", errors="strict")
            self.assertNotIn("\ufffd", text, path)

    def test_review_article_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "article.md"
            path.write_text("# 一个信息量足够的标题\n\n## 一句话结论\n正文\n", encoding="utf-8")
            report = review_article_file(path)
            self.assertIn("score", report)
            self.assertEqual(report["figure_count"], 0)

    def test_optimized_paths(self) -> None:
        paths = optimized_paths(Path("data/articles/2605-23710v1.json"))
        self.assertEqual(paths["json"], Path("data/articles/2605-23710v1.optimized.json"))
        self.assertEqual(paths["markdown"], Path("data/articles/2605-23710v1.optimized.md"))
        self.assertEqual(paths["html"], Path("data/articles/2605-23710v1.optimized.html"))

    def test_optimized_paths_preserve_dotted_arxiv_id(self) -> None:
        paths = optimized_paths(Path("data/articles/1706.03762.json"))
        self.assertEqual(paths["json"], Path("data/articles/1706.03762.optimized.json"))
        self.assertEqual(paths["markdown"], Path("data/articles/1706.03762.optimized.md"))
        self.assertEqual(paths["html"], Path("data/articles/1706.03762.optimized.html"))

    def test_article_improvement_loads_parsed_paper_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            parsed = data_dir / "parsed" / "1706.03762.json"
            parsed.parent.mkdir(parents=True)
            parsed.write_text('{"text":"Table 2: evidence from the paper"}', encoding="utf-8")
            article = Article(
                paper=PaperMeta("1706.03762", "Test", [], "", "arxiv", "", None, ""),
                title="Test",
                digest="",
                markdown="",
                html="",
            )
            with patch("smearglepaper.editor.DATA_DIR", data_dir):
                self.assertIn("Table 2", _load_paper_evidence(article))

    def test_visual_evidence_context_includes_caption_and_page(self) -> None:
        context = format_visual_evidence(
            {
                "visuals": [
                    {"id": "table-1", "kind": "table", "page": 6, "caption": "Table 1: Complexity comparison"}
                ]
            }
        )
        self.assertIn("第 6 页", context)
        self.assertIn("Table 1: Complexity comparison", context)

    def test_build_paper_structure_creates_sections_and_evidence_ledger(self) -> None:
        pages = [
            {
                "page": 1,
                "text": (
                    "1 Introduction\n"
                    "We propose a compact architecture for reliable inference.\n"
                    "2 Experiments\n"
                    "Our model achieves 91.2 BLEU and outperforms the baseline."
                ),
            }
        ]
        visuals = [{"id": "table-1", "kind": "table", "page": 1, "caption": "Table 1: Main results"}]
        sections, ledger = build_paper_structure(pages, visuals)

        self.assertEqual([section["id"] for section in sections], ["1", "2"])
        self.assertTrue(any(item["kind"] == "method" for item in ledger))
        self.assertTrue(any(item["kind"] == "result" for item in ledger))
        self.assertTrue(any(item["kind"] == "visual" for item in ledger))

    def test_evidence_ledger_groups_components_from_the_same_source_figure(self) -> None:
        visuals = [
            {"id": "figure-2", "kind": "figure", "page": 4, "caption": "Figure 2: Attention"},
            {"id": "figure-3", "kind": "figure", "page": 4, "caption": "Figure 2: Attention"},
        ]
        _, ledger = build_paper_structure([{"page": 4, "text": ""}], visuals)
        visual_entries = [item for item in ledger if item["kind"] == "visual"]
        self.assertEqual(len(visual_entries), 1)
        self.assertEqual(visual_entries[0]["visual_ids"], ["figure-2", "figure-3"])

    def test_evidence_audit_flags_unsupported_numbers_and_visual_references(self) -> None:
        parsed = {
            "text": "The model achieves 91.2 BLEU. Table 1: Main results.",
            "visuals": [{"caption": "Table 1: Main results"}],
        }
        audit = audit_article_evidence("结果达到 99.9 BLEU，见 Table 2。\n*图 3：论文原图，建议结合本节内容阅读。*", parsed)

        self.assertIn("99.9", audit["unverified_numbers"])
        self.assertIn("table-2", audit["unknown_visual_references"])
        self.assertNotIn("figure-3", audit["unknown_visual_references"])

    def test_evidence_audit_ignores_markdown_section_numbers(self) -> None:
        audit = audit_article_evidence("## 4.1 Attention setup\n\n论文没有证明全部任务。", {"text": "paper text"})
        self.assertNotIn("4.1", audit["unverified_numbers"])

    def test_structured_evidence_context_has_sections_and_source_locator(self) -> None:
        context = format_evidence_context(
            {
                "sections": [{"id": "2", "title": "Experiments", "page_start": 4, "page_end": 5}],
                "evidence_ledger": [
                    {
                        "kind": "result",
                        "page": 5,
                        "section_title": "Experiments",
                        "excerpt": "The model achieves 91.2 BLEU.",
                        "visual_ids": ["table-1"],
                    }
                ],
            }
        )
        self.assertIn("Experiments", context)
        self.assertIn("第 5 页", context)
        self.assertIn("table-1", context)

    def test_agent_run_parser_defaults(self) -> None:
        args = build_parser().parse_args(["agent-run"])
        self.assertEqual(args.command, "agent-run")
        self.assertEqual(args.topic, "agents")
        self.assertFalse(args.real_wechat)

    def test_product_agent_task_parsers(self) -> None:
        created = build_parser().parse_args(["agent", "解读这篇论文", "--paper-id", "1706.03762"])
        approved = build_parser().parse_args(["task-approve", "--task-id", "task-1", "--gate", "publish"])
        resumed = build_parser().parse_args(["task-resume", "--task-id", "task-1", "--real-wechat"])
        retried = build_parser().parse_args(["task-retry", "--task-id", "task-1"])
        previewed = build_parser().parse_args(["task-preview", "--task-id", "task-1", "--port", "9000"])
        preview_alias = build_parser().parse_args(["agent-preview", "--task-id", "task-1"])

        self.assertEqual(created.command, "agent")
        self.assertEqual(created.paper_id, "1706.03762")
        self.assertEqual(created.ranking_profile, "balanced")
        self.assertEqual(approved.gate, "publish")
        self.assertTrue(resumed.real_wechat)
        self.assertEqual(retried.task_id, "task-1")
        self.assertEqual(previewed.command, "task-preview")
        self.assertEqual(previewed.port, 9000)
        self.assertEqual(preview_alias.command, "agent-preview")

    def test_runtime_candidate_selection_parser(self) -> None:
        args = build_parser().parse_args(["runs", "select", "run-1", "2607.10001"])
        self.assertEqual(args.runs_command, "select")
        self.assertEqual(args.run_id, "run-1")
        self.assertEqual(args.paper_id, "2607.10001")

    def test_writing_agent_parser_defaults(self) -> None:
        args = build_parser().parse_args(["writing-agent", "--paper-id", "1706.03762"])
        self.assertEqual(args.command, "writing-agent")
        self.assertEqual(args.target_score, 85)
        self.assertEqual(args.max_revisions, 3)
        self.assertEqual(args.style_mode, "balanced")
        self.assertEqual(args.mode, "paper-writing")
        self.assertFalse(args.upload_images)

    def test_writing_agent_parser_accepts_paper_url(self) -> None:
        args = build_parser().parse_args(
            [
                "writing-agent",
                "--mode",
                "paper-writing",
                "--paper-title",
                "Attention Is All You Need",
                "--paper-url",
                "https://arxiv.org/abs/1706.03762",
            ]
        )
        self.assertEqual(args.paper_url, "https://arxiv.org/abs/1706.03762")
        self.assertEqual(args.paper_title, "Attention Is All You Need")

    def test_prepare_agent_assets_parser(self) -> None:
        args = build_parser().parse_args(["prepare-agent-assets", "--article-json", "draft.json"])
        self.assertEqual(args.article_json, "draft.json")
        self.assertEqual(args.target_score, 85)

    def test_agent_check_parser_and_single_agent(self) -> None:
        args = build_parser().parse_args(["agent-check", "--agent", "writer"])
        self.assertEqual(args.command, "agent-check")
        self.assertIn("writer", AGENTS)
        report = check_agents("orchestrator")
        self.assertTrue(report["ok"])

    def test_writer_agent_check_handles_nested_llm_settings(self) -> None:
        report = check_agents("writer")
        self.assertIn("writer", report["checks"])
        self.assertIn("llm_provider", report["checks"]["writer"]["detail"])
        self.assertIn("writing_agent", AGENTS)

    def test_scout_run_parser_and_review_row(self) -> None:
        args = build_parser().parse_args(["scout-run", "--from-existing"])
        self.assertEqual(args.command, "scout-run")
        self.assertTrue(args.from_existing)
        paper = PaperMeta("a", "Multi-Agent Tool Use", [], "agents use tools", "arxiv", "https://example.com", None, "2026-05-20T00:00:00Z", categories=["cs.AI"])
        row = rank_papers([paper], query="agent tool", top_k=1)[0]
        review = _paper_review_row(1, row, Path("papers.json"))
        self.assertEqual(review["type"], "multi-agent paper")
        self.assertIn("agent", review["topic_matches"])


if __name__ == "__main__":
    unittest.main()
