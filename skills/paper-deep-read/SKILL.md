---
name: paper-deep-read
id: autowechat.paper-deep-read
version: 0.1.0
description: |
  Deep-read a specific AI paper: download PDF, parse text, extract figures,
  generate a structured Chinese article with LLM, review quality, and improve.
  Use when user asks to "read this paper", "paper reading", "deep analysis",
  "解读论文", "读论文", "深度分析", or provides an arXiv URL.
user-invocable: true
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - AskUserQuestion
---

# Paper Deep Read: AI Paper Chinese Article Generation

You are a research paper analyst. Your job is to take an AI research paper and produce a high-quality Chinese close-reading article.

## When to Use

Trigger this skill when the user:
- Provides an arXiv URL (e.g., `https://arxiv.org/abs/2401.00001`)
- Asks to "read" or "analyze" a specific paper
- Wants a Chinese translation/summary of a paper
- Says "解读论文", "读论文", "paper reading", "深度分析"

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| paper_url | (required) | arXiv paper URL or paper ID |
| topic | `agents` | Context topic for ranking relevance |
| improve | `true` | Whether to run LLM improvement pass |
| dry_run | `true` | Whether to create real WeChat draft |

## Execution Flow

### Step 1: Identify the Paper

If user provides a URL, extract the paper ID. If user asks to pick from recent collection, list available papers:

```bash
cat data/papers/latest.json | python3 -c "
import sys, json
papers = json.loads(sys.stdin.read())
for i, p in enumerate(papers[:10], 1):
    print(f'{i}. {p[\"paper_id\"]} — {p[\"title\"][:80]}')
"
```

Let the user choose which paper to deep-read.

### Step 2: Download and Parse PDF

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper ingest-paper --paper-id <paper_id>
```

This downloads the PDF, extracts text, and saves parsed data to `data/parsed/<paper_id>.json`.

### Step 3: Generate Chinese Article

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper generate-article --paper-id <paper_id>
```

This generates:
- `data/articles/<paper_id>.md` — Markdown article
- `data/articles/<paper_id>.html` — WeChat-compatible HTML
- `data/articles/<paper_id>.json` — Article metadata
- `data/covers/<paper_id>.png` — Cover image

### Step 4: Review Article Quality

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper review-article data/articles/<paper_id>.json
```

Check the quality score. If score < 70, proceed to improvement.

### Step 5: Improve Article (Optional)

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper improve-article data/articles/<paper_id>.json
```

This generates optimized versions:
- `data/articles/<paper_id>.optimized.md`
- `data/articles/<paper_id>.optimized.html`
- `data/articles/<paper_id>.optimized.json`

### Step 6: Present the Article

Read the generated Markdown and present it to the user:

```bash
cat data/articles/<paper_id>.optimized.md
```

Or the non-optimized version if improvement was skipped.

### Step 7: Offer Publishing

Ask if the user wants to publish to WeChat:

```bash
# Dry-run (default)
./.conda/envs/smearglepaper/bin/python -m smearglepaper create-wechat-draft \
  --article-json data/articles/<paper_id>.optimized.json \
  --draft-only --dry-run

# Real publish
./.conda/envs/smearglepaper/bin/python -m smearglepaper create-wechat-draft \
  --article-json data/articles/<paper_id>.optimized.json \
  --draft-only
```

## Article Structure Template

The generated article should follow this structure:

```markdown
# 论文标题

## 一句话结论
[One sentence summarizing the paper's main contribution]

## 核心贡献
1. [Contribution 1]
2. [Contribution 2]
3. [Contribution 3]

## 背景与动机
[Why this research matters, what gap it fills]

## 方法详解
[Detailed explanation of the approach]

### 关键创新点
[What's novel about this method]

### 技术细节
[Technical details with references to figures if available]

## 实验结果
[Key experimental findings, metrics, comparisons]

### 主要结果
[Table or list of key results]

### 消融实验
[Ablation studies if available]

## 局限与展望
[Limitations and future directions]

## 相关工作
[Brief mention of related papers]

## 论文信息
- 作者: [Author list]
- 机构: [Institutions]
- 发表: [Venue/Date]
- 链接: [arXiv URL]
```

## Quality Standards

Before presenting the article, verify:

| Criterion | Check |
|-----------|-------|
| Information density | Each paragraph adds new information, no filler |
| Figure references | References paper figures when explaining methods |
| Technical accuracy | Preserves original paper's claims accurately |
| Chinese readability | Natural Chinese, not translationese |
| Structure completeness | All sections present and substantive |
| Length | 2000-4000 Chinese characters for typical paper |

## Error Handling

### PDF Download Fails
```
Failed to download PDF
```
Action: Check if the paper ID is correct. Try the PDF URL directly.

### LLM Not Configured
```
No LLM key configured
```
Action: Check `.env` for `OPENAI_BASE_URL` and `OPENAI_API_KEY`. Falls back to local template if not configured.

### Article Quality Too Low
If review score < 50:
1. Run `improve-article` to enhance
2. If still low, manually edit key sections
3. Present with quality warnings

## Examples

### Example 1: From URL

User: "帮我解读这篇论文 https://arxiv.org/abs/2401.00001"

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper ingest-paper --paper-id 2401.00001
./.conda/envs/smearglepaper/bin/python -m smearglepaper generate-article --paper-id 2401.00001
./.conda/envs/smearglepaper/bin/python -m smearglepaper review-article data/articles/2401.00001.json
```

### Example 2: From Recent Collection

User: "帮我解读排名最高的那篇论文"

```bash
# First check ranked papers
cat data/ranked/latest.json | python3 -c "
import sys, json
rows = json.loads(sys.stdin.read())
for i, r in enumerate(rows[:5], 1):
    p = r['paper']
    print(f'{i}. [{r[\"score\"]}] {p[\"paper_id\"]} — {p[\"title\"][:80]}')
"
# Then read the top one
./.conda/envs/smearglepaper/bin/python -m smearglepaper ingest-paper --paper-id <top_paper_id>
./.conda/envs/smearglepaper/bin/python -m smearglepaper generate-article --paper-id <top_paper_id>
```

### Example 3: Full Pipeline

User: "自动选一篇 agent 方向的论文，深度解读并生成草稿"

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper agent-run --topic agents --days 7 --top-k 1 --dry-run
```

## Output Location

- `data/parsed/<paper_id>.json` — Parsed PDF data
- `data/figures/<paper_id>/` — Extracted figures
- `data/articles/<paper_id>.md` — Markdown article
- `data/articles/<paper_id>.html` — HTML article
- `data/articles/<paper_id>.json` — Article metadata
- `data/articles/<paper_id>.optimized.md` — Improved article
- `data/covers/<paper_id>.png` — Cover image

## Follow-up Actions

After presenting the article, suggest:
1. "需要润色或修改吗？" → run improve-article
2. "要发布到微信公众号吗？" → triggers auto-publish
3. "要解读其他论文吗？" → repeat the process
4. "要发送飞书通知吗？" → send notification with article link
