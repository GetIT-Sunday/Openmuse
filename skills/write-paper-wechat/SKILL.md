---
name: write-paper-wechat
description: Write, revise, or review evidence-grounded Chinese WeChat deep-read articles from research papers, parsed PDFs, arXiv links, figures, or existing drafts. Use for 论文解读、公众号深度文章、逐图讲论文、论文改写、论文排版、文章质量审查, or when turning academic evidence into a clear public-facing technical narrative.
id: autowechat.write-paper-wechat
version: 0.1.0
requires:
  - paper-deep-read
---

# Write Paper WeChat

Create a rigorous Chinese paper deep-read that teaches through evidence and figures. Optimize for clarity and trust before virality.

## Load References

- Read [references/writing-playbook.md](references/writing-playbook.md) before drafting or substantially rewriting.
- Read [references/style-patterns.md](references/style-patterns.md) when choosing tone, structure, visual rhythm, or WeChat formatting.
- Read [references/quality-rubric.md](references/quality-rubric.md) before final review.

## Workflow

### 1. Build the evidence ledger

Read the paper metadata, abstract, parsed text, figures, captions, and existing article. Record:

- research question and prior bottleneck
- paper claims, each with supporting section, figure, table, or metric
- method components and their causal roles
- experiment setup, baselines, metrics, and results
- limitations stated by authors versus editor inference
- figure inventory: what each image shows and which claim it supports

Never draft quantitative claims from memory. Mark missing evidence explicitly.

### 2. Choose one reader promise

Define one sentence:

> After reading, the target reader will understand `<specific idea>` well enough to `<specific outcome>`.

Remove sections that do not serve this promise. Choose the target reader: general technical reader, engineer, researcher, or decision-maker.

### 3. Design the narrative

Default sequence must reconstruct the paper's argument, not merely teach its topic:

1. 一句话讲清论文
2. 作者从什么具体问题出发
3. 作者的关键假设与设计选择
4. 论文如何一步步论证
5. 方法逐图拆解
6. 每组实验在验证哪条主张
7. 关键表格与消融改变了什么判断
8. 证据支持什么、不支持什么
9. 局限、后续影响与论文信息

Adapt the structure to the paper; do not force empty sections.

Reject drafts that could have been written without reading the paper. A paper explanation must mention paper-specific decisions, experiment settings, comparisons, and evidence boundaries.

Keep later historical impact out of the main argument. Use it only near the end, after reconstructing what the original paper itself claimed and tested.

### 4. Draft through figures

Use each selected figure for a teaching purpose:

1. Introduce the question the figure answers.
2. Show the figure.
3. Explain how to read it.
4. State the evidence-backed takeaway.

Prefer 4-8 meaningful figures for a typical deep-read. Do not use decorative or redundant figures. Do not dump all figures at the end.

### 5. Control claims

Use three explicit evidence modes:

- **论文显示/结果显示**: directly supported by paper evidence.
- **作者认为/作者提出**: an author claim not independently established.
- **可以理解为/编辑解读**: explanatory interpretation.

Avoid “彻底、革命性、证明了、全面领先、首次” unless the paper provides narrow, direct evidence. Preserve uncertainty and experimental scope.

### 6. Fit the word budget

Choose the smallest format that teaches the paper:

- quick read: 1,500-2,500 Chinese characters, 2-4 figures
- standard deep-read: 3,000-5,000 characters, 4-8 figures
- technical deep-dive: 5,000-8,000 characters, 6-12 figures

See [references/writing-playbook.md](references/writing-playbook.md) for section budgets.

### 7. Format for WeChat

Use short paragraphs, meaningful subheadings, restrained emphasis, captions, and visible breathing room. Follow [references/style-patterns.md](references/style-patterns.md).

### 8. Validate

Run:

```bash
python skills/write-paper-wechat/scripts/analyze_article.py <article.md>
```

Then apply [references/quality-rubric.md](references/quality-rubric.md). Revise any critical failure before presenting or creating a real draft.

## Non-Negotiables

- Preserve the original paper link.
- Attribute experimental numbers to their setting.
- Distinguish author claims from editor interpretation.
- Explain every included figure.
- Keep paragraphs focused on one idea.
- Do not publish or update a real WeChat draft without explicit user approval.

## Deliverables

Return:

- final Markdown article
- evidence/uncertainty warnings that remain
- article metrics from the analyzer
- recommended next action: revise, create draft, or publish
