from __future__ import annotations

import re


PREFERRED_SECTIONS = ("方法", "架构", "模型", "实验", "结果", "发现", "局限")
SOURCE_REF_RE = re.compile(r"\b(?:figure|fig\.?|table)\s*(\d+[a-z]?)", re.IGNORECASE)


def place_figures(markdown: str, figure_paths: list[str]) -> str:
    """Place paper figures near relevant sections and avoid duplicate image blocks."""
    missing = [path for path in figure_paths if f"]({path})" not in markdown]
    if not missing:
        return markdown

    lines = markdown.rstrip().splitlines()
    heading_indexes = [
        index
        for index, line in enumerate(lines)
        if re.match(r"^##\s+", line) and any(section in line for section in PREFERRED_SECTIONS)
    ]

    for figure_index, path in enumerate(missing, start=1):
        block = [
            "",
            f"![论文核心图 {figure_index}]({path})",
            f"*图 {figure_index}：论文原图，建议结合本节内容阅读。*",
            "",
        ]
        if heading_indexes:
            heading_index = heading_indexes[min(figure_index - 1, len(heading_indexes) - 1)]
            next_heading = next(
                (index for index in range(heading_index + 1, len(lines)) if lines[index].startswith("## ")),
                len(lines),
            )
            lines[next_heading:next_heading] = block
            heading_indexes = [index + len(block) if index >= next_heading else index for index in heading_indexes]
        else:
            if figure_index == 1:
                lines.extend(["", "## 论文核心图"])
            lines.extend(block)

    return "\n".join(lines).rstrip() + "\n"


def place_visuals(markdown: str, visuals: list[dict[str, object]]) -> str:
    """Place structured visuals near their first source reference using original captions."""
    missing = [visual for visual in visuals if f"]({visual.get('path', '')})" not in markdown and visual.get("path")]
    if not missing:
        return markdown

    lines = markdown.rstrip().splitlines()
    caption_counts: dict[str, int] = {}
    for visual in missing:
        caption = str(visual.get("caption", "")).strip()
        caption_counts[caption] = caption_counts.get(caption, 0) + 1
    caption_seen: dict[str, int] = {}

    for visual in missing:
        caption = str(visual.get("caption", "")).strip()
        caption_seen[caption] = caption_seen.get(caption, 0) + 1
        display_caption = caption or _fallback_caption(visual)
        if caption_counts.get(caption, 0) > 1:
            display_caption += f"（组件 {caption_seen[caption]}/{caption_counts[caption]}）"

        block = [
            "",
            f"![{_alt_text(display_caption)}]({visual['path']})",
            f"*{display_caption}，原论文第 {visual.get('page', '?')} 页。*",
            "",
        ]
        target = _reference_target(lines, caption)
        if target is None:
            target = _section_target(lines, str(visual.get("kind", "figure")))
        lines[target:target] = block

    return "\n".join(lines).rstrip() + "\n"


def _reference_target(lines: list[str], caption: str) -> int | None:
    match = SOURCE_REF_RE.search(caption)
    if not match:
        return None
    kind = "table" if match.group(0).lower().startswith("table") else "figure"
    number = match.group(1)
    english_kind = r"table" if kind == "table" else r"figure|fig\.?"
    patterns = [
        re.compile(rf"\b(?:{english_kind})\s*{re.escape(number)}\b", re.IGNORECASE),
        re.compile(rf"{'表' if kind == 'table' else '图'}\s*{re.escape(number)}\b"),
    ]
    candidates = []
    for index, line in enumerate(lines):
        if line.lstrip().startswith(("![", "*Figure", "*Table", "*图", "*表")):
            continue
        if any(pattern.search(line) for pattern in patterns):
            candidates.append(index + 1)
    return candidates[-1] if candidates else None


def _section_target(lines: list[str], kind: str) -> int:
    preferred = ("实验", "结果", "关键", "消融") if kind == "table" else ("方法", "架构", "模型", "拆解")
    for index, line in enumerate(lines):
        if line.startswith("## ") and any(name in line for name in preferred):
            return next((i for i in range(index + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return len(lines)


def _fallback_caption(visual: dict[str, object]) -> str:
    label = "表格" if visual.get("kind") == "table" else "图"
    return f"论文{label}"


def _alt_text(caption: str) -> str:
    return caption.replace("[", "").replace("]", "")[:100]
