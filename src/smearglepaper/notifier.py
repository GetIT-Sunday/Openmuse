from __future__ import annotations

import json
import ssl
import urllib.request

from .collector import ssl_context
from .config import env


class FeishuNotifier:
    def __init__(self, webhook_url: str | None = None, dry_run: bool = False) -> None:
        self.webhook_url = webhook_url or env("FEISHU_WEBHOOK_URL")
        self.dry_run = dry_run or not self.webhook_url

    def notify(self, title: str, content: str, msg_type: str = "interactive") -> dict[str, object]:
        if self.dry_run:
            return {"ok": True, "dry_run": True, "title": title, "content": content, "msg_type": msg_type}
        if msg_type == "text":
            payload = _text_payload(content)
        else:
            payload = _card_payload(title, content)
        return _send_webhook(self.webhook_url, payload)

    def notify_papers(self, title: str, papers: list[dict[str, object]], top_k: int = 5) -> dict[str, object]:
        lines = []
        for i, paper in enumerate(papers[:top_k], 1):
            paper_title = paper.get("title", "")
            url = paper.get("url", "")
            score = paper.get("score", "")
            lines.append(f"{i}. [{paper_title}]({url})  (score: {score})")
        content = "\n".join(lines) or "No papers collected."
        return self.notify(title, content)

    def notify_blogs(self, title: str, posts: list[dict[str, object]], top_k: int = 5) -> dict[str, object]:
        lines = []
        for i, post in enumerate(posts[:top_k], 1):
            post_title = post.get("title", "")
            url = post.get("url", "")
            source = post.get("source", "")
            lines.append(f"{i}. [{post_title}]({url})  ({source})")
        content = "\n".join(lines) or "No blog posts collected."
        return self.notify(title, content)

    def notify_github_stars(self, title: str, repos: list[dict[str, object]], top_k: int = 10) -> dict[str, object]:
        lines = []
        for i, repo in enumerate(repos[:top_k], 1):
            full_name = repo.get("full_name", "")
            url = repo.get("url", "")
            stars = repo.get("stars", 0)
            growth = repo.get("weekly_star_growth")
            growth_str = f" (+{growth})" if growth is not None else ""
            lines.append(f"{i}. [{full_name}]({url})  {stars} stars{growth_str}")
        content = "\n".join(lines) or "No trending repos found."
        return self.notify(title, content)


def _card_payload(title: str, content: str) -> dict:
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue",
            },
            "elements": [
                {"tag": "markdown", "content": content},
            ],
        },
    }


def _text_payload(content: str) -> dict:
    return {
        "msg_type": "text",
        "content": {"text": content},
    }


def _send_webhook(webhook_url: str, payload: dict) -> dict[str, object]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15, context=ssl_context()) as response:
            body = json.loads(response.read())
            return {"ok": body.get("code") == 0 or body.get("StatusCode") == 0, "response": body}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
