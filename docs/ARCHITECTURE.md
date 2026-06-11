# SmearglePaper Agent Architecture Guide

## 1. 设计目标

将 SmearglePaper 从 Pipeline 改造为 Agent 体系，实现：

- **用户通过自然语言与 Agent 交互**，Agent 根据意图选择 Skill，Skill 调用 CLI 工具完成任务
- **CLI 工具层面向开发者**，每个命令职责单一、命名精确、输入输出明确
- **Artifact 驱动**，每个命令产生明确的文件产物，支持断点续跑和质量审查
- **分层解耦**，用户层、Agent 层、Skill 层、Workflow 层、CLI 层、Artifact 层各司其职

## 2. 分层架构

```
┌─────────────────────────────────────────────────────────┐
│                    User Layer                            │
│  用户通过自然语言提出需求                                  │
│  "帮我收集今天的 AI 论文" / "解读这篇论文"                   │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                   Agent Layer                            │
│  CLAUDE.md 定义 Agent 身份、工具列表、决策框架              │
│  意图识别 → Skill 路由 → 参数收集 → 执行 → 结果展示         │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                   Skill Layer                            │
│  skills/*/SKILL.md 定义各能力的完整执行流程                 │
│  daily-digest · paper-deep-read · auto-publish · trend   │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                  Workflow Layer                           │
│  workflow.py 编排多个 CLI 命令的执行顺序和数据流转           │
│  处理错误、重试、断点续跑                                   │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                    CLI Layer                              │
│  cli.py 提供原子命令，每个命令做一件事                       │
│  collect-arxiv · ingest-paper · generate-article · ...    │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│               Artifact / State Layer                      │
│  data/ 目录下的 JSON 文件，每个命令的输入输出有明确契约       │
│  支持断点续跑、质量审查、历史追溯                            │
└─────────────────────────────────────────────────────────┘
```

## 3. CLI 命令设计

### 3.1 命名原则

- **一个命令做一件事**：不混合收集和排序，不混合生成和发布
- **命名精确反映动作和对象**：`collect-arxiv` 而非 `collect`，`ingest-paper` 而非 `read`
- **动词选择**：collect（收集）、ingest（摄入/解析）、generate（生成）、review（审查）、improve（润色）、publish（发布）、check（检查）

### 3.2 命令列表

| 命令 | 动作 | 输入 | 输出 |
|------|------|------|------|
| `collect-arxiv` | 从 arXiv 收集论文 | `--topic`, `--query`, `--days`, `--max-results` | `data/papers/arxiv-{date}.json` |
| `collect-blogs` | 从 RSS 收集博客 | `--topic`, `--days`, `--max-results` | `data/blogs/blogs-{date}.json` |
| `collect-github` | 收集 GitHub 热门仓库 | `--language`, `--since`, `--max-results` | `data/github/github-{date}.json` |
| `rank-papers` | 对论文排序打分 | `--input`, `--top-k`, `--query` | `data/ranked/ranked-{date}.json` |
| `ingest-paper` | 下载并解析单篇论文 | `--paper-id` 或 `--paper-url` | `data/parsed/{paper_id}.json` |
| `generate-article` | 从论文生成中文文章 | `--paper-id`, `--parsed` | `data/articles/{paper_id}.md`, `.html`, `.json` |
| `review-article` | 审查文章质量 | `--article-json` | `data/reviews/{paper_id}.json` |
| `improve-article` | LLM 润色文章 | `--article-json`, `--output-prefix` | `data/articles/{paper_id}.optimized.*` |
| `create-wechat-draft` | 创建微信草稿 | `--article-json`, `--draft-only`, `--dry-run` | `data/wechat/{paper_id}.json` |
| `notify-feishu` | 发送飞书通知 | `--title`, `--content`, `--msg-type` | stdout |
| `check-artifacts` | 检查已有产物 | `--paper-id` | stdout (JSON) |
| `check-status` | 检查各组件就绪状态 | `--agent` | stdout (JSON) |
| `daily-digest` | 编排：收集论文+博客+GitHub | `--topic`, `--days`, `--top-k` | `data/digest/digest-{date}.json` |
| `trend-analysis` | 编排：趋势分析 | `--topic`, `--days`, `--top-k` | `data/digest/trend-{date}.json` |
| `agent-run` | 编排：完整发布流程 | `--topic`, `--days`, `--top-k`, `--resume` | `data/runs/run-{timestamp}/` |
| `tui` | 启动交互式 TUI Agent | (无参数) | 交互式界面 |

### 3.3 向后兼容

保留旧命令名作为别名，输出 deprecation warning：

```python
# cli.py 中
ALIASES = {
    "collect": "collect-arxiv",
    "rank": "rank-papers",
    "read": "ingest-paper",
    "write": "generate-article",
    "draft": "agent-run",
}

def resolve_alias(command: str) -> str:
    if command in ALIASES:
        print(f"Warning: '{command}' is deprecated, use '{ALIASES[command]}'", file=sys.stderr)
        return ALIASES[command]
    return command
```

## 4. Artifact 契约

### 4.1 目录结构

```
data/
├── papers/
│   └── arxiv-2026-06-07.json          # collect-arxiv 输出
├── blogs/
│   └── blogs-2026-06-07.json          # collect-blogs 输出
├── github/
│   └── github-2026-06-07.json         # collect-github 输出
├── ranked/
│   └── ranked-2026-06-07.json         # rank-papers 输出
├── parsed/
│   └── 2401.00001.json                # ingest-paper 输出
├── articles/
│   ├── 2401.00001.md                  # generate-article 输出
│   ├── 2401.00001.html
│   ├── 2401.00001.json
│   └── 2401.00001.optimized.*         # improve-article 输出
├── reviews/
│   └── 2401.00001.json                # review-article 输出
├── published/
│   └── 2401.00001.json                # publish-wechat 输出
├── digest/
│   ├── digest-2026-06-07.json         # daily-digest 输出
│   └── trend-2026-06-07.json          # trend-analysis 输出
├── runs/
│   └── run-20260607-143000.json       # agent-run 输出
├── covers/
│   └── 2401.00001.png
└── snapshots/
    └── github-2026-06-06.json         # GitHub 快照（用于计算增长）
```

### 4.2 关键 Schema

**arxiv-{date}.json** — `collect-arxiv` 输出：
```json
[
  {
    "paper_id": "2401.00001",
    "title": "Paper Title",
    "authors": ["Author A", "Author B"],
    "abstract": "...",
    "source": "arxiv",
    "url": "https://arxiv.org/abs/2401.00001",
    "pdf_url": "https://arxiv.org/pdf/2401.00001",
    "published_at": "2026-06-01",
    "collected_at": "2026-06-07T10:00:00Z"
  }
]
```

**ranked-{date}.json** — `rank-papers` 输出：
```json
[
  {
    "rank": 1,
    "score": 8.5,
    "paper": { "...PaperMeta..." },
    "reasons": ["Fresh (2 days)", "High relevance to agents"]
  }
]
```

**{paper_id}.json** — `ingest-paper` 输出：
```json
{
  "paper_id": "2401.00001",
  "title": "Paper Title",
  "abstract": "...",
  "full_text": "...",
  "figures": ["data/figures/2401.00001_fig1.png"],
  "sections": [
    {"heading": "Introduction", "text": "..."},
    {"heading": "Method", "text": "..."}
  ],
  "parsed_at": "2026-06-07T10:05:00Z"
}
```

**review-{paper_id}.json** — `review-article` 输出：
```json
{
  "paper_id": "2401.00001",
  "score": 72,
  "threshold": 60,
  "pass": true,
  "checks": {
    "has_conclusion": true,
    "has_method_section": true,
    "has_figures": true,
    "word_count_ok": true,
    "terminology_accurate": true
  },
  "suggestions": ["建议补充实验细节"],
  "reviewed_at": "2026-06-07T10:10:00Z"
}
```

**{paper_id}.json** — `publish-wechat` 输出：
```json
{
  "paper_id": "2401.00001",
  "media_id": "MEDIA_ID_FROM_WECHAT",
  "published": false,
  "draft_only": true,
  "dry_run": true,
  "published_at": "2026-06-07T10:15:00Z"
}
```

## 5. Agent 行为规范

### 5.1 Skill 路由

Agent 根据用户意图选择 Skill，路由规则写在 CLAUDE.md 中：

| 关键词 | Skill |
|--------|-------|
| 日报、daily digest、收集论文、今日论文 | daily-digest |
| 解读论文、读论文、paper reading、深度分析 | paper-deep-read |
| 自动发布、auto publish、生成草稿、微信发布 | auto-publish |
| 趋势、trend、热点分析、本周热点 | trend-analysis |

### 5.2 参数收集

Skill 触发后，如果缺少必要参数，Agent 应主动询问：

```
用户: "帮我收集今天的论文"
Agent: [触发 daily-digest]
       缺少 topic 参数，询问用户
       "请问您关注哪个方向？默认是 agents（LLM 智能体方向）"
```

### 5.3 执行模式

每个 Skill 定义了标准执行流程。Agent 按步骤调用 CLI 命令，每步完成后检查产物是否存在，再进入下一步。

### 5.4 结果展示

Agent 收到 CLI 输出后，转换为用户友好的格式展示，而非直接输出 JSON。

### 5.5 对话式 TUI

除了 CLI 和 Claude Code Agent 两种使用方式外，SmearglePaper 还提供基于 Textual 的对话式 TUI：

```bash
smearglepaper tui
```

TUI 采用三栏布局（类似 opencode）：

```
┌──────────┬───────────────────────────────────────┐
│ Sidebar  │  Chat Area (RichLog)                  │
│ 状态信息  │  [You] 帮我收集 agents 方向论文        │
│ 当前方向  │  [Tool] collect_papers → 23 篇        │
│ LLM 状态  │  [Agent] 收集完成，排名如下...         │
│ 已有产物  │                                       │
├──────────┴───────────────────────────────────────┤
│ > 输入消息...                              [发送] │
└──────────────────────────────────────────────────┘
```

核心特性：
- **真实 LLM 对话**：通过 OpenAI 兼容 API 实现 function calling
- **Agentic Loop**：LLM 自动决定调用工具，多轮推理直到完成任务
- **13 个工具**：collect_papers, rank_papers, collect_blogs, collect_github, ingest_paper, generate_article, review_article, improve_article, create_wechat_draft, daily_digest, trend_analysis, check_status, agent_run
- **斜杠命令**：`/clear`, `/topic`, `/status`, `/help`
- **后台执行**：工具调用在后台线程运行，不阻塞 UI

关键文件：
- `src/smearglepaper/tui.py` — TUI 界面
- `src/smearglepaper/tools.py` — 工具定义和执行器
- `src/smearglepaper/llm.py` — `chat_with_tools()` 和 `agentic_loop()`

## 6. 断点续跑（Breakpoint Resume）

### 6.1 原理

`check-artifacts` 命令检查某个 paper_id 的产物完成状态，返回已完成和未完成的步骤。Workflow 根据结果跳过已完成的步骤。

### 6.2 check-artifacts 输出

```json
{
  "paper_id": "2401.00001",
  "artifacts": {
    "parsed": {"exists": true, "path": "data/parsed/2401.00001.json"},
    "article_md": {"exists": true, "path": "data/articles/2401.00001.md"},
    "article_json": {"exists": true, "path": "data/articles/2401.00001.json"},
    "review": {"exists": false, "path": "data/reviews/2401.00001.json"},
    "optimized": {"exists": false},
    "published": {"exists": false}
  },
  "next_step": "review-article",
  "completed_steps": ["ingest-paper", "generate-article"],
  "remaining_steps": ["review-article", "improve-article", "publish-wechat"]
}
```

### 6.3 Workflow 中的使用

```python
def agent_run(self, paper_id, dry_run=True):
    status = self.check_artifacts(paper_id)

    if not status["artifacts"]["parsed"]["exists"]:
        self.ingest_paper(paper_id)

    if not status["artifacts"]["article_json"]["exists"]:
        self.generate_article(paper_id)

    if not status["artifacts"]["review"]["exists"]:
        self.review_article(paper_id)

    review = read_json(f"data/reviews/{paper_id}.json")
    if not review.get("pass"):
        # 质量未通过，尝试润色
        self.improve_article(paper_id)

    if not status["artifacts"]["published"]["exists"]:
        self.publish_wechat(paper_id, dry_run=dry_run)
```

## 7. 质量审查（Quality Gate）

### 7.1 流程

```
generate-article → review-article → pass? → publish-wechat
                                    fail? → improve-article → review-article → publish-wechat
```

### 7.2 审查维度

| 检查项 | 权重 | 说明 |
|--------|------|------|
| has_conclusion | 15% | 是否有一句话结论 |
| has_method_section | 20% | 是否有方法描述 |
| has_figures | 10% | 是否引用了论文图表 |
| word_count_ok | 15% | 字数在 800-3000 范围 |
| terminology_accurate | 20% | 专业术语是否准确 |
| structure_complete | 10% | 是否有完整结构（引言/方法/实验/结论） |
| readability | 10% | 句子平均长度、段落分布 |

### 7.3 阈值

- **score >= 60**：pass，可发布
- **score < 60**：fail，需要润色
- 润色后仍 < 60：提示用户手动检查

## 8. 迁移计划

### Phase 1：命令重命名 + 别名兼容

1. 在 cli.py 中添加新命令名
2. 保留旧命令名作为别名，输出 deprecation warning
3. 更新 CLAUDE.md 中的命令表

### Phase 2：Artifact 目录和契约

1. 规范 data/ 目录结构
2. 每个命令的输出使用日期后缀（如 `arxiv-2026-06-07.json`）
3. 添加 latest.json 软链接或索引指向最新文件

### Phase 3：Workflow 拆分

1. 将 workflow.py 中的大方法拆为独立函数
2. 每个函数对应一个 CLI 命令
3. agent_run 和 daily_digest 作为编排层调用原子函数

### Phase 4：check-artifacts 断点续跑

1. 实现 check-artifacts 命令
2. 在 agent_run 中集成断点检查
3. 支持从任意步骤恢复执行

### Phase 5：review/improve 质量循环

1. 增强 review-article 的检查维度
2. review 输出 pass/fail/score/threshold
3. agent_run 根据 review 结果决定是否 improve

### Phase 6：Skills 更新

1. 更新 4 个 SKILL.md 使用新命令名
2. 添加 check-artifacts 步骤
3. 添加质量审查步骤

### Phase 7：MCP Server 更新

1. 更新 MCP tool 名称和参数
2. 添加 check_artifacts tool
3. 更新 docstring

### Phase 8：测试和验证

1. 更新所有测试用例使用新命令名
2. 添加 artifact 契约测试
3. 端到端 dry-run 验证

## 9. 目录结构总览

```
/Users/wengchuangchuang/Documents/LLM-Learning/
├── CLAUDE.md                              # Agent 主定义
├── .env                                   # 环境变量
├── .env.example                           # 环境变量模板
├── skills/
│   ├── daily-digest/SKILL.md              # 信息收集日报
│   ├── paper-deep-read/SKILL.md           # 论文深度解读
│   ├── auto-publish/SKILL.md              # 端到端自动发布
│   └── trend-analysis/SKILL.md            # 趋势分析
├── src/
│   ├── smearglepaper/
│   │   ├── cli.py                         # CLI 入口
│   │   ├── workflow.py                    # 编排层
│   │   ├── collector.py                   # arXiv 收集
│   │   ├── blogs.py                       # 博客收集
│   │   ├── github_tracker.py              # GitHub 追踪
│   │   ├── ranker.py                      # 论文排序
│   │   ├── reader.py                      # PDF 解析
│   │   ├── llm.py                         # LLM 文章生成
│   │   ├── editor.py                      # LLM 润色
│   │   ├── quality.py                     # 质量审查
│   │   ├── renderer.py                    # HTML 渲染
│   │   ├── cover.py                       # 封面生成
│   │   ├── wechat.py                      # 微信 API
│   │   ├── notifier.py                    # 飞书通知
│   │   ├── config.py                      # 配置管理
│   │   ├── models.py                      # 数据模型
│   │   ├── storage.py                     # 文件读写
│   │   └── agents.py                      # Agent 检查
│   └── mcp_server/
│       └── server.py                      # MCP 工具入口
├── tests/                                 # 测试
├── data/                                  # 产物目录
└── docs/
    ├── ARCHITECTURE.md                    # 本文档
    ├── CONFIGURATION.md                   # 配置说明
    ├── MCP.md                             # MCP 使用说明
    └── WECHAT.md                          # 微信接入说明
```
