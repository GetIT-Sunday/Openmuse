from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from smearglepaper.agents import AGENTS, check_agents
from smearglepaper.blogs import _parse_rss
from smearglepaper.collector import parse_arxiv_feed, topic_queries
from smearglepaper.editor import optimized_paths
from smearglepaper.llm import chat_completions_base_url
from smearglepaper.models import PaperMeta
from smearglepaper.quality import review_article_file
from smearglepaper.ranker import rank_papers
from smearglepaper.renderer import WechatRenderer
from smearglepaper.cli import build_parser
from smearglepaper.scout import _paper_review_row
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
        html = WechatRenderer().render("# 标题\n\n## 小节\n\n正文")
        self.assertIn("<html", html)
        self.assertIn("标题", html)

    def test_append_figures(self) -> None:
        markdown = append_figures("# Title", ["/tmp/a.png", "/tmp/b.png"])
        self.assertIn("论文图表候选", markdown)
        self.assertIn("](/tmp/a.png)", markdown)

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

    def test_agent_run_parser_defaults(self) -> None:
        args = build_parser().parse_args(["agent-run"])
        self.assertEqual(args.command, "agent-run")
        self.assertEqual(args.topic, "agents")
        self.assertFalse(args.real_wechat)

    def test_agent_check_parser_and_single_agent(self) -> None:
        args = build_parser().parse_args(["agent-check", "--agent", "writer"])
        self.assertEqual(args.command, "agent-check")
        self.assertIn("writer", AGENTS)
        report = check_agents("orchestrator")
        self.assertTrue(report["ok"])

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
