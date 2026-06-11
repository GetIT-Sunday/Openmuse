from __future__ import annotations

import re
from pathlib import Path
from urllib.error import URLError

from .config import env
from .llm import ArticleWriter
from .models import Article, PaperMeta
from .quality import review_article_file
from .renderer import WechatRenderer
from .storage import ensure_parent, read_json, write_json


def improve_article_file(path: Path, output_prefix: Path | None = None) -> dict[str, object]:
    article, markdown = _load_article(path)
    before = review_article_file(path)
    improved_markdown = improve_markdown(article, markdown, before)
    improved_markdown = _strip_markdown_fence(improved_markdown)

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


def improve_markdown(article: Article, markdown: str, review: dict[str, object]) -> str:
    if not env("OPENAI_API_KEY") or not env("OPENAI_BASE_URL"):
        raise RuntimeError("OPENAI_BASE_URL and OPENAI_API_KEY are required for article improvement.")
    try:
        return ArticleWriter()._call_model(
            "你是严谨的中文科技编辑，擅长把论文解读初稿改成适合微信公众号发布的深度文章。",
            improve_prompt(article, markdown, review),
            temperature=0.35,
        )
    except URLError as exc:
        raise RuntimeError(f"Could not reach LLM endpoint: {exc.reason}") from exc
    except Exception as exc:  # noqa: BLE001 - surfaced as a concise CLI/MCP diagnostic
        raise RuntimeError(f"Article improvement failed: {type(exc).__name__}: {exc}") from exc


def improve_prompt(article: Article, markdown: str, review: dict[str, object]) -> str:
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
"""


def optimized_paths(path: Path, output_prefix: Path | None = None) -> dict[str, Path]:
    prefix = output_prefix if output_prefix else path.with_suffix("")
    if prefix.suffix:
        prefix = prefix.with_suffix("")
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
