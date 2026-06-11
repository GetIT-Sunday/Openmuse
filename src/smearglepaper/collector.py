from __future__ import annotations

import datetime as dt
import re
import ssl
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from urllib.error import HTTPError
from urllib.error import URLError

from .models import PaperMeta

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_REQUEST_INTERVAL_SECONDS = 3


class ArxivRateLimitError(RuntimeError):
    def __init__(self, retry_after: str | None = None) -> None:
        message = "arXiv is rate limiting requests. Wait a few minutes and try again."
        if retry_after:
            message += f" Retry-After: {retry_after} seconds."
        super().__init__(message)


class ArxivTemporaryError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"arXiv request failed temporarily: {reason}. Try again later or use a narrower topic.")


TOPIC_QUERIES: dict[str, list[str]] = {
    "latest_ai": ["cat:cs.AI OR cat:cs.CL OR cat:cs.LG OR cat:cs.CV"],
    "agents": [
        'cat:cs.AI AND (all:"language agent" OR all:"llm agent" OR all:"multi-agent" OR all:agentic OR all:"tool use" OR all:"autonomous agent")',
        'cat:cs.CL AND (all:"language agent" OR all:"llm agent" OR all:"multi-agent" OR all:agentic OR all:"tool use" OR all:"autonomous agent")',
        'cat:cs.LG AND (all:"language agent" OR all:"llm agent" OR all:"multi-agent" OR all:agentic OR all:"tool use" OR all:"autonomous agent")',
    ],
    "nlp": ["cat:cs.CL"],
    "nlp_semantics": [
        "cat:cs.CL AND (all:semantic OR all:semantics OR all:meaning OR all:semantic_parsing OR all:textual_entailment)",
    ],
    "nlp_syntax": [
        "cat:cs.CL AND (all:syntax OR all:syntactic OR all:grammar OR all:grammatical OR all:parsing)",
    ],
    "nlp_pragmatics": [
        "cat:cs.CL AND (all:pragmatics OR all:pragmatic OR all:discourse OR all:implicature OR all:context)",
    ],
    "nlp_semantics_syntax_pragmatics": [
        "cat:cs.CL AND (all:semantic OR all:semantics OR all:meaning OR all:semantic_parsing)",
        "cat:cs.CL AND (all:syntax OR all:syntactic OR all:grammar OR all:parsing)",
        "cat:cs.CL AND (all:pragmatics OR all:pragmatic OR all:discourse OR all:context)",
    ],
}


class ArxivCollector:
    def collect(self, topic: str | None, query: str | None, days: int, max_results: int) -> list[PaperMeta]:
        searches = [query] if query else topic_queries(topic)
        papers_by_id: dict[str, PaperMeta] = {}
        rate_limited = False
        for index, search in enumerate(searches):
            if index:
                time.sleep(ARXIV_REQUEST_INTERVAL_SECONDS)
            try:
                for paper in self._collect_one(search, days, max_results):
                    papers_by_id[paper.paper_id] = paper
            except (ArxivRateLimitError, ArxivTemporaryError):
                rate_limited = True
                if not papers_by_id:
                    raise
                break
        if rate_limited and papers_by_id:
            return sorted(papers_by_id.values(), key=lambda item: item.published_at, reverse=True)
        return sorted(papers_by_id.values(), key=lambda item: item.published_at, reverse=True)

    def _collect_one(self, search: str, days: int, max_results: int) -> list[PaperMeta]:
        params = urllib.parse.urlencode(
            {
                "search_query": search,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "start": 0,
                "max_results": max_results,
            }
        )
        try:
            request = urllib.request.Request(f"{ARXIV_API}?{params}", headers={"User-Agent": "SmearglePaper/0.1"})
            with urllib.request.urlopen(request, timeout=30, context=ssl_context()) as response:
                xml = response.read()
        except HTTPError as exc:
            if exc.code == 429:
                raise ArxivRateLimitError(exc.headers.get("Retry-After")) from exc
            raise
        except TimeoutError as exc:
            raise ArxivTemporaryError("read timed out") from exc
        except URLError as exc:
            raise ArxivTemporaryError(str(exc.reason)) from exc
        papers = parse_arxiv_feed(xml)
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
        return [paper for paper in papers if _parse_date(paper.published_at) >= cutoff]


def topic_query(topic: str | None) -> str:
    return topic_queries(topic)[0]


def topic_queries(topic: str | None) -> list[str]:
    if not topic:
        return TOPIC_QUERIES["latest_ai"]
    return TOPIC_QUERIES.get(topic, [topic])


def topic_names() -> list[str]:
    return sorted(TOPIC_QUERIES)


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def parse_arxiv_feed(xml: bytes) -> list[PaperMeta]:
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(xml)
    papers: list[PaperMeta] = []
    for entry in root.findall("atom:entry", ns):
        paper_url = _text(entry, "atom:id", ns)
        paper_id = paper_url.rstrip("/").split("/")[-1]
        pdf_url = None
        for link in entry.findall("atom:link", ns):
            if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                pdf_url = link.attrib.get("href")
        papers.append(
            PaperMeta(
                paper_id=paper_id,
                title=_compact(_text(entry, "atom:title", ns)),
                authors=[_text(author, "atom:name", ns) for author in entry.findall("atom:author", ns)],
                abstract=_compact(_text(entry, "atom:summary", ns)),
                source="arxiv",
                url=paper_url,
                pdf_url=pdf_url or paper_url.replace("/abs/", "/pdf/"),
                published_at=_text(entry, "atom:published", ns),
                updated_at=_text(entry, "atom:updated", ns),
                categories=[cat.attrib.get("term", "") for cat in entry.findall("atom:category", ns)],
                keywords=[],
            )
        )
    return papers


def _text(node: ET.Element, selector: str, ns: dict[str, str]) -> str:
    found = node.find(selector, ns)
    return found.text.strip() if found is not None and found.text else ""


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _parse_date(value: str) -> dt.datetime:
    if not value:
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
