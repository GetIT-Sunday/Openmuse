from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

RISKY_PHRASES = ("彻底", "革命性", "证明了", "全面领先", "首次", "颠覆", "完全解决")


def analyze(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    paragraphs = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.startswith(("#", "!", "-", "*", ">", "|", "```"))
    ]
    headings = re.findall(r"^#{1,3}\s+(.+)$", text, flags=re.MULTILINE)
    images = re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", text)
    risky = {
        phrase: count
        for phrase in RISKY_PHRASES
        if (count := _unqualified_phrase_count(text, phrase))
    }
    avg_paragraph = round(sum(map(len, paragraphs)) / len(paragraphs), 1) if paragraphs else 0
    return {
        "path": str(path),
        "characters": len(text),
        "paragraphs": len(paragraphs),
        "average_paragraph_characters": avg_paragraph,
        "headings": len(headings),
        "images": len(images),
        "characters_per_image": round(len(text) / len(images), 1) if images else None,
        "paper_link_present": bool(re.search(r"https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/", text)),
        "risky_phrases": risky,
        "warnings": _warnings(text, paragraphs, images, risky),
    }


def _warnings(
    text: str,
    paragraphs: list[str],
    images: list[tuple[str, str]],
    risky: dict[str, int],
) -> list[str]:
    warnings: list[str] = []
    if not images:
        warnings.append("No figures found; a deep-read may be too text-heavy.")
    if images and len(text) / len(images) > 1000:
        warnings.append("Figure density is low for a figure-led deep-read.")
    if paragraphs and max(map(len, paragraphs)) > 300:
        warnings.append("At least one paragraph exceeds 300 characters.")
    if risky:
        warnings.append("Review risky or over-strong phrases.")
    if not re.search(r"https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/", text):
        warnings.append("No arXiv paper link found.")
    return warnings


def _unqualified_phrase_count(text: str, phrase: str) -> int:
    count = 0
    for match in re.finditer(re.escape(phrase), text):
        prefix = text[max(0, match.start() - 30) : match.start()]
        if any(qualifier in prefix for qualifier in ("不能", "不可", "并非", "避免", "不要", "未能")):
            continue
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a Chinese paper WeChat article.")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.path), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
