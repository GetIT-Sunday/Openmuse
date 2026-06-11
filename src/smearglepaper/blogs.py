from __future__ import annotations

import datetime as dt
import re
import urllib.request
import xml.etree.ElementTree as ET
from urllib.error import HTTPError, URLError

from .collector import ssl_context
from .models import BlogPost

DEFAULT_FEEDS: dict[str, str] = {
    "OpenAI": "https://openai.com/news/rss.xml",
    "Anthropic": "https://www.anthropic.com/news/rss.xml",
    "LangChain": "https://blog.langchain.com/rss/",
    "LlamaIndex": "https://www.llamaindex.ai/blog/rss.xml",
    "Microsoft Research": "https://www.microsoft.com/en-us/research/feed/",
    "Google AI Blog": "https://blog.google/technology/ai/rss/",
    "Hugging Face": "https://huggingface.co/blog/feed.xml",
    "Meta AI": "https://ai.meta.com/blog/rss/",
    "DeepMind": "https://deepmind.google/blog/rss.xml",
    "Interconnects": "https://www.interconnects.ai/feed",
    "Simon Willison": "https://simonwillison.net/atom/everything/",
    "The Batch": "https://www.deeplearning.ai/the-batch/feed/",
}

TOPIC_KEYWORDS: dict[str, list[str]] = {
    "agents": [
        "agent",
        "agents",
        "agentic",
        "multi-agent",
        "multi agent",
        "tool use",
        "computer use",
        "mcp",
        "workflow",
        "autonomous",
    ],
}


class BlogCollector:
    def collect(
        self,
        topic: str | None,
        query: str | None,
        days: int,
        max_results: int,
        feeds: list[str] | None = None,
    ) -> dict[str, object]:
        keywords = _keywords(topic, query)
        feed_map = _feed_map(feeds)
        posts: list[BlogPost] = []
        errors: list[dict[str, str]] = []
        for source, url in feed_map.items():
            try:
                posts.extend(_parse_feed(source, url))
            except (HTTPError, URLError, TimeoutError, ET.ParseError) as exc:
                errors.append({"source": source, "url": url, "error": str(exc)})
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
        filtered = [
            post
            for post in posts
            if _parse_date(post.published_at) >= cutoff and _matches(post, keywords)
        ]
        filtered.sort(key=lambda post: _parse_date(post.published_at), reverse=True)
        return {
            "count": len(filtered[:max_results]),
            "topic": topic or query or "agents",
            "keywords": keywords,
            "posts": [post.to_dict() for post in filtered[:max_results]],
            "errors": errors,
        }


def _parse_feed(source: str, url: str) -> list[BlogPost]:
    request = urllib.request.Request(url, headers={"User-Agent": "SmearglePaper/0.1"})
    with urllib.request.urlopen(request, timeout=30, context=ssl_context()) as response:
        xml = response.read()
    root = ET.fromstring(xml)
    if root.tag.endswith("rss"):
        return _parse_rss(source, root)
    return _parse_atom(source, root)


def _parse_rss(source: str, root: ET.Element) -> list[BlogPost]:
    posts = []
    channel = root.find("channel")
    if channel is None:
        return posts
    for item in channel.findall("item"):
        posts.append(
            BlogPost(
                title=_compact(_child_text(item, "title")),
                url=_compact(_child_text(item, "link")),
                source=source,
                published_at=_compact(_child_text(item, "pubDate")),
                summary=_compact(_strip_html(_child_text(item, "description"))),
            )
        )
    return posts


def _parse_atom(source: str, root: ET.Element) -> list[BlogPost]:
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    posts = []
    entries = root.findall("atom:entry", ns) or root.findall("entry")
    for entry in entries:
        url = ""
        for link in entry.findall("atom:link", ns) or entry.findall("link"):
            if link.attrib.get("href"):
                url = link.attrib["href"]
                break
        posts.append(
            BlogPost(
                title=_compact(_text(entry, "atom:title", ns) or _child_text(entry, "title")),
                url=url,
                source=source,
                published_at=_compact(_text(entry, "atom:published", ns) or _text(entry, "atom:updated", ns) or _child_text(entry, "published")),
                summary=_compact(_strip_html(_text(entry, "atom:summary", ns) or _text(entry, "atom:content", ns))),
            )
        )
    return posts


def _keywords(topic: str | None, query: str | None) -> list[str]:
    if query:
        return [part.lower() for part in re.split(r"[,，]\s*|\s+OR\s+|\s+", query) if part.strip()]
    return TOPIC_KEYWORDS.get(topic or "agents", TOPIC_KEYWORDS["agents"])


def _feed_map(feeds: list[str] | None) -> dict[str, str]:
    if not feeds:
        return DEFAULT_FEEDS
    return {f"feed_{index}": url for index, url in enumerate(feeds, start=1)}


def _matches(post: BlogPost, keywords: list[str]) -> bool:
    haystack = f"{post.title} {post.summary}".lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def _parse_date(value: str) -> dt.datetime:
    if not value:
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    try:
        from email.utils import parsedate_to_datetime

        parsed = parsedate_to_datetime(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)


def _text(node: ET.Element, selector: str, ns: dict[str, str]) -> str:
    found = node.find(selector, ns)
    return found.text.strip() if found is not None and found.text else ""


def _child_text(node: ET.Element, name: str) -> str:
    found = node.find(name)
    return found.text.strip() if found is not None and found.text else ""


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value)


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
