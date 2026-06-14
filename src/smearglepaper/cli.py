from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agents import AGENTS, check_agents
from .collector import ArxivRateLimitError, ArxivTemporaryError, topic_names
from .config import DATA_DIR, runtime_settings
from .editor import improve_article_file
from .llm import check_llm_connection
from .models import PaperMeta
from .scout import run_scout_review
from .storage import read_json
from .wechat import WechatClient
from .workflow import SmearglePaperWorkflow

# Old name → new name (deprecated aliases)
_ALIASES: dict[str, str] = {
    "collect": "collect-arxiv",
    "rank": "rank-papers",
    "read": "ingest-paper",
    "write": "generate-article",
    "agent-check": "check-status",
    "check-agents": "check-status",
    "wechat-publish": "create-wechat-draft",
    "publish-wechat": "create-wechat-draft",
    "wechat-update-draft": "update-wechat-draft",
    "github-stars": "collect-github",
    "notify": "notify-feishu",
}


def _warn_deprecated(old: str, new: str) -> None:
    print(f"Warning: '{old}' is deprecated, use '{new}' instead.", file=sys.stderr)


def _add_alias(sub: argparse._SubParsersAction, old_name: str, new_name: str, help_text: str) -> argparse.ArgumentParser:
    """Create an alias parser that maps old_name to new_name."""
    alias = sub.add_parser(old_name, help=f"(deprecated) {help_text}")
    alias.set_defaults(_alias_target=new_name)
    return alias


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smearglepaper", description="AI paper to Chinese article draft automation")
    sub = parser.add_subparsers(dest="command", required=True)

    # --- product-level task agent ---
    product_agent = sub.add_parser("agent", help="Create a durable product task from a natural-language request")
    product_agent.add_argument("request")
    product_source = product_agent.add_mutually_exclusive_group()
    product_source.add_argument("--paper-id")
    product_source.add_argument("--paper-url")
    product_source.add_argument("--article-json", help="Use an existing publish-ready article")

    sub.add_parser("tasks", help="List durable product tasks")

    task_show = sub.add_parser("task-show", help="Show one durable product task")
    task_show.add_argument("--task-id", required=True)

    task_approve = sub.add_parser("task-approve", help="Approve a waiting product task gate")
    task_approve.add_argument("--task-id", required=True)
    task_approve.add_argument("--gate", required=True, choices=["topic", "publish"])

    task_resume = sub.add_parser("task-resume", help="Resume a durable product task")
    task_resume.add_argument("--task-id", required=True)
    task_resume.add_argument("--real-wechat", action="store_true", help="Allow a previously approved task to create or update a real WeChat draft")

    # --- collect-arxiv (was: collect) ---
    collect = sub.add_parser("collect-arxiv", help="Collect recent arXiv papers")
    collect.add_argument("--topic", help=f"Preset topic. Available: {', '.join(topic_names())}")
    collect.add_argument("--query")
    collect.add_argument("--days", type=int, default=7)
    collect.add_argument("--max-results", type=int, default=50)

    collect_alias = _add_alias(sub, "collect", "collect-arxiv", "Collect recent arXiv papers")
    collect_alias.add_argument("--topic", help=f"Preset topic. Available: {', '.join(topic_names())}")
    collect_alias.add_argument("--query")
    collect_alias.add_argument("--days", type=int, default=7)
    collect_alias.add_argument("--sources", nargs="+", default=["arxiv"])
    collect_alias.add_argument("--max-results", type=int, default=50)

    # --- scout-run ---
    scout = sub.add_parser("scout-run", help="Run Scout Agent and write a human-reviewable collection report")
    scout.add_argument("--topic", default="agents", help=f"Preset topic. Available: {', '.join(topic_names())}")
    scout.add_argument("--query")
    scout.add_argument("--days", type=int, default=30)
    scout.add_argument("--max-results", type=int, default=50)
    scout.add_argument("--no-blogs", action="store_true")
    scout.add_argument("--from-existing", action="store_true", help="Build the review package from existing data/papers and data/blogs files")

    # --- collect-blogs ---
    blogs = sub.add_parser("collect-blogs", help="Collect recent blog posts from RSS/Atom feeds")
    blogs.add_argument("--topic", default="agents")
    blogs.add_argument("--query")
    blogs.add_argument("--days", type=int, default=30)
    blogs.add_argument("--max-results", type=int, default=30)
    blogs.add_argument("--feed", action="append", help="Custom RSS/Atom feed URL. Can be repeated.")

    # --- collect-github (was: github-stars) ---
    github = sub.add_parser("collect-github", help="Collect trending GitHub repos and calculate star growth")
    github.add_argument("--language", help="Filter by programming language (e.g. python, rust)")
    github.add_argument("--since", default="weekly", choices=["daily", "weekly", "monthly"])
    github.add_argument("--max-results", type=int, default=20)

    github_alias = _add_alias(sub, "github-stars", "collect-github", "Collect trending GitHub repos")
    github_alias.add_argument("--language", help="Filter by programming language (e.g. python, rust)")
    github_alias.add_argument("--since", default="weekly", choices=["daily", "weekly", "monthly"])
    github_alias.add_argument("--max-results", type=int, default=20)

    # --- rank-papers (was: rank) ---
    rank = sub.add_parser("rank-papers", help="Rank collected papers")
    rank.add_argument("--input", default=str(DATA_DIR / "papers" / "latest.json"))
    rank.add_argument("--top-k", type=int, default=5)

    rank_alias = _add_alias(sub, "rank", "rank-papers", "Rank collected papers")
    rank_alias.add_argument("--input", default=str(DATA_DIR / "papers" / "latest.json"))
    rank_alias.add_argument("--top-k", type=int, default=5)

    # --- ingest-paper (was: read) ---
    ingest = sub.add_parser("ingest-paper", help="Download and parse one paper PDF")
    ingest.add_argument("--paper-id", required=True)

    ingest_alias = _add_alias(sub, "read", "ingest-paper", "Download and parse one paper PDF")
    ingest_alias.add_argument("--paper-id", required=True)

    # --- generate-article (was: write) ---
    gen = sub.add_parser("generate-article", help="Generate a WeChat Markdown and HTML article")
    gen.add_argument("--paper-id", required=True)

    gen_alias = _add_alias(sub, "write", "generate-article", "Generate a WeChat Markdown and HTML article")
    gen_alias.add_argument("--paper-id", required=True)

    # --- writing-agent ---
    writing_agent = sub.add_parser("writing-agent", help="Run the evidence-first Paper Writing Agent")
    writing_source = writing_agent.add_mutually_exclusive_group(required=True)
    writing_source.add_argument("--paper-id")
    writing_source.add_argument("--paper-url")
    writing_agent.add_argument("--paper-title", help="Optional display title override")
    writing_agent.add_argument("--mode", choices=["paper-writing"], default="paper-writing")
    writing_agent.add_argument("--notes", "--input-note", dest="notes", help="Optional Markdown/text notes file")
    writing_agent.add_argument("--input-article", help="Optional existing article to diagnose and rewrite")
    writing_agent.add_argument("--target-audience", default="AI方向研究生和算法岗候选人")
    writing_agent.add_argument("--style-mode", choices=["rigorous", "popular", "interview", "balanced"], default="balanced")
    writing_agent.add_argument("--target-score", type=int, default=85)
    writing_agent.add_argument("--max-revisions", type=int, default=3)
    writing_agent.add_argument("--upload-images", action="store_true", help="Upload local article images to WeChat after content review passes")

    prepare_assets = sub.add_parser("prepare-agent-assets", help="Upload images for an existing content-ready Agent article")
    prepare_assets.add_argument("--article-json", required=True)
    prepare_assets.add_argument("--target-score", type=int, default=85)

    # --- review-article ---
    review = sub.add_parser("review-article", help="Review a generated article Markdown or JSON file")
    review.add_argument("path")

    # --- improve-article ---
    improve = sub.add_parser("improve-article", help="Use the configured LLM to improve an article draft")
    improve.add_argument("path")
    improve.add_argument("--output-prefix", help="Optional output prefix without extension")

    # --- draft (alias of agent-run with different defaults) ---
    draft = sub.add_parser("draft", help="Generate an article and create a local or real WeChat draft")
    draft.add_argument("--topic", default="latest_ai", help=f"Preset topic. Available: {', '.join(topic_names())}")
    draft.add_argument("--query")
    draft.add_argument("--paper-url")
    draft.add_argument("--days", type=int, default=7)
    draft.add_argument("--top-k", type=int, default=5)
    draft.add_argument("--dry-run", action="store_true")

    # --- auto-publish ---
    auto = sub.add_parser("auto-publish", help="Run the full workflow")
    auto.add_argument("--topic", help=f"Preset topic. Available: {', '.join(topic_names())}")
    auto.add_argument("--query")
    auto.add_argument("--paper-url")
    auto.add_argument("--days", type=int, default=7)
    auto.add_argument("--top-k", type=int, default=5)
    auto.add_argument("--create-draft", action="store_true")
    auto.add_argument("--auto-publish", action="store_true")
    auto.add_argument("--no-publish", action="store_true")

    # --- agent-run ---
    agent = sub.add_parser("agent-run", help="Run the one-command agentic paper-to-draft workflow")
    agent.add_argument("--topic", default="agents", help=f"Preset topic. Available: {', '.join(topic_names())}")
    agent.add_argument("--query")
    agent.add_argument("--paper-url")
    agent.add_argument("--days", type=int, default=30)
    agent.add_argument("--top-k", type=int, default=5)
    agent.add_argument("--max-results", type=int, default=50)
    agent.add_argument("--skip-blogs", action="store_true")
    agent.add_argument("--skip-improve", action="store_true")
    agent.add_argument("--skip-draft", action="store_true")
    publish_mode = agent.add_mutually_exclusive_group()
    publish_mode.add_argument("--dry-run", dest="real_wechat", action="store_false", help="Create local dry-run output only (default)")
    publish_mode.add_argument("--real-wechat", action="store_true", help="Create a real WeChat draft instead of dry-run output")
    agent.set_defaults(real_wechat=False)
    agent.add_argument("--resume", action="store_true", help="Resume from existing artifacts, skip completed steps")

    # --- check-status (was: check-agents, agent-check) ---
    check = sub.add_parser("check-status", help="Check system status, dependencies, and agent readiness")
    check.add_argument("--agent", choices=["all", *AGENTS], default="all")

    check_alias1 = _add_alias(sub, "check-agents", "check-status", "Check system status and agent readiness")
    check_alias1.add_argument("--agent", choices=["all", *AGENTS], default="all")

    check_alias2 = _add_alias(sub, "agent-check", "check-status", "Check system status and agent readiness")
    check_alias2.add_argument("--agent", choices=["all", *AGENTS], default="all")

    # --- check-config / preflight / check-llm ---
    sub.add_parser("check-config", help="Show runtime configuration")
    sub.add_parser("preflight", help="Check optional dependencies and credentials")
    sub.add_parser("check-llm", help="Check OpenAI-compatible model connectivity")

    # --- create-wechat-draft (was: publish-wechat, wechat-publish) ---
    draft_cmd = sub.add_parser("create-wechat-draft", help="Create a WeChat draft from an article JSON")
    draft_cmd.add_argument("--article-json", required=True)
    draft_cmd.add_argument("--draft-only", action="store_true")
    draft_cmd.add_argument("--publish", action="store_true")
    draft_cmd.add_argument("--dry-run", action="store_true")

    pub_alias1 = _add_alias(sub, "publish-wechat", "create-wechat-draft", "Create a WeChat draft (deprecated name)")
    pub_alias1.add_argument("--article-json", required=True)
    pub_alias1.add_argument("--draft-only", action="store_true")
    pub_alias1.add_argument("--publish", action="store_true")
    pub_alias1.add_argument("--dry-run", action="store_true")

    pub_alias2 = _add_alias(sub, "wechat-publish", "create-wechat-draft", "Create a WeChat draft (deprecated name)")
    pub_alias2.add_argument("--article-json", required=True)
    pub_alias2.add_argument("--draft-only", action="store_true")
    pub_alias2.add_argument("--publish", action="store_true")
    pub_alias2.add_argument("--dry-run", action="store_true")

    # --- update-wechat-draft (was: wechat-update-draft) ---
    update = sub.add_parser("update-wechat-draft", help="Update an existing WeChat draft")
    update.add_argument("--article-json", required=True)
    update.add_argument("--media-id", required=True)
    update.add_argument("--index", type=int, default=0)
    update.add_argument("--dry-run", action="store_true")

    upd_alias = _add_alias(sub, "wechat-update-draft", "update-wechat-draft", "Update an existing WeChat draft")
    upd_alias.add_argument("--article-json", required=True)
    upd_alias.add_argument("--media-id", required=True)
    upd_alias.add_argument("--index", type=int, default=0)
    upd_alias.add_argument("--dry-run", action="store_true")

    # --- publish-status ---
    status = sub.add_parser("publish-status", help="Query WeChat publish status")
    status.add_argument("--publish-id", required=True)

    # --- publish-draft ---
    pub_draft = sub.add_parser("publish-draft", help="Submit an existing WeChat draft media_id")
    pub_draft.add_argument("--media-id", required=True)

    # --- published-index ---
    sub.add_parser("published-index", help="Show local published index")

    # --- notify-feishu (was: notify) ---
    notify = sub.add_parser("notify-feishu", help="Send a notification to Feishu webhook")
    notify.add_argument("--title", required=True)
    notify.add_argument("--content", required=True)
    notify.add_argument("--msg-type", default="interactive", choices=["interactive", "text"])

    notify_alias = _add_alias(sub, "notify", "notify-feishu", "Send a notification to Feishu webhook")
    notify_alias.add_argument("--title", required=True)
    notify_alias.add_argument("--content", required=True)
    notify_alias.add_argument("--msg-type", default="interactive", choices=["interactive", "text"])

    # --- daily-digest ---
    daily_digest = sub.add_parser("daily-digest", help="Collect papers, blogs, and GitHub stars, optionally notify")
    daily_digest.add_argument("--topic", default="agents")
    daily_digest.add_argument("--query")
    daily_digest.add_argument("--days", type=int, default=7)
    daily_digest.add_argument("--top-k", type=int, default=5)
    daily_digest.add_argument("--language", help="GitHub language filter")
    daily_digest.add_argument("--notify", action="store_true", help="Send results to Feishu")
    daily_digest.add_argument("--resume", action="store_true", help="Resume from existing artifacts")

    # --- trend-analysis ---
    trend = sub.add_parser("trend-analysis", help="Analyze trends from papers, blogs, and GitHub stars")
    trend.add_argument("--topic", default="agents")
    trend.add_argument("--days", type=int, default=14)
    trend.add_argument("--top-k", type=int, default=10)
    trend.add_argument("--language", help="GitHub language filter")

    # --- check-artifacts (new) ---
    artifacts = sub.add_parser("check-artifacts", help="Check artifact completion status for a paper")
    artifacts.add_argument("--paper-id", required=True)

    # --- tui ---
    sub.add_parser("tui", help="Launch the interactive TUI agent")

    # --- topics ---
    topics = sub.add_parser("topics", help="List built-in topic presets")
    topics.set_defaults(_topics=True)

    return parser


def _resolve_command(args: argparse.Namespace) -> str:
    """Resolve the canonical command name, warning on deprecated aliases."""
    if hasattr(args, "_alias_target"):
        old = args.command
        new = args._alias_target
        _warn_deprecated(old, new)
        return new
    return args.command


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    command = _resolve_command(args)
    workflow = SmearglePaperWorkflow()

    try:
        if command == "agent":
            from .product_agent import ProductAgent

            _print(
                ProductAgent().create(
                    args.request,
                    paper_id=args.paper_id,
                    paper_url=args.paper_url,
                    article_json=Path(args.article_json) if args.article_json else None,
                )
            )
        elif command == "tasks":
            from .product_agent import ProductAgent

            _print(ProductAgent().list())
        elif command == "task-show":
            from .product_agent import ProductAgent

            _print(ProductAgent().show(args.task_id))
        elif command == "task-approve":
            from .product_agent import ProductAgent

            _print(ProductAgent().approve(args.task_id, args.gate))
        elif command == "task-resume":
            from .product_agent import ProductAgent

            _print(ProductAgent().resume(args.task_id, real_wechat=args.real_wechat))
        elif command == "collect-arxiv":
            # Handle alias with --sources flag
            sources = getattr(args, "sources", ["arxiv"])
            papers = workflow.collect(args.topic, args.query, args.days, sources, args.max_results)
            _print({"count": len(papers), "output": str(DATA_DIR / "papers" / "latest.json")})
        elif command == "scout-run":
            _print(run_scout_review(args.topic, args.query, args.days, args.max_results, include_blogs=not args.no_blogs, from_existing=args.from_existing))
        elif command == "collect-blogs":
            result = workflow.collect_blogs(args.topic, args.query, args.days, args.max_results, args.feed)
            result["output"] = str(DATA_DIR / "blogs" / "latest.json")
            _print(result)
        elif command == "collect-github":
            _print(workflow.collect_github_stars(args.language, args.since, args.max_results))
        elif command == "rank-papers":
            papers = workflow.load_papers_file(Path(args.input))
            _print(workflow.rank(papers, top_k=args.top_k))
        elif command == "ingest-paper":
            _print(workflow.read(_find_paper(args.paper_id)))
        elif command == "generate-article":
            paper = _find_paper(args.paper_id)
            parsed_path = DATA_DIR / "parsed" / f"{paper.paper_id.replace('/', '_')}.json"
            _print(workflow.write_article(paper, parsed=read_json(parsed_path, {})))
        elif command == "writing-agent":
            _print(
                workflow.run_writing_agent(
                    args.paper_id,
                    paper_url=args.paper_url,
                    paper_title=args.paper_title,
                    notes_path=Path(args.notes) if args.notes else None,
                    article_path=Path(args.input_article) if args.input_article else None,
                    target_audience=args.target_audience,
                    style_mode=args.style_mode,
                    target_score=args.target_score,
                    max_revisions=args.max_revisions,
                    upload_images=args.upload_images,
                )
            )
        elif command == "prepare-agent-assets":
            _print(workflow.prepare_agent_assets(Path(args.article_json), target_score=args.target_score))
        elif command == "review-article":
            _print(workflow.review_article(Path(args.path)))
        elif command == "improve-article":
            output_prefix = Path(args.output_prefix) if args.output_prefix else None
            _print(improve_article_file(Path(args.path), output_prefix))
        elif command == "draft":
            _print(workflow.create_topic_draft(args.topic, args.query, args.paper_url, args.days, args.top_k, args.dry_run))
        elif command == "auto-publish":
            _print(workflow.auto_publish(args.topic, args.query, args.paper_url, args.days, args.top_k, args.create_draft, args.auto_publish, args.no_publish))
        elif command == "agent-run":
            _print(
                workflow.agent_run(
                    topic=args.topic,
                    query=args.query,
                    paper_url=args.paper_url,
                    days=args.days,
                    top_k=args.top_k,
                    max_results=args.max_results,
                    collect_blogs=not args.skip_blogs,
                    improve=not args.skip_improve,
                    create_draft=not args.skip_draft,
                    dry_run=not args.real_wechat,
                    resume=args.resume,
                )
            )
        elif command == "check-status":
            _print(check_agents(args.agent))
        elif command == "check-config":
            _print(runtime_settings())
        elif command == "preflight":
            _print(workflow.preflight())
        elif command == "check-llm":
            _print(check_llm_connection())
        elif command == "create-wechat-draft":
            if not args.draft_only and not args.publish:
                raise SystemExit("Use --draft-only or --publish.")
            _print(workflow.publish_existing_article(Path(args.article_json), real_wechat=not args.dry_run, publish=args.publish))
        elif command == "update-wechat-draft":
            _print(workflow.update_existing_draft(Path(args.article_json), args.media_id, real_wechat=not args.dry_run, index=args.index))
        elif command == "publish-status":
            _print(WechatClient(dry_run=False).get_publish_status(args.publish_id))
        elif command == "publish-draft":
            _print({"publish_id": WechatClient(dry_run=False).publish_draft(args.media_id)})
        elif command == "published-index":
            _print(read_json(DATA_DIR / "published_index.json", {}))
        elif command == "topics":
            _print({"topics": topic_names()})
        elif command == "notify-feishu":
            _print(workflow.send_notification(args.title, args.content, args.msg_type))
        elif command == "daily-digest":
            _print(workflow.daily_digest(args.topic, args.days, args.top_k, args.language, args.notify, resume=args.resume))
        elif command == "trend-analysis":
            _print(workflow.trend_analysis(args.topic, args.days, args.top_k, args.language))
        elif command == "check-artifacts":
            _print(workflow.check_artifacts(args.paper_id))
        elif command == "tui":
            from .tui import run_tui
            run_tui()
        else:
            raise SystemExit(f"Unknown command: {command}")
    except ArxivRateLimitError as exc:
        raise SystemExit(str(exc)) from exc
    except ArxivTemporaryError as exc:
        raise SystemExit(str(exc)) from exc
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc


def _find_paper(paper_id: str) -> PaperMeta:
    for item in read_json(DATA_DIR / "papers" / "latest.json", []):
        if item.get("paper_id") == paper_id:
            return PaperMeta.from_dict(item)
    for path in [DATA_DIR / "ranked" / "latest.json", DATA_DIR / "papers" / "ranked_latest.json"]:
        for row in read_json(path, []):
            paper = row.get("paper", {})
            if paper.get("paper_id") == paper_id:
                return PaperMeta.from_dict(paper)
    raise SystemExit(f"Paper not found in latest data: {paper_id}")


def _print(payload: object) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
