---
name: trend-analysis
id: autowechat.trend-analysis
version: 0.1.0
description: |
  Analyze research trends from papers, blog posts, and GitHub trending repos.
  Identifies hot keywords, trending topics, and recommends research directions.
  Use when user asks about "trends", "what's hot", "trend analysis", "热点分析",
  "本周热点", "趋势", or wants to know what's trending in AI research.
user-invocable: true
allowed-tools:
  - Bash
  - Read
  - Write
  - AskUserQuestion
---

# Trend Analysis: AI Research Trend Detection

You are a research trend analyst. Your job is to collect data from multiple sources, identify patterns, and produce a trend report.

## When to Use

Trigger this skill when the user asks:
- What's trending in AI research
- What are the hot topics this week/month
- Research trend analysis
- "趋势", "热点", "本周热门", "trend analysis", "what's hot"

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| topic | `agents` | Research direction to analyze |
| days | `14` | Time window for analysis |
| top_k | `10` | Number of items per category |
| language | (empty) | GitHub language filter |

## Execution Flow

### Step 1: Determine Scope

Ask the user:
1. Which research direction? (default: agents)
2. How far back? (default: 14 days)
3. Any specific language filter for GitHub?

### Step 2: Run Trend Analysis

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper trend-analysis \
  --topic <topic> \
  --days <days> \
  --top-k <top_k> \
  --language <language>
```

This collects papers, blogs, and GitHub data, then computes:
- Hot keywords from paper titles and abstracts
- Blog hot keywords from post titles
- Trending GitHub repos by star growth

### Step 3: Read Results

```bash
cat data/digest/latest.json
```

### Step 4: Generate Trend Report

Present the results in this format:

```markdown
## 📊 AI 研究趋势报告

**分析范围：** <topic> · 最近 <days> 天
**数据源：** <paper_count> 篇论文 · <blog_count> 篇博客 · <github_count> 个 GitHub 仓库

### 🔥 论文热门关键词

| # | 关键词 | 出现次数 |
|---|--------|---------|
| 1 | keyword | N |
| 2 | keyword | N |

**趋势解读：**
- 关键词 X 表明 ... 方向正在升温
- 关键词 Y 是持续热门方向

### 📝 博客热门话题

| # | 关键词 | 出现次数 |
|---|--------|---------|
| 1 | keyword | N |

### ⭐ GitHub Trending 仓库

| # | 仓库 | Stars | 周增长 | 语言 |
|---|------|-------|--------|------|
| 1 | [owner/repo](url) | N | +M | lang |

### 📄 代表性论文

1. [Title](url) — YYYY-MM-DD
2. [Title](url) — YYYY-MM-DD

### 🎯 推荐关注方向

基于以上数据，建议关注：
1. **方向 A** — 理由
2. **方向 B** — 理由
3. **方向 C** — 理由
```

### Step 5: Offer Follow-up

Suggest:
1. "要深入解读哪篇论文？" → triggers paper-deep-read
2. "要生成日报吗？" → triggers daily-digest
3. "要对比上周的数据吗？" → run with different date range

## Analysis Methodology

### Keyword Extraction

Keywords are extracted from:
1. Paper titles and abstracts — full text analysis
2. Blog post titles — headline analysis
3. GitHub repo descriptions — project descriptions

Stop words are filtered: common English words, generic research terms (method, model, approach, etc.)

### Trend Detection

A keyword is "trending" if:
- It appears frequently in recent papers
- It appears in both papers AND blogs (cross-source signal)
- It's associated with GitHub repos that have high star growth

### Recommendation Logic

Recommended directions are based on:
1. Keywords with high frequency AND recent emergence
2. GitHub repos with high weekly star growth
3. Blog posts from authoritative sources (OpenAI, Anthropic, etc.)
4. Cross-source signals (keyword appears in papers + blogs + GitHub)

## Error Handling

### Insufficient Data
If fewer than 5 papers found:
1. Suggest broadening the topic
2. Suggest increasing the time window
3. Note that trend analysis works better with more data

### API Rate Limits
Same as daily-digest skill.

## Examples

### Example 1: General AI Trends

User: "本周 AI 研究有什么热点？"

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper trend-analysis --topic latest_ai --days 7 --top-k 10
```

### Example 2: Agent-Specific Trends

User: "Agent 方向最近有什么趋势"

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper trend-analysis --topic agents --days 14 --top-k 10
```

### Example 3: With GitHub Language Filter

User: "Python 生态的 AI 项目有什么热门的"

```bash
./.conda/envs/smearglepaper/bin/python -m smearglepaper trend-analysis --topic latest_ai --days 7 --language python
```

## Output Location

- `data/digest/trend-{date}.json` / `data/digest/latest.json` — Full trend analysis data
- `data/digest/papers-{date}.json` / `data/digest/latest.json` — Papers used in analysis
- `data/digest/blogs-{date}.json` / `data/digest/latest.json` — Blogs used in analysis
- `data/digest/github-{date}.json` / `data/digest/latest.json` — GitHub data used in analysis
