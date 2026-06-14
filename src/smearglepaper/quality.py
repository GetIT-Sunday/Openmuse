from __future__ import annotations

import re
from pathlib import Path

from .config import DATA_DIR
from .evidence import audit_article_evidence
from .models import Article
from .storage import read_json

REQUIRED_SECTIONS = ["一句话结论", "研究问题", "方法", "关键", "局限", "适合谁读"]
RISKY_PHRASES = ["首次", "彻底", "颠覆", "完全解决", "革命性", "显著优于所有", "证明了"]
REVIEW_THRESHOLD = 60


def _check_dimensions(markdown: str, headings: list[str], figure_count: int, char_count: int) -> dict[str, dict[str, object]]:
    """Return per-dimension check results with pass/fail and weight."""
    has_conclusion = any("结论" in h or "一句话" in h for h in headings)
    has_method = any("方法" in h or "怎么做的" in h or "技术" in h for h in headings)
    has_figures = figure_count > 0
    word_count_ok = 800 <= char_count <= 5000
    has_risky = any(phrase in markdown for phrase in RISKY_PHRASES)
    structure_complete = len([h for h in headings if any(k in h for k in ["引言", "方法", "实验", "结论", "局限"])]) >= 3
    avg_sentence_len = _avg_sentence_length(markdown)
    readability_ok = avg_sentence_len < 80

    return {
        "has_conclusion": {"pass": has_conclusion, "weight": 0.15},
        "has_method_section": {"pass": has_method, "weight": 0.20},
        "has_figures": {"pass": has_figures, "weight": 0.10},
        "word_count_ok": {"pass": word_count_ok, "weight": 0.15},
        "terminology_accurate": {"pass": not has_risky, "weight": 0.20},
        "structure_complete": {"pass": structure_complete, "weight": 0.10},
        "readability": {"pass": readability_ok, "weight": 0.10},
    }


def _avg_sentence_length(text: str) -> float:
    sentences = re.split(r"[。！？.!?]+", text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 5]
    if not sentences:
        return 0
    return sum(len(s) for s in sentences) / len(sentences)


def review_article_file(path: Path) -> dict[str, object]:
    paper_id = ""
    if path.suffix == ".json":
        article = Article.from_dict(read_json(path, {}))
        markdown = article.markdown
        title = article.title
        figure_count = len(article.figure_paths)
        paper_id = article.paper.paper_id
    else:
        markdown = path.read_text(encoding="utf-8")
        title = _title_from_markdown(markdown)
        figure_count = markdown.count("![")
        paper_id = path.stem.removesuffix(".optimized")

    issues: list[dict[str, str]] = []
    suggestions: list[str] = []
    char_count = len(markdown)
    headings = re.findall(r"^#{1,3}\s+(.+)$", markdown, flags=re.MULTILINE)
    parsed = read_json(DATA_DIR / "parsed" / f"{paper_id.replace('/', '_')}.json", {})
    evidence_audit = audit_article_evidence(markdown, parsed)

    if char_count < 2500:
        issues.append({"severity": "medium", "message": "文章偏短，适合作为快读稿；若要公众号深度解读，建议扩到 3000-5000 字。"})
    if char_count > 9000:
        issues.append({"severity": "medium", "message": "文章偏长，公众号阅读压力较大，建议拆分或压缩。"})
    if not title or len(title) < 10:
        issues.append({"severity": "high", "message": "标题信息量不足。"})
    if len(title) > 64:
        issues.append({"severity": "low", "message": "标题较长，可能不适合公众号列表页展示。"})

    missing = [name for name in REQUIRED_SECTIONS if not any(name in heading for heading in headings)]
    if missing:
        issues.append({"severity": "medium", "message": "缺少或未明确标出的栏目：" + "、".join(missing)})

    risky_hits = [phrase for phrase in RISKY_PHRASES if phrase in markdown]
    if risky_hits:
        issues.append({"severity": "medium", "message": "存在可能过度断言的词：" + "、".join(risky_hits)})
        suggestions.append("将强断言改成“作者认为/结果显示/在该设置下”，避免超出论文证据。")

    if figure_count == 0:
        issues.append({"severity": "low", "message": "没有图表候选。公众号可读性会弱一些。"})
    elif "page_1_preview" in markdown:
        issues.append({"severity": "low", "message": "当前图表候选是首页预览，不一定是论文核心图。"})

    if "http://arxiv.org/abs/" not in markdown and "https://arxiv.org/abs/" not in markdown:
        issues.append({"severity": "medium", "message": "未检测到 arXiv 论文链接。"})

    if evidence_audit.get("available"):
        unverified_numbers = evidence_audit.get("unverified_numbers", [])
        unknown_refs = evidence_audit.get("unknown_visual_references", [])
        available_refs = evidence_audit.get("available_visual_references", [])
        referenced_visuals = evidence_audit.get("referenced_visuals", [])
        if unverified_numbers:
            shown = "、".join(str(value) for value in unverified_numbers[:8])
            issues.append({"severity": "high", "message": f"以下数字无法在原论文正文中定位：{shown}"})
            suggestions.append("逐项核对实验数字；若数字来自外部资料，应标明来源，不要归因于原论文。")
        if unknown_refs:
            issues.append({"severity": "high", "message": "引用了原论文中未识别到的图表：" + "、".join(str(value) for value in unknown_refs)})
        if available_refs and not referenced_visuals:
            issues.append({"severity": "low", "message": "原论文存在可用核心图表，但正文没有按 Figure/Table 编号进行讲解。"})
            suggestions.append("选择最关键的 Figure/Table，说明它回答的问题、观察结果与推断边界。")

    score = 100
    for issue in issues:
        score -= {"high": 25, "medium": 12, "low": 5}.get(issue["severity"], 5)
    score = max(score, 0)

    if score >= 85:
        verdict = "ready_after_light_edit"
    elif score >= 70:
        verdict = "needs_editorial_pass"
    else:
        verdict = "needs_revision"

    if not suggestions:
        suggestions = [
            "检查摘要中的关键实验数字是否和论文原文一致。",
            "为公众号读者补一个“为什么现在值得读”的现实背景段。",
            "发布前确认图表是否为核心图，而不是 PDF 首页预览。",
        ]

    checks = _check_dimensions(markdown, headings, figure_count, char_count)
    if evidence_audit.get("available"):
        for name, passed in evidence_audit.get("checks", {}).items():
            checks[name] = {"pass": bool(passed), "weight": 0.20}

    return {
        "path": str(path),
        "title": title,
        "char_count": char_count,
        "heading_count": len(headings),
        "figure_count": figure_count,
        "score": score,
        "threshold": REVIEW_THRESHOLD,
        "pass": score >= REVIEW_THRESHOLD,
        "verdict": verdict,
        "checks": {name: {"pass": c["pass"], "weight": c["weight"]} for name, c in checks.items()},
        "evidence_audit": evidence_audit,
        "issues": issues,
        "suggestions": suggestions,
    }


def _title_from_markdown(markdown: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""

