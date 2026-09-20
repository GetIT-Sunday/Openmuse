from __future__ import annotations

import json
from typing import Any

from .workflow import SmearglePaperWorkflow


TOOL_SKILLS: dict[str, str] = {
    "collect_papers": "autowechat.paper-deep-read",
    "rank_papers": "autowechat.paper-deep-read",
    "ingest_paper": "autowechat.paper-deep-read",
    "generate_article": "autowechat.write-paper-wechat",
    "run_writing_agent": "autowechat.write-paper-wechat",
    "prepare_agent_assets": "autowechat.write-paper-wechat",
    "review_article": "autowechat.write-paper-wechat",
    "improve_article": "autowechat.write-paper-wechat",
    "create_wechat_draft": "autowechat.auto-publish",
    "daily_digest": "autowechat.daily-digest",
    "trend_analysis": "autowechat.trend-analysis",
    "agent_run": "autowechat.auto-publish",
    "check_status": "autowechat.auto-publish",
}


def installed_capabilities(project_root: "Path | None" = None) -> tuple[list[dict[str, Any]], str]:
    """Return tools backed by installed Skills and a concise model catalog."""
    from pathlib import Path
    from .pack_manager import InstalledPackStore
    from .skill_registry import SkillRegistry

    root = project_root or Path(__file__).resolve().parents[2]
    pack_store = InstalledPackStore(root / ".aigc")
    registry = SkillRegistry((root / "skills", *pack_store.skill_roots()))
    valid_ids = {package.manifest.id for package in registry.list() if not package.validate()}
    tools = [tool for tool in TOOLS if TOOL_SKILLS.get(str(tool.get("function", {}).get("name", ""))) in valid_ids]
    summaries = [package.capability_summary() for package in registry.list() if package.manifest.id in valid_ids]
    catalog = "已安装的可用 Skills：\n" + "\n".join(summaries) if summaries else "当前没有通过校验的 Skill。"
    return tools, catalog

SYSTEM_PROMPT = """你是 OpenMuse，AIGC Harness 的 AI 论文信息助手（SmearglePaper 是兼容模块名）。你可以：
- 收集 arXiv 论文、博客、GitHub 热门仓库
- 对论文排名、解读、生成中文文章
- 运行证据优先的论文写作 Agent，自动规划、审稿和修订
- 创建微信公众号草稿
- 分析研究趋势

用户用中文或英文交流。你通过调用工具完成任务，用中文回复。
回复简洁，信息密度高。结果用 Markdown 格式展示。

当用户提到研究方向时，将其转换为合适的 arXiv 搜索关键词。
例如：
- "大模型 agent" → topic 用 "agents"
- "NLP 语义" → topic 用 "nlp"
- "多模态" → topic 用 "multimodal"
- "强化学习" → topic 用 "reinforcement learning"
- 用户没指定方向时，使用 "agents" 作为默认值
"""

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "collect_papers",
            "description": "从 arXiv 收集近期论文。topic 可以是任意研究方向关键词，如 agents, nlp, multimodal, reinforcement learning, vision, reasoning 等",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "研究方向关键词，如 agents, nlp, multimodal, vision, reasoning, reinforcement learning 等"},
                    "days": {"type": "integer", "description": "收集最近几天的论文，默认 7"},
                    "max_results": {"type": "integer", "description": "最大结果数，默认 50"},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rank_papers",
            "description": "对已收集的论文排名，返回 top-k 结果",
            "parameters": {
                "type": "object",
                "properties": {
                    "top_k": {"type": "integer", "description": "返回前 k 篇，默认 5"},
                    "query": {"type": "string", "description": "排序时的查询关键词"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "collect_blogs",
            "description": "从 RSS/Atom 订阅源收集近期博客文章",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "研究方向"},
                    "days": {"type": "integer", "description": "收集最近几天，默认 30"},
                    "max_results": {"type": "integer", "description": "最大结果数，默认 30"},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "collect_github",
            "description": "收集 GitHub 热门仓库和 star 增长",
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {"type": "string", "description": "编程语言过滤，如 python, rust"},
                    "since": {"type": "string", "enum": ["daily", "weekly", "monthly"], "description": "时间范围，默认 weekly"},
                    "max_results": {"type": "integer", "description": "最大结果数，默认 20"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ingest_paper",
            "description": "下载并解析一篇论文的 PDF，提取文本和图表",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string", "description": "论文 ID，如 2401.00001 或 2606.07513v1"},
                },
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_article",
            "description": "从已解析的论文生成中文深度解读文章（Markdown + HTML）",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string", "description": "论文 ID"},
                },
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_writing_agent",
            "description": "运行论文写作 Agent：建立证据包、规划文章、生成草稿、证据审稿、自动修订并输出最终文章",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string", "description": "已解析论文 ID"},
                    "paper_url": {"type": "string", "description": "论文 URL；本地无解析产物时自动下载解析"},
                    "paper_title": {"type": "string", "description": "可选的展示标题"},
                    "notes_path": {"type": "string", "description": "可选的用户读书笔记路径"},
                    "article_path": {"type": "string", "description": "可选的待诊断和改写原始文章路径"},
                    "target_audience": {"type": "string", "description": "目标读者"},
                    "style_mode": {"type": "string", "enum": ["rigorous", "popular", "interview", "balanced"]},
                    "target_score": {"type": "integer", "description": "目标审稿分数，默认 85"},
                    "max_revisions": {"type": "integer", "description": "最大自动修订轮数，默认 3"},
                    "upload_images": {"type": "boolean", "description": "内容审稿通过后上传本地图片并替换链接"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prepare_agent_assets",
            "description": "为已通过内容审稿的 Agent 文章上传本地图片，并生成发布就绪版本",
            "parameters": {
                "type": "object",
                "properties": {
                    "article_json": {"type": "string", "description": "已有 Agent 草稿 JSON 路径"},
                    "target_score": {"type": "integer", "description": "目标审稿分数，默认 85"},
                },
                "required": ["article_json"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_article",
            "description": "审查文章质量，返回评分和维度检查",
            "parameters": {
                "type": "object",
                "properties": {
                    "article_path": {"type": "string", "description": "文章 JSON 或 Markdown 路径"},
                },
                "required": ["article_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "improve_article",
            "description": "用 LLM 润色文章，生成优化版本",
            "parameters": {
                "type": "object",
                "properties": {
                    "article_path": {"type": "string", "description": "文章 JSON 路径"},
                },
                "required": ["article_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_wechat_draft",
            "description": "从文章创建微信公众号草稿",
            "parameters": {
                "type": "object",
                "properties": {
                    "article_path": {"type": "string", "description": "文章 JSON 路径"},
                    "dry_run": {"type": "boolean", "description": "是否模拟运行，默认 true"},
                },
                "required": ["article_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "daily_digest",
            "description": "一键收集论文+博客+GitHub 热门，生成日报",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "研究方向"},
                    "days": {"type": "integer", "description": "天数，默认 7"},
                    "top_k": {"type": "integer", "description": "每个类别 top-k，默认 5"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trend_analysis",
            "description": "分析近期论文、博客、GitHub 的热点趋势",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "研究方向"},
                    "days": {"type": "integer", "description": "分析最近几天，默认 14"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_status",
            "description": "检查系统状态、LLM 连接、依赖就绪情况",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "agent_run",
            "description": "端到端自动运行：收集→排名→选论文→解读→生成→审查→发布",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "研究方向"},
                    "days": {"type": "integer", "description": "天数，默认 7"},
                    "top_k": {"type": "integer", "description": "选前 k 篇，默认 1"},
                    "dry_run": {"type": "boolean", "description": "是否模拟发布，默认 true"},
                },
            },
        },
    },
]

# Tool name → function mapping
_TOOL_MAP: dict[str, Any] = {}


def _get_workflow() -> SmearglePaperWorkflow:
    return SmearglePaperWorkflow()


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Execute a tool call and return JSON result string."""
    import traceback

    workflow = _get_workflow()
    try:
        if name == "collect_papers":
            result = workflow.collect(
                arguments.get("topic", "agents"),
                None,
                arguments.get("days", 7),
                ["arxiv"],
                arguments.get("max_results", 50),
            )
            return json.dumps({"count": len(result), "papers": [_summarize_paper(p) for p in result[:10]]}, ensure_ascii=False)

        elif name == "rank_papers":
            from .storage import read_json
            from .config import DATA_DIR
            from .models import PaperMeta
            papers = [PaperMeta.from_dict(p) for p in read_json(DATA_DIR / "papers" / "latest.json", [])]
            result = workflow.rank(papers, top_k=arguments.get("top_k", 5), query=arguments.get("query"))
            return json.dumps({"count": len(result), "ranked": [_summarize_ranked(r) for r in result]}, ensure_ascii=False)

        elif name == "collect_blogs":
            result = workflow.collect_blogs(
                arguments.get("topic", "agents"),
                None,
                arguments.get("days", 30),
                arguments.get("max_results", 30),
            )
            return json.dumps({"count": result.get("count", 0), "posts": result.get("posts", [])[:10]}, ensure_ascii=False)

        elif name == "collect_github":
            result = workflow.collect_github_stars(
                arguments.get("language"),
                arguments.get("since", "weekly"),
                arguments.get("max_results", 20),
            )
            return json.dumps({"count": result.get("count", 0), "repos": result.get("repos", [])[:10]}, ensure_ascii=False)

        elif name == "ingest_paper":
            from .models import PaperMeta
            from .storage import read_json
            from .config import DATA_DIR
            paper_id = arguments["paper_id"]
            paper = _find_paper(paper_id)
            result = workflow.read(paper)
            return json.dumps({"status": "ok", "parsed": result.get("parsed", "")}, ensure_ascii=False)

        elif name == "generate_article":
            from .models import PaperMeta
            from .storage import read_json
            from .config import DATA_DIR
            from pathlib import Path
            paper_id = arguments["paper_id"]
            paper = _find_paper(paper_id)
            pid = paper_id.replace("/", "_")
            parsed_path = DATA_DIR / "parsed" / f"{pid}.json"
            parsed = read_json(parsed_path, {})
            result = workflow.write_article(paper, parsed=parsed)
            return json.dumps({"status": "ok", "outputs": result}, ensure_ascii=False)

        elif name == "run_writing_agent":
            from pathlib import Path
            result = workflow.run_writing_agent(
                arguments.get("paper_id"),
                paper_url=arguments.get("paper_url"),
                paper_title=arguments.get("paper_title"),
                notes_path=Path(arguments["notes_path"]) if arguments.get("notes_path") else None,
                article_path=Path(arguments["article_path"]) if arguments.get("article_path") else None,
                target_audience=arguments.get("target_audience", "AI方向研究生和算法岗候选人"),
                style_mode=arguments.get("style_mode", "balanced"),
                target_score=arguments.get("target_score", 85),
                max_revisions=arguments.get("max_revisions", 3),
                upload_images=arguments.get("upload_images", False),
            )
            return json.dumps(
                {
                    "status": result.get("status"),
                    "run_dir": result.get("run_dir"),
                    "manifest": result.get("manifest"),
                    "final": result.get("final"),
                },
                ensure_ascii=False,
            )

        elif name == "prepare_agent_assets":
            from pathlib import Path
            result = workflow.prepare_agent_assets(
                Path(arguments["article_json"]),
                target_score=arguments.get("target_score", 85),
            )
            return json.dumps(result, ensure_ascii=False)

        elif name == "review_article":
            from .quality import review_article_file
            from pathlib import Path
            result = review_article_file(Path(arguments["article_path"]))
            return json.dumps(result, ensure_ascii=False)

        elif name == "improve_article":
            from .editor import improve_article_file
            from pathlib import Path
            result = improve_article_file(Path(arguments["article_path"]))
            return json.dumps({"status": "ok", "outputs": result}, ensure_ascii=False)

        elif name == "create_wechat_draft":
            from .models import Article
            from .wechat import WechatClient
            from .storage import read_json
            from pathlib import Path
            article = Article.from_dict(read_json(Path(arguments["article_path"]), {}))
            result = WechatClient(dry_run=arguments.get("dry_run", True)).create_draft(article)
            return json.dumps(result, ensure_ascii=False)

        elif name == "daily_digest":
            result = workflow.daily_digest(
                topic=arguments.get("topic", "agents"),
                days=arguments.get("days", 7),
                top_k=arguments.get("top_k", 5),
            )
            return json.dumps({
                "papers_count": result.get("papers", {}).get("count", 0),
                "blogs_count": result.get("blogs", {}).get("count", 0),
                "github_count": result.get("github_stars", {}).get("count", 0),
            }, ensure_ascii=False)

        elif name == "trend_analysis":
            result = workflow.trend_analysis(
                topic=arguments.get("topic", "agents"),
                days=arguments.get("days", 14),
            )
            return json.dumps({
                "paper_count": result.get("paper_count", 0),
                "blog_count": result.get("blog_count", 0),
                "hot_keywords": result.get("hot_keywords", [])[:10],
            }, ensure_ascii=False)

        elif name == "check_status":
            from .agents import check_agents
            return json.dumps(check_agents("all"), ensure_ascii=False)

        elif name == "agent_run":
            result = workflow.agent_run(
                topic=arguments.get("topic", "agents"),
                query=None,
                paper_url=None,
                days=arguments.get("days", 7),
                top_k=arguments.get("top_k", 1),
                max_results=50,
                collect_blogs=True,
                improve=True,
                create_draft=True,
                dry_run=arguments.get("dry_run", True),
            )
            return json.dumps({
                "status": result.get("status"),
                "selected_paper": result.get("selected_paper"),
                "report_dir": result.get("report_dir"),
            }, ensure_ascii=False)

        else:
            return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)

    except Exception as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)


def _find_paper(paper_id: str) -> Any:
    from .models import PaperMeta
    from .storage import read_json
    from .config import DATA_DIR
    for item in read_json(DATA_DIR / "papers" / "latest.json", []):
        if item.get("paper_id") == paper_id:
            return PaperMeta.from_dict(item)
    for path in [DATA_DIR / "ranked" / "latest.json"]:
        for row in read_json(path, []):
            paper = row.get("paper", {})
            if paper.get("paper_id") == paper_id:
                return PaperMeta.from_dict(paper)
    raise ValueError(f"Paper not found: {paper_id}")


def _summarize_paper(paper: dict) -> dict:
    return {
        "paper_id": paper.get("paper_id", ""),
        "title": paper.get("title", "")[:80],
        "published_at": paper.get("published_at", "")[:10],
    }


def _summarize_ranked(row: dict) -> dict:
    paper = row.get("paper", {})
    return {
        "rank": row.get("rank", 0),
        "score": row.get("score", 0),
        "paper_id": paper.get("paper_id", ""),
        "title": paper.get("title", "")[:80],
    }
