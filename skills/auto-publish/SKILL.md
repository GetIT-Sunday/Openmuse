---
name: auto-publish
id: autowechat.auto-publish
version: 0.1.0
description: |
  End-to-end pipeline: collect papers, rank, select best, read PDF, generate article,
  review quality, improve, and publish to WeChat Official Account.
  Use when user asks to "auto publish", "generate draft", "微信发布", "自动发布",
  "生成草稿", or wants the full pipeline.
user-invocable: true
allowed-tools:
  - Bash
  - Read
  - Write
  - AskUserQuestion
---

# Auto-Publish: End-to-End Paper-to-WeChat Pipeline

You are an automated publishing agent. Your job is to run the complete pipeline from paper collection to WeChat draft creation.

## When to Use

Trigger this skill when the user:
- Wants to automatically generate and publish a paper article
- Says "自动发布", "auto publish", "生成草稿", "微信发布"
- Wants the full pipeline without manual steps
- Asks to "just pick a paper and publish it"

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| topic | `latest_ai` | Research direction |
| days | `7` | Days to look back |
| top_k | `5` | Number of candidate papers to rank |
| paper_url | (empty) | Specific paper URL (skip collection if provided) |
| dry_run | `true` | Whether to create real WeChat draft |
| notify | `false` | Whether to send Feishu notification |

## Pre-flight Checks

Before running the pipeline, verify readiness:

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper check-status
```

Check:
- LLM is configured (OPENAI_BASE_URL + OPENAI_API_KEY)
- WeChat credentials if publishing (WECHAT_APP_ID + WECHAT_APP_SECRET)
- Feishu webhook if notifying (FEISHU_WEBHOOK_URL)

## Execution Flow

### Step 1: Confirm Parameters

Ask the user:
1. Which topic/direction?
2. How many days back?
3. Dry-run or real publish?
4. Notify via Feishu?

### Step 2: Run the Agent Pipeline

The `agent-run` command handles the full pipeline:

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper agent-run \
  --topic <topic> \
  --days <days> \
  --top-k <top_k> \
  --dry-run  # remove for real publish
```

This executes:
1. **Collect papers** from arXiv
2. **Collect blogs** from RSS feeds
3. **Rank papers** by relevance and freshness
4. **Select top paper** (rank #1)
5. **Read PDF** — download and parse
6. **Write article** — LLM generates Chinese article
7. **Review quality** — automated quality check
8. **Improve article** — LLM editing pass
9. **Create draft** — WeChat draft (dry-run or real)

### Step 3: Review the Output

Check the generated article:

```bash
# Read the article
cat data/articles/<paper_id>.optimized.md

# Check quality score
./.conda/envs/smearglepaper/bin/python -m smearglepaper review-article data/articles/<paper_id>.optimized.json
```

### Step 4: Present Results

Show the user:
1. Selected paper (title, authors, abstract)
2. Article preview (first 500 chars)
3. Quality score
4. Output file paths

### Step 5: Offer Adjustments

If the user wants changes:
- "换一篇论文" → re-run with different top_k
- "修改文章" → manual editing
- "重新润色" → run improve-article again
- "发布" → run without --dry-run

### Step 6: Publish (If Requested)

For real WeChat publish:

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper create-wechat-draft \
  --article-json data/articles/<paper_id>.optimized.json \
  --draft-only
```

For Feishu notification:

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper notify-feishu \
  --title "新文章发布" \
  --content "已生成论文解读：<paper_title>"
```

## Single-Paper Mode

If the user provides a specific paper URL, skip collection and ranking:

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper agent-run \
  --paper-url <arxiv_url> \
  --dry-run
```

## Error Handling

### No Papers Found
```
No papers collected. Try a broader query or longer --days window.
```
Action:
1. Increase `--days` (try 14 or 30)
2. Use broader topic (`latest_ai` instead of `agents`)
3. Check arXiv accessibility

### LLM Error
```
LLM API error
```
Action:
1. Check OPENAI_BASE_URL and OPENAI_API_KEY in .env
2. Verify the model is available
3. Falls back to local template if LLM fails

### WeChat Error
```
40164 error
```
Action: Add current IP to WeChat Official Account IP whitelist.

### Quality Too Low
If review score < 50:
1. Run improve-article to enhance
2. If still low, suggest manual editing
3. Present with quality warnings

## Pipeline Steps Detail

Each step in the pipeline:

| Step | Command | Output |
|------|---------|--------|
| 1. Collect | `collect-arxiv --topic X --days Y` | `data/papers/arxiv-{date}.json` |
| 2. Collect blogs | `collect-blogs --topic X` | `data/blogs/blogs-{date}.json` |
| 3. Rank | `rank-papers --top-k N` | `data/ranked/ranked-{date}.json` |
| 4. Read | `ingest-paper --paper-id ID` | `data/parsed/ID.json` |
| 5. Write | `generate-article --paper-id ID` | `data/articles/ID.md`, `.html`, `.json` |
| 6. Review | `review-article data/articles/ID.json` | Quality score + pass/fail |
| 7. Improve | `improve-article data/articles/ID.json` | `data/articles/ID.optimized.*` |
| 8. Draft | `publish-wechat --article-json ...` | WeChat draft |

## Examples

### Example 1: Quick Auto-Publish

User: "帮我自动生成一篇 agent 方向的论文解读"

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper agent-run --topic agents --days 7 --top-k 3 --dry-run
```

### Example 2: Specific Paper

User: "帮我把这篇论文生成微信草稿 https://arxiv.org/abs/2401.00001"

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper agent-run --paper-url https://arxiv.org/abs/2401.00001 --dry-run
```

### Example 3: Real Publish

User: "自动发布一篇 NLP 论文到微信公众号"

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper agent-run --topic nlp --days 7 --top-k 3 --real-wechat
```

### Example 4: With Notification

User: "生成论文解读并发飞书通知"

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper agent-run --topic agents --days 7 --top-k 3 --dry-run
./.conda/envs/smearglepaper/bin/python -m smearglepaper notify-feishu --title "新论文解读" --content "已生成 agent 方向论文解读"
```

## Output Location

- `data/runs/agent-run-YYYYMMDD-HHMMSS.json` — Full pipeline report
- `data/papers/latest.json` — Collected papers
- `data/ranked/latest.json` — Ranked papers
- `data/parsed/<paper_id>.json` — Parsed PDF
- `data/articles/<paper_id>.md` — Generated article
- `data/articles/<paper_id>.optimized.md` — Improved article
- `data/covers/<paper_id>.png` — Cover image

## Follow-up Actions

After the pipeline completes, suggest:
1. "要换一篇论文重新生成吗？"
2. "要发布到微信公众号吗？"
3. "要发送飞书通知吗？"
4. "要解读其他论文吗？"
