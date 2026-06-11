from __future__ import annotations

import datetime as dt
import html
from pathlib import Path

from .blogs import BlogCollector
from .collector import ArxivCollector
from .config import DATA_DIR
from .models import PaperMeta
from .ranker import rank_papers
from .storage import ensure_parent, read_json, write_json


def run_scout_review(
    topic: str | None,
    query: str | None,
    days: int,
    max_results: int,
    include_blogs: bool = True,
    from_existing: bool = False,
) -> dict[str, object]:
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = DATA_DIR / "agent_reviews" / "scout" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    if from_existing:
        papers_payload = read_json(DATA_DIR / "papers" / "latest.json", [])
        blogs_payload = read_json(DATA_DIR / "blogs" / "latest.json", {"count": 0, "posts": [], "errors": []})
    else:
        papers = ArxivCollector().collect(topic, query, days, max_results)
        papers_payload = [paper.to_dict() for paper in papers]
        blogs_payload = BlogCollector().collect(topic or "agents", query, days, max_results) if include_blogs else {"count": 0, "posts": [], "errors": []}

    papers = [PaperMeta.from_dict(item) for item in papers_payload]
    ranked = rank_papers(papers, query=query or topic, top_k=len(papers) or max_results)
    paper_rows = [_paper_review_row(index, row, run_dir / "papers.json") for index, row in enumerate(ranked, start=1)]
    blog_rows = _blog_review_rows(blogs_payload, run_dir / "blogs.json")
    summary = {
        "agent": "Scout Agent",
        "run_id": run_id,
        "topic": topic,
        "query": query,
        "days": days,
        "max_results": max_results,
        "from_existing": from_existing,
        "human_check": "pending",
        "outputs": {
            "run_dir": str(run_dir),
            "papers": str(run_dir / "papers.json"),
            "blogs": str(run_dir / "blogs.json"),
            "paper_review": str(run_dir / "paper_review.json"),
            "blog_review": str(run_dir / "blog_review.json"),
            "summary": str(run_dir / "summary.json"),
            "html": str(run_dir / "report.html"),
        },
        "counts": {
            "papers": len(paper_rows),
            "blogs": len(blog_rows),
            "blog_errors": len(blogs_payload.get("errors", [])) if isinstance(blogs_payload, dict) else 0,
        },
        "next_agent": "Ranker Agent",
        "review_questions": [
            "这些论文是否真的是当前想看的 Agent 方向？",
            "Top 5 是否适合进入 Ranker/Reader，还是需要换 query？",
            "博客源是否需要补充，例如 Google DeepMind、Hugging Face、LangGraph、LlamaIndex？",
        ],
    }

    write_json(run_dir / "papers.json", papers_payload)
    write_json(run_dir / "blogs.json", blogs_payload)
    write_json(run_dir / "paper_review.json", paper_rows)
    write_json(run_dir / "blog_review.json", blog_rows)
    write_json(run_dir / "summary.json", summary)
    html_path = run_dir / "report.html"
    ensure_parent(html_path)
    html_path.write_text(_render_html(summary, paper_rows, blog_rows, blogs_payload), encoding="utf-8")
    summary["outputs"]["html"] = str(html_path)  # type: ignore[index]
    return summary


def _paper_review_row(index: int, ranked_row: dict[str, object], source_file: Path) -> dict[str, object]:
    paper = PaperMeta.from_dict(ranked_row["paper"])  # type: ignore[arg-type]
    return {
        "rank": index,
        "paper_id": paper.paper_id,
        "title": paper.title,
        "type": _paper_type(paper),
        "source": paper.source,
        "categories": paper.categories,
        "published_at": paper.published_at,
        "updated_at": paper.updated_at,
        "url": paper.url,
        "pdf_url": paper.pdf_url,
        "authors": paper.authors,
        "abstract": paper.abstract,
        "score": ranked_row.get("score", 0),
        "selection_reasons": ranked_row.get("reasons", []),
        "topic_matches": _topic_matches(paper),
        "stored_in": str(source_file),
        "human_decision": "pending",
    }


def _blog_review_rows(payload: object, source_file: Path) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        return []
    rows = []
    for index, post in enumerate(payload.get("posts", []), start=1):
        if not isinstance(post, dict):
            continue
        rows.append(
            {
                "rank": index,
                "title": post.get("title", ""),
                "type": "blog",
                "source": post.get("source", ""),
                "published_at": post.get("published_at", ""),
                "url": post.get("url", ""),
                "summary": post.get("summary", ""),
                "selection_reasons": ["matched_agent_blog_keywords"],
                "stored_in": str(source_file),
                "human_decision": "pending",
            }
        )
    return rows


def _paper_type(paper: PaperMeta) -> str:
    text = f"{paper.title} {paper.abstract}".lower()
    if any(term in text for term in ("multi-agent", "multi agent")):
        return "multi-agent paper"
    if any(term in text for term in ("agent", "agentic", "tool use", "autonomous")):
        return "agent paper"
    if any(cat == "cs.CL" for cat in paper.categories):
        return "nlp paper"
    if any(cat == "cs.LG" for cat in paper.categories):
        return "machine learning paper"
    if any(cat == "cs.AI" for cat in paper.categories):
        return "ai paper"
    return "paper"


def _topic_matches(paper: PaperMeta) -> list[str]:
    terms = ["agent", "agents", "agentic", "multi-agent", "multi agent", "tool use", "autonomous", "workflow", "memory"]
    text = f"{paper.title} {paper.abstract}".lower()
    return [term for term in terms if term in text]


def _render_html(summary: dict[str, object], paper_rows: list[dict[str, object]], blog_rows: list[dict[str, object]], blogs_payload: object) -> str:
    paper_cards = "\n".join(_paper_card(row) for row in paper_rows)
    blog_cards = "\n".join(_blog_card(row) for row in blog_rows) or "<p class='muted'>No blog posts collected for this run.</p>"
    errors = blogs_payload.get("errors", []) if isinstance(blogs_payload, dict) else []
    error_html = "\n".join(f"<li>{html.escape(str(item))}</li>" for item in errors) or "<li>No blog feed errors.</li>"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Scout Agent Review - {html.escape(str(summary["run_id"]))}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; color: #172033; background: #f7f8fb; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 28px 20px 52px; }}
    h1 {{ font-size: 28px; margin: 0 0 8px; }}
    h2 {{ font-size: 20px; margin-top: 34px; border-left: 4px solid #2563eb; padding-left: 10px; }}
    .muted {{ color: #667085; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 20px 0; }}
    .metric, article {{ background: #fff; border: 1px solid #dde3ee; border-radius: 8px; padding: 14px 16px; }}
    .metric strong {{ display: block; font-size: 24px; }}
    article {{ margin: 14px 0; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }}
    .tag {{ background: #eef2ff; color: #273b8f; border-radius: 999px; padding: 3px 8px; font-size: 12px; }}
    a {{ color: #1d4ed8; text-decoration: none; }}
    code {{ background: #eef1f6; padding: 2px 5px; border-radius: 5px; }}
    p {{ line-height: 1.72; }}
  </style>
</head>
<body>
<main>
  <h1>Scout Agent 人工验收报告</h1>
  <p class="muted">Run: <code>{html.escape(str(summary["run_id"]))}</code> · Human check: <code>pending</code></p>
  <div class="grid">
    <div class="metric"><span>论文候选</span><strong>{len(paper_rows)}</strong></div>
    <div class="metric"><span>博客候选</span><strong>{len(blog_rows)}</strong></div>
    <div class="metric"><span>Topic</span><strong>{html.escape(str(summary.get("topic") or "-"))}</strong></div>
    <div class="metric"><span>Days</span><strong>{html.escape(str(summary.get("days")))}</strong></div>
  </div>
  <h2>输出路径</h2>
  <p><code>{html.escape(str(summary["outputs"]))}</code></p>
  <h2>人工检查问题</h2>
  <ul>{''.join(f"<li>{html.escape(q)}</li>" for q in summary["review_questions"])}</ul>
  <h2>论文候选</h2>
  {paper_cards}
  <h2>博客候选</h2>
  {blog_cards}
  <h2>博客源错误</h2>
  <ul>{error_html}</ul>
</main>
</body>
</html>"""


def _paper_card(row: dict[str, object]) -> str:
    tags = "".join(f"<span class='tag'>{html.escape(str(tag))}</span>" for tag in row.get("categories", []))
    matches = "".join(f"<span class='tag'>{html.escape(str(tag))}</span>" for tag in row.get("topic_matches", []))
    reasons = ", ".join(str(x) for x in row.get("selection_reasons", []))
    return f"""<article>
  <h3>#{row.get("rank")} {html.escape(str(row.get("title", "")))}</h3>
  <div class="meta"><span class="tag">{html.escape(str(row.get("type", "")))}</span>{tags}{matches}</div>
  <p class="muted">Score: {html.escape(str(row.get("score", "")))} · Published: {html.escape(str(row.get("published_at", "")))}</p>
  <p>{html.escape(str(row.get("abstract", ""))[:700])}</p>
  <p>选择原因：{html.escape(reasons)}</p>
  <p>路径：<code>{html.escape(str(row.get("stored_in", "")))}</code></p>
  <p><a href="{html.escape(str(row.get("url", "")))}">论文页</a> · <a href="{html.escape(str(row.get("pdf_url", "")))}">PDF</a></p>
</article>"""


def _blog_card(row: dict[str, object]) -> str:
    return f"""<article>
  <h3>#{row.get("rank")} {html.escape(str(row.get("title", "")))}</h3>
  <p class="muted">{html.escape(str(row.get("source", "")))} · {html.escape(str(row.get("published_at", "")))}</p>
  <p>{html.escape(str(row.get("summary", ""))[:500])}</p>
  <p>路径：<code>{html.escape(str(row.get("stored_in", "")))}</code></p>
  <p><a href="{html.escape(str(row.get("url", "")))}">原文链接</a></p>
</article>"""
