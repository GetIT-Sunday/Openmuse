from __future__ import annotations

import re
from pathlib import Path
from urllib.error import URLError

from .config import DATA_DIR
from .evidence import format_evidence_context
from .llm import ArticleWriter, _detect_api_provider, format_visual_evidence
from .figures import place_figures, place_visuals
from .models import Article, PaperMeta
from .quality import review_article_file
from .renderer import WechatRenderer
from .storage import ensure_parent, read_json, write_json


def improve_article_file(path: Path, output_prefix: Path | None = None) -> dict[str, object]:
    article, markdown = _load_article(path)
    before = review_article_file(path)
    parsed_paper = _load_parsed_paper(article)
    paper_evidence = _paper_evidence_from_parsed(parsed_paper)
    improved_markdown = improve_markdown(article, markdown, before, paper_evidence)
    improved_markdown = _strip_markdown_fence(improved_markdown)
    visuals = parsed_paper.get("visuals", [])
    if isinstance(visuals, list) and visuals:
        improved_markdown = place_visuals(improved_markdown, visuals)
    else:
        improved_markdown = place_figures(improved_markdown, article.figure_paths)

    paths = optimized_paths(path, output_prefix)
    html = WechatRenderer().render(improved_markdown)
    title = _title_from_markdown(improved_markdown) or article.title
    digest = _digest_from_markdown(improved_markdown) or article.digest
    improved = Article(
        paper=article.paper,
        title=title,
        digest=digest,
        markdown=improved_markdown,
        html=html,
        cover_path=article.cover_path,
        figure_paths=article.figure_paths,
        thumb_media_id=article.thumb_media_id,
        word_count=len(improved_markdown),
    )

    ensure_parent(paths["markdown"])
    paths["markdown"].write_text(improved_markdown, encoding="utf-8")
    paths["html"].write_text(html, encoding="utf-8")
    write_json(paths["json"], improved.to_dict())

    after = review_article_file(paths["json"])
    return {
        "ok": True,
        "input": str(path),
        "outputs": {key: str(value) for key, value in paths.items()},
        "before": before,
        "after": after,
    }


def improve_markdown(article: Article, markdown: str, review: dict[str, object], paper_evidence: str = "") -> str:
    if _detect_api_provider() == "none":
        raise RuntimeError("Configure an OpenAI-compatible or Anthropic endpoint for article improvement.")
    try:
        return ArticleWriter()._call_model(
            "你是严谨的中文科技编辑，擅长把论文解读初稿改成适合微信公众号发布的深度文章。",
            improve_prompt(article, markdown, review, paper_evidence),
            temperature=0.35,
        )
    except URLError as exc:
        raise RuntimeError(f"Could not reach LLM endpoint: {exc.reason}") from exc
    except Exception as exc:  # noqa: BLE001 - surfaced as a concise CLI/MCP diagnostic
        raise RuntimeError(f"Article improvement failed: {type(exc).__name__}: {exc}") from exc


def improve_prompt(article: Article, markdown: str, review: dict[str, object], paper_evidence: str = "") -> str:
    paper = article.paper
    return f"""请把下面的中文论文解读初稿改写成更适合微信公众号发布的 Markdown 深度稿。

必须遵守：
1. 只基于“论文元数据、摘要、原始草稿、质检报告”改写，不要编造实验数字、数据集结果、作者结论；
2. 目标长度 3000-5000 个中文字符；
3. 保留 Markdown 格式，直接输出文章正文，不要解释你的改稿过程；
4. 标题克制、信息量高，避免“首次、彻底、颠覆、革命性、完全解决、证明了”等过强表述；
5. 明确区分论文声称、实验支持、读者可以继续追问的地方；
6. 必须包含这些二级栏目：一句话结论、为什么值得读、研究问题、方法拆解、实验怎么验证、局限与风险、适合谁读；
7. 保留论文链接：{paper.url}
8. 使用证据模式区分措辞：“结果显示”仅用于论文直接证据，“作者提出/作者认为”用于作者主张，“可以理解为/编辑解读”用于解释；
9. 避免“彻底、革命性、颠覆、证明了、全面领先、完全解决”等过强措辞，除非原文在明确范围内直接支持；
10. 每个实验数字必须同时说明任务、指标或对比范围，不要只给孤立数字；
11. 每个方法组件都解释它解决什么问题、如何工作、与整体贡献有什么关系；
12. 为论文图设计逐图讲解：先提出读图问题，再说明图中元素，最后给出证据支持的结论和不能推出的结论；
13. 目标长度 3500-5000 个中文字符，段落保持短小，每段聚焦一个观点；
14. 结尾明确区分“原论文已经验证的结论”和“后续影响或编辑推断”。
15. 这是一篇“论文讲解”，不是主题知识教程。沿着作者的论证链写：作者观察到什么问题、提出什么假设、为何选择这些设计、每组实验验证哪条主张、证据是否充分；
16. 优先引用原论文中的章节名、Figure、Table、任务设置和对比基线。不要用后来广为人知的 Transformer 知识替代论文当时实际写了什么；
17. 方法部分不要只解释组件定义，要解释作者为何需要该组件，以及它如何回应论文的研究问题；
18. 实验部分按“待验证主张 → 实验设置 → 观察结果 → 能得出的结论 → 仍不能回答的问题”组织；
19. 至少包含一个“论文的论证链”栏目，以及一个“关键表格与消融告诉我们什么”栏目。
20. 正文前 80% 聚焦原论文在发表当时的内容；BERT、GPT、产业影响等后续历史最多放在结尾一小段，不能代替原论文论证；
21. 不要使用“改变领域、革新、枷锁、压倒性、无需赘述”等媒体化评价作为标题或栏目名；
22. 只有在原论文证据中能定位到具体 Table/行/指标时，才能声称某项消融验证了某个设计。若证据不足，明确写“本文未单独消融验证”；
23. 优先沿当前原论文自己的章节与论证顺序推进，不得套用其他论文的固定章节结构；
24. 对证据索引中最关键的 Figure 和 Table 分别解释“作者想回答什么问题、图表中实际观察到什么、不能据此推出什么”，不得把理论比较写成实验结果。

论文信息：
- 标题：{paper.title}
- 作者：{", ".join(paper.authors)}
- 分类：{", ".join(paper.categories)}
- 摘要：{paper.abstract}
- 链接：{paper.url}

质检报告：
{review}

原始草稿：
{markdown[:12000]}

原论文正文证据（由 PDF 解析，优先级高于原始草稿）：
{paper_evidence[:24000]}
"""


def optimized_paths(path: Path, output_prefix: Path | None = None) -> dict[str, Path]:
    prefix = output_prefix if output_prefix else path.with_suffix("")
    return {
        "json": prefix.with_name(f"{prefix.name}.optimized.json"),
        "markdown": prefix.with_name(f"{prefix.name}.optimized.md"),
        "html": prefix.with_name(f"{prefix.name}.optimized.html"),
    }


def _load_article(path: Path) -> tuple[Article, str]:
    if path.suffix == ".json":
        article = Article.from_dict(read_json(path, {}))
        return article, article.markdown

    markdown = path.read_text(encoding="utf-8")
    paper = PaperMeta(
        paper_id=path.stem,
        title=_title_from_markdown(markdown) or path.stem,
        authors=[],
        abstract="",
        source="local",
        url="",
        pdf_url=None,
        published_at="",
    )
    return Article(
        paper=paper,
        title=paper.title,
        digest=_digest_from_markdown(markdown),
        markdown=markdown,
        html=WechatRenderer().render(markdown),
        word_count=len(markdown),
    ), markdown


def _load_paper_evidence(article: Article) -> str:
    return _paper_evidence_from_parsed(_load_parsed_paper(article))


def _load_parsed_paper(article: Article) -> dict[str, object]:
    paper_id = article.paper.paper_id.replace("/", "_")
    return read_json(DATA_DIR / "parsed" / f"{paper_id}.json", {})


def _paper_evidence_from_parsed(parsed: dict[str, object]) -> str:
    structured_evidence = format_evidence_context(parsed)
    visual_evidence = format_visual_evidence(parsed)
    text_evidence = str(parsed.get("text", "")).strip()[:30000]
    return "\n\n".join(chunk for chunk in (structured_evidence, visual_evidence, text_evidence) if chunk)


def _strip_markdown_fence(markdown: str) -> str:
    text = markdown.strip()
    match = re.match(r"^```(?:markdown|md)?\s*(.*?)\s*```$", text, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text


def _title_from_markdown(markdown: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _digest_from_markdown(markdown: str) -> str:
    for line in markdown.splitlines():
        text = line.strip()
        if text and not text.startswith("#") and not text.startswith("!["):
            return re.sub(r"[*_>`]", "", text)[:120]
    return ""
