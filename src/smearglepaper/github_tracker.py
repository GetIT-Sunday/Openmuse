from __future__ import annotations

import datetime as dt
import json
import ssl
import urllib.parse
import urllib.request
from pathlib import Path

from .collector import ssl_context
from .config import DATA_DIR, env
from .models import GitHubRepo
from .storage import ensure_parent, read_json, write_json

GITHUB_API = "https://api.github.com"
GITHUB_DIR = DATA_DIR / "github"


class GitHubTracker:
    def collect_trending(
        self,
        language: str | None = None,
        since: str = "weekly",
        max_results: int = 20,
    ) -> list[GitHubRepo]:
        created_after = _since_date(since)
        query_parts = [f"created:>{created_after}", "stars:>10"]
        if language:
            query_parts.append(f"language:{language}")
        query = " ".join(query_parts)

        params = urllib.parse.urlencode(
            {"q": query, "sort": "stars", "order": "desc", "per_page": min(max_results, 100)}
        )
        url = f"{GITHUB_API}/search/repositories?{params}"
        data = _github_get(url)

        now = dt.datetime.now(dt.timezone.utc).isoformat()
        repos: list[GitHubRepo] = []
        for item in data.get("items", [])[:max_results]:
            repos.append(
                GitHubRepo(
                    owner=item["owner"]["login"],
                    name=item["name"],
                    full_name=item["full_name"],
                    description=item.get("description") or "",
                    stars=item["stargazers_count"],
                    forks=item["forks_count"],
                    language=item.get("language") or "",
                    url=item["html_url"],
                    collected_at=now,
                )
            )
        return repos

    def save_snapshot(self, repos: list[GitHubRepo]) -> Path:
        date_str = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
        path = GITHUB_DIR / f"snapshot-{date_str}.json"
        write_json(path, [repo.to_dict() for repo in repos])
        return path

    def load_previous_snapshot(self, skip_today: bool = True) -> list[GitHubRepo] | None:
        if not GITHUB_DIR.exists():
            return None
        snapshots = sorted(GITHUB_DIR.glob("snapshot-*.json"), reverse=True)
        today_str = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
        for snapshot in snapshots:
            if skip_today and today_str in snapshot.name:
                continue
            data = read_json(snapshot, [])
            if data:
                return [GitHubRepo.from_dict(item) for item in data]
        return None

    def calculate_growth(
        self,
        current: list[GitHubRepo],
        previous: list[GitHubRepo] | None,
    ) -> list[GitHubRepo]:
        if not previous:
            return sorted(current, key=lambda r: r.stars, reverse=True)

        prev_map = {repo.full_name: repo for repo in previous}
        for repo in current:
            prev = prev_map.get(repo.full_name)
            if prev:
                repo.weekly_star_growth = repo.stars - prev.stars
            else:
                repo.weekly_star_growth = None

        return sorted(current, key=lambda r: r.weekly_star_growth or 0, reverse=True)

    def collect_with_growth(
        self,
        language: str | None = None,
        since: str = "weekly",
        max_results: int = 20,
    ) -> dict[str, object]:
        current = self.collect_trending(language, since, max_results)
        previous = self.load_previous_snapshot()
        ranked = self.calculate_growth(current, previous)
        self.save_snapshot(current)
        return {
            "count": len(ranked),
            "language": language,
            "since": since,
            "has_previous_snapshot": previous is not None,
            "repos": [repo.to_dict() for repo in ranked],
        }


def _github_get(url: str) -> dict:
    headers = {"User-Agent": "SmearglePaper/0.1", "Accept": "application/vnd.github.v3+json"}
    token = env("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30, context=ssl_context()) as response:
        return json.loads(response.read())


def _since_date(since: str) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    if since == "daily":
        target = now - dt.timedelta(days=1)
    elif since == "monthly":
        target = now - dt.timedelta(days=30)
    else:
        target = now - dt.timedelta(days=7)
    return target.strftime("%Y-%m-%d")
