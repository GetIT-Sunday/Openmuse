from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: T) -> T:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: object) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_dated_json(directory: Path, prefix: str, payload: object, *, date: str | None = None) -> Path:
    """Write a date-suffixed JSON file and update latest.json as a copy.

    Returns the path to the dated file.
    Example: write_dated_json(data/papers, "arxiv", [...]) → data/papers/arxiv-2026-06-08.json
    """
    today = date or dt.date.today().isoformat()
    dated_path = directory / f"{prefix}-{today}.json"
    latest_path = directory / "latest.json"
    write_json(dated_path, payload)
    write_json(latest_path, payload)
    return dated_path


def slugify(value: str, fallback: str = "paper") -> str:
    allowed = []
    for ch in value.lower():
        if ch.isalnum():
            allowed.append(ch)
        elif ch in {" ", "-", "_", ".", ":"}:
            allowed.append("-")
    slug = "".join(allowed).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:90] or fallback

