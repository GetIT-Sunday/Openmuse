---
name: daily-digest
id: autowechat.daily-digest
version: 0.1.0
description: |
  Collect and summarize daily AI research papers, blog posts, and GitHub trending repos.
  Generates a structured digest report. Optionally sends to Feishu.
  Use when user asks for "daily digest", "today's papers", "collect papers", "日报",
  "收集论文", "今日论文", or similar collection requests.
user-invocable: true
allowed-tools:
  - Bash
  - Read
  - Write
  - AskUserQuestion
---

# Daily Digest: AI Research Information Collection

You are an AI research information collector. Your job is to gather the latest papers, blog posts, and GitHub trending repos, then present a structured digest.

## When to Use

Trigger this skill when the user asks to:
- Collect today's or this week's AI papers
- Get a daily/weekly research digest
- See what's new in a research area
- Check trending GitHub repos
- "日报", "收集论文", "今日论文", "daily digest", "what's new"

## Parameters

Before executing, confirm these with the user (use defaults if not specified):

| Parameter | Default | Description |
|-----------|---------|-------------|
| topic | `agents` | Research direction. Use `topics` command to see presets. |
| days | `7` | How many days back to look |
| top_k | `5` | Number of items to show per category |
| language | (empty) | GitHub language filter (python, rust, etc.) |
| notify | `false` | Whether to send to Feishu |

## Execution Flow

### Step 1: Determine Direction

If the user specifies a topic, use it. Otherwise, ask or use `agents` as default.

```bash
cd /Users/wengchuangchuang/Documents/LLM-Learning
./.conda/envs/smearglepaper/bin/python -m smearglepaper topics
```

### Step 2: Collect Papers, Blogs, and GitHub Stars

Run the daily-digest command which collects all three in one pass:

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper daily-digest \
  --topic <topic> \
  --days <days> \
  --top-k <top_k> \
  --language <language>
```

This saves results to `data/digest/latest.json`.

### Step 3: Read and Parse Results

Read the output file to extract the digest:

```bash
cat data/digest/latest.json
```

### Step 4: Format the Digest

Present the results in this format:

```markdown
## 📄 论文 Top N

| # | 标题 | 分数 | 日期 |
|---|------|------|------|
| 1 | [Paper Title](arxiv_url) | X.X | YYYY-MM-DD |

**摘要要点：**
- Paper 1: one-line summary of the abstract
- Paper 2: ...

## 📝 博客 Top N

| # | 标题 | 来源 | 日期 |
|---|------|------|------|
| 1 | [Blog Title](url) | Source | date |

## ⭐ GitHub Star 周报 Top N

| # | 仓库 | Stars | 周增长 | 语言 |
|---|------|-------|--------|------|
| 1 | [owner/repo](url) | N | +M | lang |
```

### Step 5: Send Notification (Optional)

If the user wants Feishu notification:

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper daily-digest \
  --topic <topic> --days <days> --top-k <top_k> --notify
```

Or send manually:

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper notify-feishu \
  --title "AI 研究日报" --content "<formatted_digest>"
```

## Error Handling

### arXiv Rate Limit (429)
```
arXiv is rate limiting requests. Wait a few minutes and try again.
```
Action: Wait 3 minutes, retry once. If still failing, suggest the user try again later.

### RSS Feed Timeout
Some blog feeds may timeout. This is normal. The digest will include whatever feeds succeeded. Note which feeds failed in the output.

### GitHub API Rate Limit
```
API rate limit exceeded
```
Action: Suggest configuring `GITHUB_TOKEN` in `.env` for 5000 requests/hour.

### No Results
If no papers found for the given topic/days:
1. Suggest broadening the topic (e.g., `latest_ai` instead of `agents`)
2. Suggest increasing `--days`
3. Check if arXiv is accessible

## Examples

### Example 1: Basic Daily Digest

User: "帮我收集今天的 AI agent 论文"

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper daily-digest --topic agents --days 1 --top-k 5
```

Then format and present the results.

### Example 2: Weekly Digest with GitHub

User: "本周 AI 研究热点，包括 GitHub"

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper daily-digest --topic latest_ai --days 7 --top-k 10 --language python
```

### Example 3: Specific Direction

User: "NLP 语义方向最近有什么新论文"

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper daily-digest --topic nlp_semantics --days 14 --top-k 5
```

## Output Location

All collected data is saved to:
- `data/digest/latest.json` — combined digest
- `data/digest/papers.json` — papers only
- `data/digest/blogs.json` — blogs only
- `data/digest/github.json` — GitHub repos only
- `data/papers/latest.json` — raw paper collection
- `data/blogs/latest.json` — raw blog collection
- `data/github/latest.json` — raw GitHub data

## Follow-up Actions

After presenting the digest, suggest:
1. "想深入解读哪篇论文？" → triggers paper-deep-read
2. "要生成趋势分析报告吗？" → triggers trend-analysis
3. "要自动发布到微信吗？" → triggers auto-publish
4. "需要发送飞书通知吗？" → sends notification
