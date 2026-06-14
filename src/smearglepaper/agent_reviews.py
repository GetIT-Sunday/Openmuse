from __future__ import annotations

import re
from typing import Any

from .evidence import audit_article_evidence
from .models import PaperMeta

OVERCLAIM_WORDS = ["彻底", "颠覆", "完全解决", "证明一切", "全面领先", "全面解析", "终结"]
BODY_OVERCLAIM_WORDS = ["彻底", "颠覆", "完全解决", "证明一切", "全面领先", "全面解析"]
TITLE_OVERCLAIM_WORDS = [
    *OVERCLAIM_WORDS,
    "取代RNN",
    "取代 RNN",
    "取代循环神经网络",
    "纯注意力架构",
]


def diagnose_article(markdown: str, paper: PaperMeta, target_audience: str) -> tuple[dict[str, object], str]:
    title = _title(markdown)
    headings = _headings(markdown)
    checks = {
        "title_scope": bool(title) and len(title) <= 64 and not any(word in title for word in TITLE_OVERCLAIM_WORDS),
        "clear_thesis": any(term in markdown for term in ("核心", "真正", "主线", "问题")),
        "not_section_notes": len(re.findall(r"\bSection\s+\d", markdown, flags=re.IGNORECASE)) < 8,
        "separates_history": any(term in markdown for term in ("后续影响", "并非原论文", "编辑解读")),
        "has_boundaries": any(term in markdown for term in ("没有证明", "不能推出", "局限", "边界")),
        "visuals_support_argument": bool(re.search(r"(?:Figure|Table|图|表)\s*\d", markdown, flags=re.IGNORECASE)),
        "has_learning_transfer": any(term in markdown for term in ("面试", "学习迁移", "项目迁移", "适合谁读")),
    }
    issues = [name for name, passed in checks.items() if not passed]
    report = {
        "paper_title": paper.title,
        "target_audience": target_audience,
        "title": title,
        "heading_count": len(headings),
        "checks": checks,
        "issues": issues,
        "summary": "原稿可作为输入基础。" if not issues else f"原稿需要重点修复 {len(issues)} 类问题。",
    }
    lines = ["# Article Diagnosis", "", f"- 论文：{paper.title}", f"- 目标读者：{target_audience}", f"- 诊断：{report['summary']}", "", "## 检查结果"]
    lines.extend(f"- {'通过' if passed else '需修改'}：{name}" for name, passed in checks.items())
    return report, "\n".join(lines) + "\n"


def analyze_style(
    source_profiles: dict[str, object],
    style_patterns: dict[str, object],
    *,
    style_mode: str,
    target_audience: str,
) -> tuple[dict[str, object], str]:
    preferred = ["PaperWeekly", "机器之心", "夕小瑶科技说", "AINLP", "量子位"]
    selected = {name: source_profiles[name] for name in preferred if name in source_profiles}
    profile = {
        "style_mode": style_mode,
        "target_audience": target_audience,
        "selected_sources": selected,
        "patterns": style_patterns.get(style_mode, style_patterns.get("balanced", [])),
        "rules": ["不复制公众号全文", "技术准确性优先于传播感", "标题有问题感但不标题党"],
    }
    lines = ["# Style Report", "", f"- 风格模式：{style_mode}", f"- 目标读者：{target_audience}", "", "## 风格组合"]
    for name, item in selected.items():
        lines.append(f"- {name}：学习 {', '.join(item.get('learn', []))}；避免 {', '.join(item.get('avoid', []))}")
    lines.extend(["", "## 写作规则", *[f"- {rule}" for rule in profile["rules"]]])
    return profile, "\n".join(lines) + "\n"


def technical_review(markdown: str, paper: PaperMeta, parsed: dict[str, object]) -> tuple[dict[str, object], str]:
    audit = audit_article_evidence(markdown, parsed)
    blocking: list[str] = []
    minor: list[str] = []
    instructions: list[str] = []
    for value in audit.get("unverified_numbers", []):
        blocking.append(f"数字 {value} 无法在原论文正文定位")
    for value in audit.get("unknown_visual_references", []):
        blocking.append(f"引用了未识别的图表 {value}")
    blocking.extend(_article_integrity_issues(markdown))
    overclaims = [word for word in BODY_OVERCLAIM_WORDS if word in _original_paper_scope(markdown)]
    if overclaims:
        minor.append(f"存在可能过度结论的措辞：{', '.join(overclaims)}")
    if not any(term in markdown for term in ("没有证明", "不能推出", "局限", "边界")):
        minor.append("缺少论文没有证明什么或结论边界")
    if "后续影响" in markdown and not any(term in markdown for term in ("不是原论文", "并非原论文", "后续历史")):
        minor.append("后续影响与原论文结论的边界不够明确")
    if paper.paper_id == "1706.03762":
        transformer_checks = {
            "“纯注意力”容易被误解，需说明模型仍包含 FFN、残差、LayerNorm 和位置编码": all(
                term in markdown for term in ("FFN", "残差", "LayerNorm")
            ),
            "需要区分训练并行和自回归生成并行": "自回归" in markdown and "训练" in markdown and "并行" in markdown,
            "Table 3 和 Table 4 不能过度推广": "Table 3" in markdown and "Table 4" in markdown and any(
                term in markdown for term in ("不能", "边界", "特定")
            ),
        }
        minor.extend(message for message, passed in transformer_checks.items() if not passed)
    score = max(0, 100 - 20 * len(blocking) - 6 * len(minor))
    instructions.extend(f"修复：{issue}" for issue in blocking + minor)
    report = {
        "score": score,
        "blocking_issues": blocking,
        "minor_issues": minor,
        "revision_instructions": instructions,
        "evidence_audit": audit,
        "needs_human_verification": blocking,
    }
    return report, _review_markdown("Technical Review", report)


def wechat_review(markdown: str) -> tuple[dict[str, object], str]:
    title = _title(markdown)
    headings = _headings(markdown)
    paragraphs = [
        re.sub(r"^>\s*", "", line.strip())
        for line in markdown.splitlines()
        if line.strip() and not line.startswith(("#", "!", "*", "-"))
    ]
    opening = paragraphs[0] if paragraphs else ""
    title_suggestions: list[str] = []
    opening_suggestions: list[str] = []
    structure_suggestions: list[str] = []
    readability_suggestions: list[str] = []
    publication_blocking_issues: list[str] = []
    if not title or len(title) < 10:
        title_suggestions.append("标题需要包含论文、问题或明确判断")
    if len(title) > 64 or any(word in title for word in TITLE_OVERCLAIM_WORDS):
        title_suggestions.append("收敛标题范围，避免标题党或列表页截断")
    if len(opening) > 300 or not any(mark in opening for mark in ("？", "?", "为什么", "能否", "如何")):
        opening_suggestions.append("开头 300 字内提出核心问题和阅读价值")
    if len(headings) < 6:
        structure_suggestions.append("增加问题型小标题，显式呈现论证链")
    if not any(term in markdown for term in ("没有证明", "不能推出")):
        structure_suggestions.append("增加“论文没有证明什么”")
    long_paragraphs = [paragraph for paragraph in paragraphs if len(paragraph) > 350]
    if long_paragraphs:
        readability_suggestions.append(f"拆分 {len(long_paragraphs)} 个超过 350 字的长段落")
    if not any(term in markdown for term in ("面试", "学习迁移", "项目迁移")):
        readability_suggestions.append("增加面试或项目迁移价值")
    if not any(term in markdown for term in ("总结", "结论", "重新评价")):
        structure_suggestions.append("结尾需要回收中心论点")
    if _local_image_paths(markdown):
        publication_blocking_issues.append("正文仍包含本地图片路径，发布前必须上传并替换为线上地址")
    all_issues = title_suggestions + opening_suggestions + structure_suggestions + readability_suggestions
    score = max(0, 100 - 7 * len(all_issues))
    report = {
        "score": score,
        "title_suggestions": title_suggestions,
        "opening_suggestions": opening_suggestions,
        "structure_suggestions": structure_suggestions,
        "readability_suggestions": readability_suggestions,
        "publication_blocking_issues": publication_blocking_issues,
        "revision_instructions": all_issues,
    }
    return report, _review_markdown("WeChat Review", report)


def publish_package(markdown: str, paper: PaperMeta, style_mode: str) -> str:
    title = _title(markdown) or paper.title
    digest = _first_paragraph(markdown)[:100]
    tags = list(dict.fromkeys([*paper.categories, "AI论文精读", style_mode]))[:8]
    alternatives = [
        f"重读《{paper.title}》：它真正证明了什么？",
        f"从问题到证据，读懂《{paper.title}》",
        f"《{paper.title}》没有证明什么？",
    ]
    local_images = _local_image_paths(markdown)
    image_status = (
        f"未通过：检测到 {len(local_images)} 个本地图片路径，需上传后替换。"
        if local_images
        else "通过：未检测到本地图片路径。"
    )
    return f"""# Publish Package

## 推荐标题

{title}

## 备用标题

{chr(10).join(f"- {item}" for item in alternatives)}

## 100 字摘要

{digest}

## 公众号导语

本文沿原论文的问题、设计、证据与边界展开，重点解释关键图表支撑了什么，以及哪些结论仍不能从论文中推出。

## 封面图建议

使用论文核心结构图的抽象化重绘或克制的主题封面；避免直接使用低清截图和夸张文字。

## 文末互动问题

如果将论文方法迁移到你的项目中，你会优先验证哪一条核心假设？

## 文章标签

{", ".join(tags)}

## 适合发布平台

微信公众号、知乎专栏、技术团队博客。

## 配图检查

- {image_status}
- 确认所有图片已上传为线上地址。
- 图注包含原论文 Figure / Table 编号与来源页码。
- 每张图都在正文中被解释。

## 发布前 Checklist

- [ ] 技术审稿分数达到 85。
- [ ] 公众号审稿分数达到 85。
- [ ] 原论文结论与后续影响已分开。
- [ ] 本地绝对图片路径已替换。
- [ ] 标题、摘要和正文相互兑现。
"""


def _review_markdown(name: str, report: dict[str, object]) -> str:
    lines = [f"# {name}", "", f"- Score: {report.get('score', 0)}"]
    for key, value in report.items():
        if key == "score" or not isinstance(value, list):
            continue
        lines.extend(["", f"## {key}", *([f"- {item}" for item in value] or ["- None"])])
    return "\n".join(lines) + "\n"


def _title(markdown: str) -> str:
    return next((line[2:].strip() for line in markdown.splitlines() if line.startswith("# ")), "")


def _headings(markdown: str) -> list[str]:
    return re.findall(r"^#{1,3}\s+(.+)$", markdown, flags=re.MULTILINE)


def _first_paragraph(markdown: str) -> str:
    for line in markdown.splitlines():
        text = line.strip()
        if text and not text.startswith(("#", "!", "*", "-", ">")):
            return re.sub(r"[*_`]", "", text)
    return ""


def _article_integrity_issues(markdown: str) -> list[str]:
    issues: list[str] = []
    text = markdown.rstrip()
    if not text:
        return ["正文为空"]
    if text.count("**") % 2:
        issues.append("正文疑似被截断：存在未闭合的加粗标记")
    if text.count("`") % 2:
        issues.append("正文疑似被截断：存在未闭合的代码标记")
    last_line = next((line.strip() for line in reversed(text.splitlines()) if line.strip()), "")
    if re.search(r"(?:\*\*|：|:|，|、|以及|包括|能够|不能|可以|能)$", last_line):
        issues.append("正文疑似被截断：结尾停在未完成的语句")
    return list(dict.fromkeys(issues))


def _local_image_paths(markdown: str) -> list[str]:
    targets = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown)
    return [
        target
        for target in targets
        if re.match(r"^(?:[A-Za-z]:[\\/]|/[^/]|file://)", target.strip(), flags=re.IGNORECASE)
    ]


def _original_paper_scope(markdown: str) -> str:
    history_markers = ("## 后续影响", "## 历史影响", "### 历史回响", "## 历史回响")
    positions = [markdown.find(marker) for marker in history_markers if marker in markdown]
    return markdown[: min(positions)] if positions else markdown
