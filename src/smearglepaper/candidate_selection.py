from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from .config import DATA_DIR
from .models import PaperMeta
from .storage import read_json

RANKING_PROFILES = {"frontier", "classic", "engineering", "balanced"}


def select_candidates(
    ranked_rows: list[dict[str, object]],
    *,
    profile: str = "balanced",
    limit: int = 3,
    history_path: Path | None = None,
) -> list[dict[str, object]]:
    if profile not in RANKING_PROFILES:
        raise ValueError(f"Unknown ranking profile: {profile}")
    published_ids = known_paper_ids(history_path)
    candidates: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in ranked_rows:
        paper_payload = row.get("paper")
        if not isinstance(paper_payload, dict):
            continue
        paper = PaperMeta.from_dict(paper_payload)
        identity = canonical_paper_id(paper.paper_id or paper.url)
        if not identity or identity in seen or identity in published_ids:
            continue
        seen.add(identity)
        candidates.append(_candidate(paper, row, profile))
    candidates.sort(key=lambda item: float(item["score"]), reverse=True)
    return candidates[:limit]


def known_paper_ids(history_path: Path | None = None) -> set[str]:
    paths = [
        history_path or DATA_DIR.parent / "memory" / "article_history.json",
        DATA_DIR / "published_index.json",
    ]
    identities: set[str] = set()
    for path in paths:
        payload = read_json(path, [])
        if isinstance(payload, dict):
            for key in payload:
                identity = canonical_paper_id(str(key))
                if identity:
                    identities.add(identity)
            rows = list(payload.values())
        elif isinstance(payload, list):
            rows = payload
        else:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if path.name == "article_history.json" and not row.get("ready", False):
                continue
            value = row.get("paper_id") or row.get("id") or row.get("url")
            identity = canonical_paper_id(str(value or ""))
            if identity:
                identities.add(identity)
    return identities


def canonical_paper_id(value: str) -> str:
    candidate = value.rstrip("/").split("/")[-1].removesuffix(".pdf")
    return re.sub(r"v\d+$", "", candidate, flags=re.IGNORECASE).lower()


def _candidate(paper: PaperMeta, row: dict[str, object], profile: str) -> dict[str, object]:
    relevance = min(float(row.get("score", 0)) / 10.0, 1.0)
    freshness = _freshness(paper.published_at)
    explainability = min(len(paper.abstract.strip()) / 800.0, 1.0)
    ai_category = 1.0 if any(category.startswith(("cs.AI", "cs.CL", "cs.LG", "cs.CV")) for category in paper.categories) else 0.0
    weights = {
        "frontier": {"relevance": 0.35, "freshness": 0.40, "explainability": 0.15, "ai_category": 0.10},
        "classic": {"relevance": 0.35, "freshness": 0.05, "explainability": 0.35, "ai_category": 0.25},
        "engineering": {"relevance": 0.40, "freshness": 0.15, "explainability": 0.30, "ai_category": 0.15},
        "balanced": {"relevance": 0.40, "freshness": 0.25, "explainability": 0.20, "ai_category": 0.15},
    }[profile]
    signals = {
        "relevance": round(relevance, 3),
        "freshness": round(freshness, 3),
        "explainability": round(explainability, 3),
        "ai_category": round(ai_category, 3),
    }
    score = round(sum(signals[name] * weight for name, weight in weights.items()) * 100, 2)
    reasons = list(row.get("reasons", []))
    reasons.extend(_recommendation_reasons(signals))
    return {
        "paper_id": paper.paper_id,
        "canonical_id": canonical_paper_id(paper.paper_id or paper.url),
        "title": paper.title,
        "authors": paper.authors,
        "abstract": paper.abstract,
        "url": paper.url,
        "pdf_url": paper.pdf_url,
        "published_at": paper.published_at,
        "categories": paper.categories,
        "score": score,
        "score_breakdown": signals,
        "ranking_profile": profile,
        "recommendation_reasons": reasons,
        "one_sentence_contribution": _one_sentence(paper.abstract),
        "uncertainty": ["citation_count_unavailable", "github_project_not_verified"],
        "publication_status": "not_seen_in_local_history",
    }


def _freshness(value: str) -> float:
    try:
        published = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return 0.0
    age_days = max((dt.datetime.now(dt.timezone.utc) - published).days, 0)
    return max(0.0, 1.0 - age_days / 365.0)


def _recommendation_reasons(signals: dict[str, float]) -> list[str]:
    labels = {
        "relevance": "与研究方向高度相关",
        "freshness": "发布时间较新",
        "explainability": "摘要信息充分，适合拆解",
        "ai_category": "属于核心 AI 研究分类",
    }
    return [labels[name] for name, score in signals.items() if score >= 0.7]


def _one_sentence(abstract: str) -> str:
    text = " ".join(abstract.split())
    if not text:
        return "摘要信息不足，需要读取全文后判断贡献。"
    sentence = re.split(r"(?<=[.!?。！？])\s+", text, maxsplit=1)[0]
    return sentence[:240]
