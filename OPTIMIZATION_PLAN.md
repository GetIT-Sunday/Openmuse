# Optimization Plan

> Status: the original Paper Writing Agent phases below are substantially implemented.
> The active product-level roadmap is now [PRODUCT_AGENT_PLAN.md](PRODUCT_AGENT_PLAN.md),
> which covers intent routing, discovery, approvals, resumable tasks, previews,
> idempotent WeChat draft creation, and scheduled research tasks.

## Phase 1：跑通 Agent 闭环

在现有 `PaperWritingAgent` 上增加：

```text
Intake
  -> NoteDiagnoser
  -> StyleAnalyst
  -> OutlinePlanner
  -> DraftWriter
  -> TechnicalReviewer
  -> WeChatEditor
  -> RevisionLoop
  -> PublishPackager
```

统一输出到 `workspace/paper_writing/<run-id>/`，同时保留 `data/articles/*.agent.*` 兼容现有发布流程。

## Phase 2：提升写作质量

1. 使用内置来源画像组合风格，不复制公众号全文。
2. 增强标题、开头、图表解释和双审稿 checklist。
3. 对经典论文增加特定风险检测，但不写死到通用流程。

## Phase 3：增强 Agent 能力

1. 最多三轮自动修订。
2. 技术分和公众号分双门禁，均达到 85 才 ready。
3. 保存每轮草稿和审稿报告。
4. 写入文章历史和用户风格 Memory。

## Phase 4：未来扩展

1. 参考文章链接解析与来源管理。
2. Web UI 中展示节点、评分和版本差异。
3. 多方向写作模式：NLP / CV / VLM / Agent / RAG / RL。
4. 从发布包直接驱动微信草稿预览和人工确认。

## 回滚边界

- 不修改现有 parsed/article 数据格式的必填字段。
- 不删除现有 CLI、MCP、TUI 和微信入口。
- 新 Agent 节点失败时保留中间产物并返回 `needs_human_review`。
