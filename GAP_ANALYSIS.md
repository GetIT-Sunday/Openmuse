# Gap Analysis

| 能力模块 | 当前是否存在 | 现有文件/位置 | 问题 | 优化方式 | 优先级 |
|---|---|---|---|---|---|
| Intake | 部分 | `cli.py`、`workflow.py` | 仅支持 paper_id 和 notes，缺目标读者、风格、原始文章 | 修改 | P0 |
| SourceScout | 部分 | `scout.py`、`blogs.py` | 偏选题，不管理参考媒体风格 | 复用并补 Memory | P1 |
| StyleAnalyst | 否 | 无 | 无内置风格组合报告 | 新增轻量节点 | P1 |
| PaperReader | 是 | `reader.py`、`evidence.py` | 已有章节、证据和图表 | 复用 | P0 |
| NoteDiagnoser | 否 | 无 | 无独立原稿诊断产物 | 新增 | P0 |
| OutlinePlanner | 部分 | `writing_agent.py` strategy | 输出名和结构不标准 | 修改 | P0 |
| DraftWriter | 是 | `llm.py`、`writing_agent.py` | 已可生成正文 | 复用 | P0 |
| TechnicalReviewer | 部分 | `quality.py` | 单一报告，缺标准 JSON/Markdown | 扩展 | P0 |
| WeChatEditor | 否 | 无 | 技术正确与公众号可读性未分离 | 新增 | P0 |
| RevisionLoop | 是 | `writing_agent.py` | 仅依据单一 review | 扩展双审稿 | P0 |
| PublishPackager | 部分 | `renderer.py`、`wechat.py` | 缺标题、摘要、导语、标签等发布包 | 新增 | P0 |
| MemoryManager | 否 | 无 | 无用户风格和历史记忆 | 新增轻量 JSON | P1 |
