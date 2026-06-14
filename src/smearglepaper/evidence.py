from __future__ import annotations

import re
from typing import Any

SECTION_RE = re.compile(r"^(?P<number>\d+(?:\.\d+)*)\s+(?P<title>[A-Z][^\n]{2,120})$")
UNNUMBERED_SECTIONS = {"abstract", "introduction", "conclusion", "conclusions", "references", "limitations"}
RESULT_RE = re.compile(
    r"\b(?:achiev(?:e|es|ed)|outperform(?:s|ed)?|result(?:s)? show|we (?:find|observe|show)|"
    r"improv(?:e|es|ed|ement)|better|superior|reduce(?:s|d)?|increase(?:s|d)?|establish(?:es|ed)?)\b",
    re.IGNORECASE,
)
METHOD_RE = re.compile(
    r"\b(?:we (?:propose|present|introduce|use|employ)|our (?:model|method|approach|architecture)|"
    r"consists? of|is composed of)\b",
    re.IGNORECASE,
)
BOUNDARY_RE = re.compile(
    r"\b(?:limitation(?:s)?|future work|we plan to|cannot|does not|however|although)\b",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?:%|x|×)?(?![\w.])", re.IGNORECASE)
VISUAL_REF_RE = re.compile(r"\b(?:figure|fig\.?|table)\s*(\d+[a-z]?)", re.IGNORECASE)
ZH_VISUAL_REF_RE = re.compile(r"(?<!图表)([图表])\s*(\d+[a-z]?)", re.IGNORECASE)


def build_paper_structure(
    pages: list[dict[str, object]],
    visuals: list[dict[str, object]],
    *,
    max_evidence: int = 80,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    sections = extract_sections(pages)
    ledger = build_evidence_ledger(pages, sections, visuals, max_evidence=max_evidence)
    return sections, ledger


def extract_sections(pages: list[dict[str, object]]) -> list[dict[str, object]]:
    sections: list[dict[str, object]] = []
    current: dict[str, Any] | None = None

    for page in pages:
        page_number = int(page.get("page", 0))
        for raw_line in str(page.get("text", "")).splitlines():
            line = " ".join(raw_line.split()).strip()
            heading = _section_heading(line)
            if heading:
                if current:
                    _finish_section(current, page_number)
                    sections.append(current)
                number, title = heading
                section_id = number or _slug(title)
                current = {
                    "id": section_id,
                    "number": number,
                    "title": title,
                    "page_start": page_number,
                    "page_end": page_number,
                    "_lines": [],
                }
            elif current and line:
                current["_lines"].append(line)
                current["page_end"] = page_number

    if current:
        _finish_section(current, int(current["page_end"]))
        sections.append(current)
    return sections


def build_evidence_ledger(
    pages: list[dict[str, object]],
    sections: list[dict[str, object]],
    visuals: list[dict[str, object]],
    *,
    max_evidence: int = 80,
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    seen_excerpts: set[str] = set()

    visual_groups: dict[tuple[int, str], list[dict[str, object]]] = {}
    for visual in visuals:
        caption = str(visual.get("caption", "")).strip()
        if not caption:
            continue
        page = int(visual.get("page", 0))
        visual_groups.setdefault((page, caption), []).append(visual)

    for (page, caption), group in visual_groups.items():
        section = _section_for_page(sections, page)
        entries.append(
            {
                "id": f"evidence-{len(entries) + 1}",
                "kind": "visual",
                "page": page,
                "section_id": section.get("id", ""),
                "section_title": section.get("title", ""),
                "excerpt": caption,
                "visual_ids": [str(visual.get("id", "")) for visual in group],
                "confidence": "high",
            }
        )

    for page in pages:
        page_number = int(page.get("page", 0))
        section = _section_for_page(sections, page_number)
        for sentence in _sentences(str(page.get("text", ""))):
            kind = _evidence_kind(sentence)
            if not kind:
                continue
            excerpt = sentence[:600].strip()
            normalized = _normalize(excerpt)
            if normalized in seen_excerpts:
                continue
            seen_excerpts.add(normalized)
            entries.append(
                {
                    "id": f"evidence-{len(entries) + 1}",
                    "kind": kind,
                    "page": page_number,
                    "section_id": section.get("id", ""),
                    "section_title": section.get("title", ""),
                    "excerpt": excerpt,
                    "visual_ids": _related_visual_ids(sentence, page_number, visuals),
                    "confidence": "high" if kind == "result" and NUMBER_RE.search(sentence) else "medium",
                }
            )
            if len(entries) >= max_evidence:
                return entries
    return entries


def format_evidence_context(parsed: dict[str, object], *, max_entries: int = 32) -> str:
    sections = parsed.get("sections", [])
    ledger = parsed.get("evidence_ledger", [])
    chunks: list[str] = []
    if isinstance(sections, list) and sections:
        chunks.append(
            "原论文章节顺序：\n"
            + "\n".join(
                f"- {item.get('id', '')} {item.get('title', '')}（第 {item.get('page_start', '?')}-{item.get('page_end', '?')} 页）"
                for item in sections
                if isinstance(item, dict)
            )
        )
    if isinstance(ledger, list) and ledger:
        lines = ["可追溯证据单元（引用结论时保留页码、章节或图表定位）："]
        for item in ledger[:max_entries]:
            if not isinstance(item, dict):
                continue
            locator = f"第 {item.get('page', '?')} 页"
            if item.get("section_title"):
                locator += f"｜{item['section_title']}"
            if item.get("visual_ids"):
                locator += f"｜{', '.join(str(value) for value in item['visual_ids'])}"
            lines.append(f"- [{item.get('kind', 'claim')}] {locator}：{item.get('excerpt', '')}")
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks)


def audit_article_evidence(markdown: str, parsed: dict[str, object]) -> dict[str, object]:
    source_text = str(parsed.get("text", ""))
    visuals = parsed.get("visuals", [])
    sections = parsed.get("sections", [])
    ledger = parsed.get("evidence_ledger", [])
    available = bool(source_text or visuals or ledger)
    if not available:
        return {"available": False}

    article_without_urls = re.sub(r"https?://\S+", "", markdown)
    article_without_urls = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", article_without_urls)
    article_without_urls = re.sub(r"^\*图\s*\d+[^*]*论文原图[^*]*\*\s*$", "", article_without_urls, flags=re.MULTILINE)
    section_numbers = set(re.findall(r"(?m)^\s*#{1,6}\s*(\d+(?:\.\d+)+)\b", article_without_urls))
    source_numbers = set(NUMBER_RE.findall(source_text))
    article_numbers = {
        value
        for value in NUMBER_RE.findall(article_without_urls)
        if value not in section_numbers
        and ("." in value or "%" in value or value.lower().endswith("x") or value.endswith("×") or len(value) >= 2)
    }
    unverified_numbers = sorted(article_numbers - source_numbers, key=_number_sort_key)

    available_refs = _available_visual_refs(visuals)
    article_refs = _article_visual_refs(article_without_urls)
    unknown_refs = sorted(article_refs - available_refs)
    known_refs = sorted(article_refs & available_refs)
    return {
        "available": True,
        "section_count": len(sections) if isinstance(sections, list) else 0,
        "evidence_count": len(ledger) if isinstance(ledger, list) else 0,
        "available_visual_references": sorted(available_refs),
        "referenced_visuals": known_refs,
        "unknown_visual_references": unknown_refs,
        "unverified_numbers": unverified_numbers,
        "checks": {
            "numeric_claims_traceable": not unverified_numbers,
            "visual_references_traceable": not unknown_refs,
            "uses_source_visual_reference": not available_refs or bool(known_refs),
        },
    }


def _section_heading(line: str) -> tuple[str, str] | None:
    if len(line) > 140:
        return None
    match = SECTION_RE.match(line)
    if match and not re.search(r"[.!?]$", match.group("title")):
        return match.group("number"), match.group("title").strip()
    if line.lower() in UNNUMBERED_SECTIONS:
        return "", line
    return None


def _finish_section(section: dict[str, Any], page_end: int) -> None:
    section["page_end"] = max(int(section["page_start"]), page_end)
    section["text"] = " ".join(section.pop("_lines"))[:12_000]


def _section_for_page(sections: list[dict[str, object]], page: int) -> dict[str, object]:
    matches = [
        section
        for section in sections
        if int(section.get("page_start", 0)) <= page <= int(section.get("page_end", 0))
    ]
    return matches[-1] if matches else {}


def _sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text)
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text) if 40 <= len(sentence.strip()) <= 900]


def _evidence_kind(sentence: str) -> str:
    if RESULT_RE.search(sentence):
        return "result"
    if METHOD_RE.search(sentence):
        return "method"
    if BOUNDARY_RE.search(sentence):
        return "boundary"
    return ""


def _related_visual_ids(sentence: str, page: int, visuals: list[dict[str, object]]) -> list[str]:
    refs = {f"{kind}-{number.lower()}" for kind, number in _english_visual_refs(sentence)}
    related: list[str] = []
    for visual in visuals:
        visual_id = str(visual.get("id", ""))
        caption_refs = _article_visual_refs(str(visual.get("caption", "")))
        if int(visual.get("page", 0)) == page or refs.intersection(caption_refs):
            related.append(visual_id)
    return related


def _available_visual_refs(visuals: object) -> set[str]:
    refs: set[str] = set()
    if not isinstance(visuals, list):
        return refs
    for visual in visuals:
        if isinstance(visual, dict):
            refs.update(_article_visual_refs(str(visual.get("caption", ""))))
    return refs


def _article_visual_refs(text: str) -> set[str]:
    refs = {f"{kind}-{number.lower()}" for kind, number in _english_visual_refs(text)}
    for kind, number in ZH_VISUAL_REF_RE.findall(text):
        refs.add(f"{'figure' if kind == '图' else 'table'}-{number.lower()}")
    return refs


def _english_visual_refs(text: str) -> list[tuple[str, str]]:
    refs = []
    for match in VISUAL_REF_RE.finditer(text):
        prefix = match.group(0).lower()
        refs.append(("table" if prefix.startswith("table") else "figure", match.group(1)))
    return refs


def _normalize(text: str) -> str:
    return re.sub(r"\W+", "", text.lower())


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _number_sort_key(value: str) -> tuple[float, str]:
    try:
        return float(re.sub(r"[^0-9.]", "", value)), value
    except ValueError:
        return 0.0, value
