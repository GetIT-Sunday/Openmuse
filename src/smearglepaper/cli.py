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

PROJECT_ROOT = Path(__file__).resolve().parents[2]

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
    sub = parser.add_subparsers(dest="command", required=False)

    # --- durable runtime ---
    run = sub.add_parser("run", help="Run a durable workflow")
    run.add_argument("workflow", choices=["paper-research", "paper-to-article", "paper-to-wechat", "daily-digest"])
    _add_runtime_inputs(run)

    runs = sub.add_parser("runs", help="Inspect and control durable runs")
    runs_sub = runs.add_subparsers(dest="runs_command", required=True)
    runs_list = runs_sub.add_parser("list")
    runs_list.add_argument("--workspace")
    runs_list.add_argument("--json", action="store_true")
    runs_show = runs_sub.add_parser("show")
    runs_show.add_argument("run_id")
    runs_show.add_argument("--workspace")
    runs_show.add_argument("--events", action="store_true")
    runs_show.add_argument("--json", action="store_true")
    for runtime_action in ("resume", "cancel"):
        action = runs_sub.add_parser(runtime_action)
        action.add_argument("run_id")
        action.add_argument("--workspace")
        action.add_argument("--json", action="store_true")
    runs_retry = runs_sub.add_parser("retry")
    runs_retry.add_argument("run_id")
    runs_retry.add_argument("--step")
    runs_retry.add_argument("--workspace")
    runs_retry.add_argument("--json", action="store_true")
    runs_select = runs_sub.add_parser("select")
    runs_select.add_argument("run_id")
    runs_select.add_argument("paper_id")
    runs_select.add_argument("--workspace")
    runs_select.add_argument("--json", action="store_true")

    approve = sub.add_parser("approve", help="Resolve a durable run approval")
    approve.add_argument("run_id")
    approve.add_argument("approval_id")
    approve.add_argument("--reject", action="store_true")
    approve.add_argument("--workspace")
    approve.add_argument("--json", action="store_true")

    artifacts = sub.add_parser("artifacts", help="Inspect run artifacts")
    artifacts_sub = artifacts.add_subparsers(dest="artifacts_command", required=True)
    artifacts_list = artifacts_sub.add_parser("list")
    artifacts_list.add_argument("run_id")
    artifacts_list.add_argument("--workspace")
    artifacts_list.add_argument("--json", action="store_true")

    runtime_config = sub.add_parser("config", help="Inspect runtime configuration")
    config_sub = runtime_config.add_subparsers(dest="config_command", required=True)
    config_check = config_sub.add_parser("check")
    config_check.add_argument("--workspace")
    config_check.add_argument("--json", action="store_true")

    # --- product-level task agent ---
    product_agent = sub.add_parser("agent", help="Run a durable workflow from a natural-language request")
    product_agent.add_argument("request")
    product_source = product_agent.add_mutually_exclusive_group()
    product_source.add_argument("--paper-id")
    product_source.add_argument("--paper-url")
    product_source.add_argument("--article-json", help="Use an existing publish-ready article")
    product_agent.add_argument("--ranking-profile", choices=["frontier", "classic", "engineering", "balanced"], default="balanced")
    product_agent.add_argument("--days", type=int, default=30)
    product_agent.add_argument("--candidate-count", type=int, default=3)
    product_agent.add_argument("--no-auto-prepare-assets", action="store_true", help="Do not automatically upload local images after content review passes")
    product_agent.add_argument("--workspace")
    product_agent.add_argument("--workflow", choices=["paper-research", "paper-to-article", "paper-to-wechat", "daily-digest"])
    product_agent.add_argument("--real", action="store_true", help="Request a real publish workflow; approval is still required")
    product_agent.add_argument("--json", action="store_true")

    legacy_product_agent = sub.add_parser("product-agent", help="Use the legacy durable product task orchestrator")
    legacy_product_agent.add_argument("request")
    legacy_product_source = legacy_product_agent.add_mutually_exclusive_group()
    legacy_product_source.add_argument("--paper-id")
    legacy_product_source.add_argument("--paper-url")
    legacy_product_source.add_argument("--article-json")
    legacy_product_agent.add_argument("--ranking-profile", choices=["frontier", "classic", "engineering", "balanced"], default="balanced")
    legacy_product_agent.add_argument("--days", type=int, default=30)
    legacy_product_agent.add_argument("--candidate-count", type=int, default=3)
    legacy_product_agent.add_argument("--no-auto-prepare-assets", action="store_true")

    sub.add_parser("tasks", help="List durable product tasks")

    task_show = sub.add_parser("task-show", help="Show one durable product task")
    task_show.add_argument("--task-id", required=True)

    task_approve = sub.add_parser("task-approve", help="Approve a waiting product task gate")
    task_approve.add_argument("--task-id", required=True)
    task_approve.add_argument("--gate", required=True, choices=["topic", "publish"])
    task_approve.add_argument("--paper-id", help="Candidate paper to approve; defaults to the top-ranked candidate")

    task_resume = sub.add_parser("task-resume", help="Resume a durable product task")
    task_resume.add_argument("--task-id", required=True)
    task_resume.add_argument("--real-wechat", action="store_true", help="Allow a previously approved task to create or update a real WeChat draft")

    task_retry = sub.add_parser("task-retry", help="Move a failed or needs-attention task back to its recorded recovery stage")
    task_retry.add_argument("--task-id", required=True)

    task_preview = sub.add_parser("task-preview", help="Serve the mobile preview and publish-readiness checklist for a task")
    task_preview.add_argument("--task-id", required=True)
    task_preview.add_argument("--host", default="0.0.0.0")
    task_preview.add_argument("--port", type=int, default=8765)

    agent_preview = sub.add_parser("agent-preview", help="Serve the mobile task preview")
    agent_preview.add_argument("--task-id", required=True)
    agent_preview.add_argument("--host", default="0.0.0.0")
    agent_preview.add_argument("--port", type=int, default=8765)

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
    writing_agent.add_argument("--workspace")
    writing_agent.add_argument("--json", action="store_true")

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
    agent.add_argument("--workspace")
    agent.add_argument("--json", action="store_true")

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

    # --- portable Skills and Packs ---
    skill = sub.add_parser("skill", help="Discover, inspect, and export portable Skills")
    skill_sub = skill.add_subparsers(dest="skill_command", required=True)
    skill_sub.add_parser("list", help="List installed local Skills")
    skill_show = skill_sub.add_parser("show", help="Show one Skill manifest")
    skill_show.add_argument("skill_id")
    skill_export = skill_sub.add_parser("export", help="Export one Skill for another Harness")
    skill_export.add_argument("skill_id")
    skill_export.add_argument("--to", dest="destination", required=True, help="Destination skills directory")
    skill_sub.add_parser("validate", help="Validate local Skills")

    pack = sub.add_parser("pack", help="Install, resolve, and manage Capability Packs")
    pack_sub = pack.add_subparsers(dest="pack_command", required=True)
    pack_sub.add_parser("list", help="List local Packs")
    pack_show = pack_sub.add_parser("show", help="Show one Pack manifest")
    pack_show.add_argument("pack_id")
    pack_export = pack_sub.add_parser("export", help="Export a self-contained Pack")
    pack_export.add_argument("pack_id")
    pack_export.add_argument("--to", dest="destination", required=True, help="Destination Pack directory")
    pack_resolve = pack_sub.add_parser("resolve", help="Resolve a local or remote Pack and its dependencies")
    pack_resolve.add_argument("pack_id")
    pack_resolve.add_argument("--source", action="append", default=[], help="Additional local Pack catalog directory")
    pack_resolve.add_argument("--registry", help="Configured remote Registry name")
    pack_resolve.add_argument("--version", default="*", help="Pack version constraint")
    pack_install = pack_sub.add_parser("install", help="Install a Pack from a local directory or signed Registry")
    pack_install.add_argument("source", help="Directory containing pack.yaml, or publisher/name")
    pack_install.add_argument("--registry", help="Configured remote Registry name")
    pack_install.add_argument("--version", default="*", help="Pack version constraint")
    pack_search = pack_sub.add_parser("search", help="Search a configured remote Registry")
    pack_search.add_argument("query", nargs="?", default="")
    pack_search.add_argument("--registry", help="Configured remote Registry name")
    pack_build = pack_sub.add_parser("build", help="Build a reproducible self-contained Pack ZIP")
    pack_build.add_argument("source", help="Local Pack directory or Pack id")
    pack_build.add_argument("--to", dest="destination", required=True, help="Build output directory")
    pack_sign = pack_sub.add_parser("sign", help="Sign a built Pack ZIP with Ed25519")
    pack_sign.add_argument("archive")
    pack_sign.add_argument("--private-key", required=True)
    pack_sign.add_argument("--key-id", required=True)
    pack_sign.add_argument("--to", dest="descriptor")
    pack_publish = pack_sub.add_parser("publish", help="Add a signed release to a static Registry directory")
    pack_publish.add_argument("descriptor")
    pack_publish.add_argument("--registry-dir", required=True)
    pack_publish.add_argument("--name", default="AIGC Pack Registry")
    pack_deploy = pack_sub.add_parser("deploy", help="Prepare a static Registry for a hosting provider")
    deploy_sub = pack_deploy.add_subparsers(dest="deploy_provider", required=True)
    deploy_pages = deploy_sub.add_parser("github-pages", help="Prepare a Registry for GitHub Pages")
    deploy_pages.add_argument("--registry-dir", required=True)
    deploy_pages.add_argument("--output-dir", required=True)
    deploy_pages.add_argument("--workflow-out", help="Optional GitHub Actions workflow output path")
    deploy_pages.add_argument("--site-path", help="Repository-relative Pages directory used by the workflow")
    pack_keygen = pack_sub.add_parser("keygen", help="Generate an Ed25519 Pack signing key")
    pack_keygen.add_argument("--to", dest="private_key", required=True)
    pack_keygen.add_argument("--key-id", required=True)
    for action_name in ("uninstall", "enable", "disable"):
        action = pack_sub.add_parser(action_name, help=f"{action_name.title()} a project Pack")
        action.add_argument("pack_id")
    pack_sub.add_parser("status", help="Show installed and enabled Packs")
    pack_registry = pack_sub.add_parser("registry", help="Manage trusted Pack Registries")
    registry_sub = pack_registry.add_subparsers(dest="registry_command", required=True)
    registry_sub.add_parser("list", help="List trusted Registries")
    registry_add = registry_sub.add_parser("add", help="Trust a signed Pack Registry")
    registry_add.add_argument("name")
    registry_add.add_argument("index_url")
    registry_add.add_argument("--key-id", required=True)
    registry_add.add_argument("--public-key", required=True, help="Base64 Ed25519 public key")
    registry_remove = registry_sub.add_parser("remove", help="Remove a trusted Pack Registry")
    registry_remove.add_argument("name")
    registry_trust = registry_sub.add_parser("trust-key", help="Add a trusted signing key for rotation")
    registry_trust.add_argument("name")
    registry_trust.add_argument("--key-id", required=True)
    registry_trust.add_argument("--public-key", required=True, help="Base64 Ed25519 public key")
    for name, action in pack_sub.choices.items():
        if name == "registry":
            continue
        action.add_argument("--root", default=str(PROJECT_ROOT / ".aigc"), help=argparse.SUPPRESS)
    for action in registry_sub.choices.values():
        action.add_argument("--root", default=str(PROJECT_ROOT / ".aigc"), help=argparse.SUPPRESS)

    # --- topics ---
    topics = sub.add_parser("topics", help="List built-in topic presets")
    topics.set_defaults(_topics=True)

    for command_parser in sub.choices.values():
        if not any("--json" in action.option_strings for action in command_parser._actions):
            command_parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    return parser


def _add_runtime_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--paper-id")
    parser.add_argument("--paper-url")
    parser.add_argument("--paper-title")
    parser.add_argument("--topic", default="agents")
    parser.add_argument("--query")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-count", type=int, default=3)
    parser.add_argument("--ranking-profile", choices=["frontier", "classic", "engineering", "balanced"], default="balanced")
    parser.add_argument("--max-results", type=int, default=50)
    parser.add_argument("--target-audience", default="AI方向研究生和算法岗候选人")
    parser.add_argument("--style-mode", choices=["rigorous", "popular", "interview", "balanced"], default="balanced")
    parser.add_argument("--target-score", type=int, default=85)
    parser.add_argument("--max-revisions", type=int, default=3)
    parser.add_argument("--notes")
    parser.add_argument("--input-article")
    parser.add_argument("--language")
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--offline-example", action="store_true", help="Run with bundled metadata and evidence; no network or API key required")
    parser.add_argument("--workspace")
    parser.add_argument("--model")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="real", action="store_false", help="Do not call external write APIs (default)")
    mode.add_argument("--real", action="store_true", help="Allow external writes after explicit approval")
    parser.set_defaults(real=False)
    parser.add_argument("--json", action="store_true")


def _resolve_command(args: argparse.Namespace) -> str:
    """Resolve the canonical command name, warning on deprecated aliases."""
    if hasattr(args, "_alias_target"):
        old = args.command
        new = args._alias_target
        _warn_deprecated(old, new)
        return new
    return args.command


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command is None:
        from .tui import run_tui

        run_tui()
        return 0
    command = _resolve_command(args)
    workflow = SmearglePaperWorkflow()

    try:
        if command == "agent":
            return _run_natural_language_agent(args)
        elif command == "product-agent":
            from .product_agent import ProductAgent

            _print(
                ProductAgent().create(
                    args.request,
                    paper_id=args.paper_id,
                    paper_url=args.paper_url,
                    article_json=Path(args.article_json) if args.article_json else None,
                    ranking_profile=args.ranking_profile,
                    days=args.days,
                    candidate_count=args.candidate_count,
                    auto_prepare_assets=not args.no_auto_prepare_assets,
                )
            )
        elif command == "run":
            return _run_runtime_workflow(args)
        elif command == "runs":
            return _run_runtime_control(args)
        elif command == "approve":
            return _run_runtime_approval(args)
        elif command == "artifacts":
            from .runtime import AgentRuntime

            _print(AgentRuntime(args.workspace).list_artifacts(args.run_id))
        elif command == "config":
            from .runtime.config import load_runtime_config, resolve_workspace

            configured = load_runtime_config()
            _print({"ok": True, "workspace": str(resolve_workspace(args.workspace, configured)), "config": configured, "legacy": runtime_settings()})
        elif command == "tasks":
            from .product_agent import ProductAgent

            _print(ProductAgent().list())
        elif command == "task-show":
            from .product_agent import ProductAgent

            _print(ProductAgent().show(args.task_id))
        elif command == "task-approve":
            from .product_agent import ProductAgent

            _print(ProductAgent().approve(args.task_id, args.gate, paper_id=args.paper_id))
        elif command == "task-resume":
            from .product_agent import ProductAgent

            _print(ProductAgent().resume(args.task_id, real_wechat=args.real_wechat))
        elif command == "task-retry":
            from .product_agent import ProductAgent

            _print(ProductAgent().retry(args.task_id))
        elif command in {"task-preview", "agent-preview"}:
            from .preview_server import preview_urls, serve_task_preview

            def _ready(payload: dict[str, object]) -> None:
                _print(
                    {
                        "task_id": args.task_id,
                        "local": payload.get("local") or preview_urls(args.task_id, host=args.host, port=args.port),
                        "lan": payload.get("lan"),
                        "message": "Preview server is running. Press Ctrl+C to stop.",
                    }
                )

            serve_task_preview(args.task_id, host=args.host, port=args.port, ready_callback=_ready)
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
            _warn_deprecated("writing-agent", "run paper-to-article")
            return _run_legacy_runtime_command(args, "paper-to-article")
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
            _warn_deprecated("agent-run", "run paper-to-wechat")
            return _run_legacy_runtime_command(
                args,
                "paper-to-article" if args.skip_draft else "paper-to-wechat",
                dry_run=not args.real_wechat,
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
        elif command == "skill":
            from .skill_registry import SkillRegistry

            registry = SkillRegistry(PROJECT_ROOT / "skills")
            if args.skill_command == "list":
                _print([package.manifest.to_dict() | {"path": str(package.path)} for package in registry.list()])
            elif args.skill_command == "show":
                _print(registry.get(args.skill_id).manifest.to_dict())
            elif args.skill_command == "validate":
                errors = registry.validate()
                _print({"ok": not errors, "errors": errors})
                return 0 if not errors else 5
            elif args.skill_command == "export":
                target = registry.export(args.skill_id, Path(args.destination))
                _print({"ok": True, "skill": args.skill_id, "destination": str(target)})
        elif command == "pack":
            from .pack_deploy import GitHubPagesAdapter
            from .pack_manager import InstalledPackStore, PackCatalog, PackPackage, PackResolver, harness_version
            from .pack_publisher import (
                build_pack_archive,
                generate_signing_key,
                publish_signed_release,
                sign_pack_archive,
            )
            from .pack_registry import (
                PackRegistryClient,
                RegistryConfig,
                RegistryConfigStore,
                RegistryResolver,
            )
            from .skill_registry import PackManifest, SkillRegistry, canonical_pack_id, discover_packs, export_pack

            packs = discover_packs(PROJECT_ROOT / "packs")
            if args.pack_command == "list":
                _print([manifest.to_dict() | {"path": str(path)} for manifest, path in packs])
            elif args.pack_command == "show":
                requested_id = canonical_pack_id(args.pack_id)
                match = next((manifest for manifest, path in packs if requested_id in {manifest.id, manifest.name, path.name}), None)
                if match is None:
                    raise SystemExit(f"Pack not found: {args.pack_id}")
                _print(match.to_dict())
            elif args.pack_command == "export":
                exported = export_pack(PROJECT_ROOT / "packs", args.pack_id, SkillRegistry(PROJECT_ROOT / "skills"), Path(args.destination))
                _print({"ok": True, "pack": args.pack_id, "skills": [str(path) for path in exported]})
            elif args.pack_command == "resolve":
                if args.registry:
                    config = RegistryConfigStore(Path(args.root) / "registries.json").get(args.registry)
                    client = PackRegistryClient(config, Path(args.root) / "cache")
                    resolution = RegistryResolver(client.fetch_index(), harness_version()).resolve(args.pack_id, args.version)
                    _print(
                        {
                            "root": resolution.root,
                            "install_order": [
                                {
                                    "id": release.pack_id,
                                    "version": release.version,
                                    "dependencies": release.requirement_dicts(),
                                }
                                for release in resolution.releases
                            ],
                        }
                    )
                else:
                    roots = (PROJECT_ROOT / "packs", *(Path(item) for item in args.source))
                    resolution = PackResolver(PackCatalog.from_roots(roots)).resolve(args.pack_id, args.version)
                    _print(resolution.to_dict())
            elif args.pack_command == "install":
                source = Path(args.source).expanduser().resolve()
                manifest_path = source / "pack.yaml"
                store = InstalledPackStore(Path(args.root))
                if manifest_path.is_file():
                    package = PackPackage(PackManifest.from_file(manifest_path), source)
                    catalog = PackCatalog.from_roots((PROJECT_ROOT / "packs", source.parent, source, store.packs_dir))
                    catalog.add(package)
                    resolution = store.install(package, PackResolver(catalog), SkillRegistry(PROJECT_ROOT / "skills"))
                else:
                    config_store = RegistryConfigStore(store.root / "registries.json")
                    config = config_store.get(args.registry)
                    client = PackRegistryClient(config, store.root / "cache")
                    remote = client.download(args.source, args.version, harness_version())
                    catalog = PackCatalog(remote.packages)
                    package = catalog.get(remote.root, args.version)
                    resolution = store.install(
                        package,
                        PackResolver(catalog),
                        SkillRegistry(PROJECT_ROOT / "skills"),
                        remote.provenance(config),
                    )
                _print({"ok": True, "installed": resolution.to_dict(), "root": str(store.root)})
            elif args.pack_command == "search":
                config = RegistryConfigStore(Path(args.root) / "registries.json").get(args.registry)
                _print(PackRegistryClient(config, Path(args.root) / "cache").search(args.query))
            elif args.pack_command == "build":
                source = Path(args.source).expanduser().resolve()
                manifest_path = source / "pack.yaml"
                if manifest_path.is_file():
                    package = PackPackage(PackManifest.from_file(manifest_path), source)
                else:
                    requested_id = canonical_pack_id(args.source)
                    match = next(
                        (
                            (manifest, path)
                            for manifest, path in packs
                            if requested_id in {manifest.id, manifest.name, path.name}
                        ),
                        None,
                    )
                    if match is None:
                        raise SystemExit(f"Pack not found: {args.source}")
                    package = PackPackage(*match)
                build = build_pack_archive(package, SkillRegistry(PROJECT_ROOT / "skills"), Path(args.destination))
                _print({"ok": True, "build": build.to_dict()})
            elif args.pack_command == "keygen":
                _print({"ok": True, "signing_key": generate_signing_key(Path(args.private_key), args.key_id)})
            elif args.pack_command == "sign":
                signed = sign_pack_archive(
                    Path(args.archive),
                    Path(args.private_key),
                    args.key_id,
                    Path(args.descriptor) if args.descriptor else None,
                )
                _print({"ok": True, "release": signed.to_dict()})
            elif args.pack_command == "publish":
                published = publish_signed_release(Path(args.descriptor), Path(args.registry_dir), args.name)
                _print({"ok": True, "published": published})
            elif args.pack_command == "deploy":
                if args.deploy_provider == "github-pages":
                    deployment = GitHubPagesAdapter().prepare(
                        Path(args.registry_dir),
                        Path(args.output_dir),
                        workflow_path=Path(args.workflow_out) if args.workflow_out else None,
                        site_path=args.site_path,
                    )
                    _print({"ok": True, "deployment": deployment.to_dict()})
            elif args.pack_command == "registry":
                config_store = RegistryConfigStore(Path(args.root) / "registries.json")
                if args.registry_command == "list":
                    _print([config.to_dict() for config in config_store.list()])
                elif args.registry_command == "add":
                    config = RegistryConfig(args.name, args.index_url, {args.key_id: args.public_key})
                    config_store.add(config)
                    _print({"ok": True, "registry": config.to_dict()})
                elif args.registry_command == "remove":
                    config_store.remove(args.name)
                    _print({"ok": True, "removed": args.name})
                elif args.registry_command == "trust-key":
                    config = config_store.trust_key(args.name, args.key_id, args.public_key)
                    _print({"ok": True, "registry": config.to_dict()})
            elif args.pack_command == "status":
                _print(InstalledPackStore(Path(args.root)).status())
            elif args.pack_command == "enable":
                enabled = InstalledPackStore(Path(args.root)).enable(canonical_pack_id(args.pack_id))
                _print({"ok": True, "enabled": sorted(enabled)})
            elif args.pack_command == "disable":
                enabled = InstalledPackStore(Path(args.root)).disable(canonical_pack_id(args.pack_id))
                _print({"ok": True, "enabled": sorted(enabled)})
            elif args.pack_command == "uninstall":
                store = InstalledPackStore(Path(args.root))
                store.uninstall(canonical_pack_id(args.pack_id))
                _print({"ok": True, "uninstalled": canonical_pack_id(args.pack_id)})
        else:
            raise SystemExit(f"Unknown command: {command}")
        return 0
    except ArxivRateLimitError as exc:
        raise SystemExit(str(exc)) from exc
    except ArxivTemporaryError as exc:
        raise SystemExit(str(exc)) from exc
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc


def _runtime_inputs(args: argparse.Namespace) -> dict[str, object]:
    names = (
        "paper_id", "paper_url", "paper_title", "topic", "query", "days", "top_k",
        "max_results", "candidate_count", "ranking_profile", "target_audience", "style_mode", "target_score", "max_revisions",
        "notes", "input_article", "language", "notify", "offline_example",
    )
    return {name: getattr(args, name) for name in names if hasattr(args, name) and getattr(args, name) is not None}


def _runtime_exit(status: str, manifest: dict[str, object] | None = None) -> int:
    if status == "completed":
        return 0
    if status == "waiting_approval":
        return 7
    if status == "waiting_input":
        return 8
    if status == "cancelled":
        return 130
    if manifest and manifest.get("failure_code") in {3, 4, 5, 6}:
        return int(manifest["failure_code"])
    return 5


def _run_legacy_runtime_command(
    args: argparse.Namespace,
    workflow_id: str,
    *,
    dry_run: bool = True,
) -> int:
    from .runtime import AgentRuntime, RunRequest

    inputs = {
        name: getattr(args, name)
        for name in (
            "paper_id",
            "paper_url",
            "paper_title",
            "topic",
            "query",
            "days",
            "top_k",
            "max_results",
            "target_audience",
            "style_mode",
            "target_score",
            "max_revisions",
            "notes",
            "input_article",
        )
        if hasattr(args, name) and getattr(args, name) is not None
    }
    runtime = AgentRuntime(getattr(args, "workspace", None))
    result = runtime.run(
        RunRequest(workflow_id, inputs, getattr(args, "workspace", None), dry_run=dry_run),
        event_handler=None if getattr(args, "json", False) else _event_printer,
    )
    manifest = runtime.get_run(result.run_id)
    _print(result.to_dict())
    return _runtime_exit(result.status, manifest)


def _event_printer(event: object) -> None:
    event_type = getattr(event, "type", "event")
    payload = getattr(event, "payload", {})
    step = payload.get("step") if isinstance(payload, dict) else None
    message = payload.get("message") if isinstance(payload, dict) else None
    detail = ": ".join(str(item) for item in (step, message) if item)
    print(f"[{event_type}] {detail}".rstrip(), file=sys.stderr)


def _run_runtime_workflow(args: argparse.Namespace) -> int:
    from .runtime import AgentRuntime, RunRequest

    runtime = AgentRuntime(args.workspace)
    request = RunRequest(
        workflow=args.workflow,
        inputs=_runtime_inputs(args),
        workspace=args.workspace,
        dry_run=not args.real,
        model=args.model,
    )
    result = runtime.run(request, event_handler=None if args.json else _event_printer)
    manifest = runtime.get_run(result.run_id)
    _print({**result.to_dict(), "manifest": str(runtime.store.run_dir(result.run_id) / "manifest.json")})
    return _runtime_exit(result.status, manifest)


def _run_natural_language_agent(args: argparse.Namespace) -> int:
    from .runtime import AgentRuntime, RunRequest

    request_text = str(args.request)
    workflow_id = args.workflow or (
        "paper-to-wechat"
        if any(word in request_text.lower() for word in ("微信", "公众号", "wechat", "发布", "草稿"))
        else "paper-to-article"
    )
    inputs = {
        "request": request_text,
        "paper_id": args.paper_id,
        "paper_url": args.paper_url,
        "topic": "agents",
        "days": args.days,
        "top_k": args.candidate_count,
    }
    runtime = AgentRuntime(args.workspace)
    result = runtime.run(
        RunRequest(workflow_id, {key: value for key, value in inputs.items() if value is not None}, args.workspace, dry_run=not args.real),
        event_handler=None if args.json else _event_printer,
    )
    manifest = runtime.get_run(result.run_id)
    _print(result.to_dict())
    return _runtime_exit(result.status, manifest)


def _run_runtime_control(args: argparse.Namespace) -> int:
    from .runtime import AgentRuntime

    runtime = AgentRuntime(args.workspace)
    if args.runs_command == "list":
        _print(runtime.list_runs())
        return 0
    if args.runs_command == "show":
        _print(runtime.get_run(args.run_id, include_events=args.events))
        return 0
    if args.runs_command == "resume":
        result = runtime.resume(args.run_id)
    elif args.runs_command == "retry":
        result = runtime.retry(args.run_id, args.step)
    elif args.runs_command == "cancel":
        result = runtime.cancel(args.run_id)
    elif args.runs_command == "select":
        runtime.resolve_interaction(args.run_id, args.paper_id)
        result = runtime.resume(args.run_id)
    else:
        raise SystemExit(f"Unknown runs command: {args.runs_command}")
    manifest = runtime.get_run(result.run_id)
    _print(result.to_dict())
    return _runtime_exit(result.status, manifest)


def _run_runtime_approval(args: argparse.Namespace) -> int:
    from .runtime import AgentRuntime

    runtime = AgentRuntime(args.workspace)
    result = runtime.resolve_approval(args.run_id, args.approval_id, approve=not args.reject)
    if not args.reject:
        result = runtime.resume(args.run_id)
    manifest = runtime.get_run(result.run_id)
    _print(result.to_dict())
    return _runtime_exit(result.status, manifest)


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
    raise SystemExit(main())
