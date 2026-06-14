from __future__ import annotations

from pathlib import Path

from .config import DATA_DIR, env, runtime_settings
from .storage import read_json

AGENTS = ["scout", "ranker", "reader", "writer", "writing_agent", "editor", "publisher", "github_tracker", "notifier", "orchestrator"]


def check_agents(agent: str = "all") -> dict[str, object]:
    selected = AGENTS if agent == "all" else [agent]
    unknown = [name for name in selected if name not in AGENTS]
    if unknown:
        raise ValueError(f"Unknown agent: {', '.join(unknown)}. Available: {', '.join(AGENTS)}")
    checks = {name: CHECKS[name]() for name in selected}
    return {
        "ok": all(item["ok"] for item in checks.values()),
        "agent": agent,
        "checks": checks,
    }


def _check_scout() -> dict[str, object]:
    latest = DATA_DIR / "papers" / "latest.json"
    blogs = DATA_DIR / "blogs" / "latest.json"
    papers = read_json(latest, [])
    blog_payload = read_json(blogs, {})
    warnings = []
    if not papers:
        warnings.append("No local paper collection found.")
    if not blog_payload or int(blog_payload.get("count", 0)) == 0:
        warnings.append("No recent blog posts are available locally.")
    return _status(
        bool(papers) or bool(blog_payload),
        "Scout Agent",
        {
            "role": "collect arXiv papers and AI blog posts",
            "papers_file": str(latest),
            "papers_count": len(papers) if isinstance(papers, list) else 0,
            "blogs_file": str(blogs),
            "blogs_count": int(blog_payload.get("count", 0)) if isinstance(blog_payload, dict) else 0,
            "next": "Run collect --topic agents and collect-blogs --topic agents if counts are 0.",
        },
        warnings,
    )


def _check_ranker() -> dict[str, object]:
    ranked = DATA_DIR / "papers" / "ranked_latest.json"
    rows = read_json(ranked, [])
    return _status(
        bool(rows),
        "Ranker Agent",
        {
            "role": "score and select candidate papers",
            "ranked_file": str(ranked),
            "ranked_count": len(rows) if isinstance(rows, list) else 0,
            "next": "Run rank --top-k 10 after collecting papers.",
        },
    )


def _check_reader() -> dict[str, object]:
    parsed_dir = DATA_DIR / "parsed"
    figure_dir = DATA_DIR / "figures"
    parsed_files = _glob(parsed_dir, "*.json")
    figure_files = _glob(figure_dir, "*/*")
    warnings = []
    if not figure_files:
        warnings.append("No extracted figures are available; reports may be text-only.")
    elif any("page_1_preview" in str(path) for path in figure_files):
        warnings.append("Some figure candidates are page previews, not semantic figure crops.")
    return _status(
        bool(parsed_files),
        "Reader Agent",
        {
            "role": "download PDFs, parse text, and extract/crop figure candidates",
            "parsed_dir": str(parsed_dir),
            "parsed_count": len(parsed_files),
            "figure_dir": str(figure_dir),
            "figure_count": len(figure_files),
            "next": "Run read --paper-id <paper-id>. Figure count 0 means the report will be text-only.",
        },
        warnings,
    )


def _check_writer() -> dict[str, object]:
    articles = _glob(DATA_DIR / "articles", "*.json")
    settings = runtime_settings()
    llm = settings["llm"]
    provider = str(llm["provider"])
    provider_settings = llm.get(provider, {}) if provider != "none" else {}
    llm_ready = bool(provider_settings.get("api_key_configured") and provider_settings.get("base_url"))
    return _status(
        bool(articles) and llm_ready,
        "Writer Agent",
        {
            "role": "turn paper notes into Chinese Markdown/HTML articles",
            "article_count": len(articles),
            "llm_provider": provider,
            "llm_model": provider_settings.get("model", ""),
            "llm_base_url_configured": bool(provider_settings.get("base_url")),
            "llm_api_key_configured": bool(provider_settings.get("api_key_configured")),
            "next": "Run check-llm if LLM fields are configured but writing fails.",
        },
    )


def _check_writing_agent() -> dict[str, object]:
    runs = _glob(DATA_DIR / "agent_runs" / "writing", "*/manifest.json")
    final_articles = _glob(DATA_DIR / "articles", "*.agent.json")
    settings = runtime_settings()
    llm = settings["llm"]
    provider = str(llm["provider"])
    provider_settings = llm.get(provider, {}) if provider != "none" else {}
    llm_ready = bool(provider_settings.get("api_key_configured") and provider_settings.get("base_url"))
    return _status(
        llm_ready,
        "Paper Writing Agent",
        {
            "role": "plan, draft, evidence-review, revise, and finalize paper explanations",
            "run_count": len(runs),
            "final_article_count": len(final_articles),
            "llm_provider": provider,
            "llm_ready": llm_ready,
            "next": "Run writing-agent --paper-id <paper-id> after ingest-paper.",
        },
        [] if runs else ["No Paper Writing Agent run has been completed yet."],
    )


def _check_editor() -> dict[str, object]:
    articles = _glob(DATA_DIR / "articles", "*.json")
    optimized = _glob(DATA_DIR / "articles", "*.optimized.json")
    settings = runtime_settings()
    llm = settings["llm"]
    provider = str(llm["provider"])
    provider_settings = llm.get(provider, {}) if provider != "none" else {}
    warnings = []
    if not optimized:
        warnings.append("No optimized article has been generated yet.")
    return _status(
        bool(articles),
        "Editor Agent",
        {
            "role": "review article quality and improve drafts",
            "article_count": len(articles),
            "optimized_count": len(optimized),
            "llm_ready": bool(provider_settings.get("api_key_configured") and provider_settings.get("base_url")),
            "next": "Run review-article, then improve-article for drafts that need revision.",
        },
        warnings,
    )


def _check_publisher() -> dict[str, object]:
    settings = runtime_settings()
    wechat = settings["wechat"]
    covers = _glob(DATA_DIR / "covers", "*.png")
    warnings = ["WeChat credentials can be checked statically, but IP whitelist must be verified by a real API call."]
    return _status(
        bool(wechat["app_id_configured"]) and bool(wechat["app_secret_configured"]) and bool(covers),
        "Publisher Agent",
        {
            "role": "render WeChat-ready HTML, upload cover/content images, and create drafts",
            "wechat_app_id_configured": bool(wechat["app_id_configured"]),
            "wechat_app_secret_configured": bool(wechat["app_secret_configured"]),
            "cover_count": len(covers),
            "next": "If WeChat returns 40164, add the current outbound IP to the Official Account IP whitelist.",
        },
        warnings,
    )


def _check_github_tracker() -> dict[str, object]:
    settings = runtime_settings()
    github = settings["github"]
    latest = DATA_DIR / "github" / "latest.json"
    snapshots = _glob(DATA_DIR / "github", "snapshot-*.json")
    warnings = []
    if not github["token_configured"]:
        warnings.append("No GITHUB_TOKEN configured; API rate limit is 60 requests/hour.")
    return _status(
        True,
        "GitHub Tracker Agent",
        {
            "role": "collect trending GitHub repos and calculate star growth",
            "github_token_configured": bool(github["token_configured"]),
            "latest_file": str(latest),
            "snapshot_count": len(snapshots),
            "next": "Run github-stars to collect trending repos.",
        },
        warnings,
    )


def _check_notifier() -> dict[str, object]:
    settings = runtime_settings()
    feishu = settings["feishu"]
    warnings = []
    if not feishu["webhook_configured"]:
        warnings.append("No FEISHU_WEBHOOK_URL configured; notifications will run in dry-run mode.")
    return _status(
        True,
        "Notifier Agent",
        {
            "role": "send notifications to Feishu/Lark webhook",
            "feishu_webhook_configured": bool(feishu["webhook_configured"]),
            "next": "Configure FEISHU_WEBHOOK_URL to enable real notifications.",
        },
        warnings,
    )


def _check_orchestrator() -> dict[str, object]:
    report_dir = DATA_DIR / "runs"
    reports = _glob(report_dir, "agent-run-*.json")
    return _status(
        True,
        "Orchestrator Agent",
        {
            "role": "run collect, rank, read, write, review, improve, and draft steps as one workflow",
            "report_dir": str(report_dir),
            "run_report_count": len(reports),
            "next": "Run agent-run --topic agents --days 30 --top-k 5.",
        },
    )


def _status(ok: bool, name: str, detail: dict[str, object], warnings: list[str] | None = None) -> dict[str, object]:
    return {
        "ok": ok,
        "name": name,
        "status": "ready" if ok else "needs_attention",
        "detail": detail,
        "warnings": warnings or [],
    }


def _glob(path: Path, pattern: str) -> list[Path]:
    if not path.exists():
        return []
    return sorted(path.glob(pattern))


CHECKS = {
    "scout": _check_scout,
    "ranker": _check_ranker,
    "reader": _check_reader,
    "writer": _check_writer,
    "writing_agent": _check_writing_agent,
    "editor": _check_editor,
    "publisher": _check_publisher,
    "github_tracker": _check_github_tracker,
    "notifier": _check_notifier,
    "orchestrator": _check_orchestrator,
}
