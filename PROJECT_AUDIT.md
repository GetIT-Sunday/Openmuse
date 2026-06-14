# Project Audit

## 1. 当前目录结构

- `src/smearglepaper/`：核心业务、CLI、Agent 工具、工作流、解析、写作、审稿、发布。
- `src/mcp_server/`：MCP 入口。
- `tests/`：核心、工作流可靠性、TUI、Agent 测试。
- `skills/write-paper-wechat/`：论文公众号写作规则。
- `data/`：论文、解析结果、图表、文章、审稿、运行报告和微信草稿。
- `docs/`：MCP 与项目文档。

## 2. 当前入口

- CLI：`python -m smearglepaper.cli` / `smearglepaper`
- MCP：`python -m mcp_server.server`
- TUI：`smearglepaper tui`
- 端到端工作流：`smearglepaper agent-run`
- 论文写作 Agent：`smearglepaper writing-agent`

## 3. 已有 Agent / workflow / prompt / memory / output

| 类型 | 现有位置 | 状态 |
|---|---|---|
| Agent 健康检查 | `agents.py` | 已有 Scout、Reader、Writer、Editor、Publisher、Writing Agent 等 |
| 主工作流 | `workflow.py` | 已有 collect/read/write/review/improve/draft 闭环 |
| TUI 工作流定义 | `workflow_engine.py` | 已有流程定义，但执行器仍主要为 mock |
| LLM prompts | `llm.py`、`editor.py`、`writing_agent.py` | 已有写作、优化和 Agent prompt |
| 论文理解 | `reader.py`、`evidence.py` | 已有全文、章节树、证据账本、图表与表格 |
| 审稿 | `quality.py` | 已有通用质量与证据可追溯检查 |
| 发布 | `renderer.py`、`wechat.py` | 已有 HTML、封面、微信草稿 |
| Memory | 无统一模块 | 缺失 |
| Agent 输出 | `data/agent_runs/writing/` | 已有阶段产物，但未按角色分目录 |

## 4. 当前已有能力

1. arXiv 收集、论文排名和真实 PDF 下载。
2. 全页文本、章节树、Evidence Ledger、Figure / Table 提取。
3. LLM 写作与编辑。
4. 数字和图表引用可追溯审稿。
5. 自动修订循环与最高分版本选择。
6. CLI、MCP、TUI 工具调用。
7. 微信 HTML、图片上传与草稿创建。

## 5. 当前缺失能力

1. 用户原始文章的独立诊断节点。
2. 内置参考媒体风格画像与 StyleAnalyst。
3. 独立 TechnicalReviewer 和 WeChatEditor。
4. 技术分与公众号分双门禁。
5. 标准发布包。
6. 用户风格、来源画像和文章历史 Memory。
7. 统一 `workspace/paper_writing/` 输出目录。
8. 写作 Agent 对目标读者、风格模式、原始文章的显式输入。

## 6. 可直接复用

- `PaperReader`、`evidence.py`：PaperReader / TechnicalReviewer 的事实基础。
- `ArticleWriter`：DraftWriter 和 RevisionLoop 的 LLM 调用。
- `quality.py`：TechnicalReviewer 的确定性证据审计。
- `renderer.py`、`wechat.py`：PublishPackager 后续发布。
- `tools.py`、`cli.py`、`mcp_server/server.py`：运行入口。
- `skills/write-paper-wechat/`：写作与审稿规则。

## 7. 需要新增

- `agent_reviews.py`：NoteDiagnoser、TechnicalReviewer、WeChatEditor、PublishPackager。
- `memory.py`：轻量 JSON MemoryManager。
- `memory/*.json`：默认来源、风格、用户偏好和文章历史。
- `workspace/paper_writing/`：统一 Agent 输出。
- Agent prompt 文档：供审计和后续迭代使用。

## 8. 需要轻量修改

- `writing_agent.py`：增加独立节点、双审稿、发布包和 Memory。
- `workflow.py`、`cli.py`、MCP、工具定义：补充输入参数。
- `README.md`：说明新 Agent 使用方法。

## 9. 高风险修改点

- 不能破坏现有 `agent-run` 和微信发布流程。
- 审稿评分必须与证据门禁兼容，不能只依赖 LLM。
- 旧文章和 parsed JSON 需要保持向后兼容。
- 自动修订不能引入原论文证据之外的事实。

## 10. 最小改造方案

扩展现有 `PaperWritingAgent`，不新建平行工程。将当前单一 review 拆为 TechnicalReviewer 与 WeChatEditor；在同一运行中依次生成 diagnosis、style、outline、draft、双审稿、revision、publish package，并写入轻量 Memory。
