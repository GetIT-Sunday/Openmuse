# SmearglePaper

Product roadmap: [PRODUCT_AGENT_PLAN.md](PRODUCT_AGENT_PLAN.md)

Create and manage a durable product task:

```bash
smearglepaper agent "解读这篇论文" --paper-url https://arxiv.org/abs/1706.03762
smearglepaper task-resume --task-id <task-id>
smearglepaper task-retry --task-id <task-id>
smearglepaper task-show --task-id <task-id>
smearglepaper task-approve --task-id <task-id> --gate topic --paper-id <candidate-paper-id>
smearglepaper task-approve --task-id <task-id> --gate publish
smearglepaper task-resume --task-id <task-id> --real-wechat
```

`task-resume --real-wechat` only creates or updates a draft after an explicit
publish approval. It never formally publishes the draft.

After content review passes, ProductAgent automatically prepares local images,
re-reviews the publish-ready article, and writes exact desktop/mobile preview
HTML under `workspace/tasks/<task-id>/preview/`. When local Chrome or Edge can
run headlessly, desktop and mobile PNG screenshots are generated too. Publish
approval is bound to both article and preview fingerprints.

Mobile-first preview:

```bash
smearglepaper task-preview --task-id <task-id>
```

The command starts a local Apple-style review workbench with:

- `/tasks/<task-id>/preview` for the phone-width article preview.
- `/tasks/<task-id>/check` for the publish-readiness checklist.
- `/api/tasks/<task-id>/readiness` for structured status JSON.

The preview service is read-only. After checking the phone preview, approve
publishing from the CLI:

```bash
smearglepaper task-approve --task-id <task-id> --gate publish
```

SmearglePaper turns recent AI papers into structured Chinese article drafts.

It can collect arXiv papers, rank candidates, parse PDFs, extract figure candidates, generate Chinese close-reading articles, render WeChat-friendly HTML, create cover images, prepare WeChat drafts, and expose the workflow as MCP tools.

The name is inspired by Smeargle, the Pokemon known for sketching. This project "sketches" research papers into readable technical articles.

## Features

- Collect recent papers from arXiv.
- Collect recent blog posts from AI engineering RSS/Atom feeds.
- Rank papers by topic relevance, freshness, and AI category signals.
- Download PDFs, parse text, and extract figure candidates with PyMuPDF.
- Generate Chinese paper-reading articles with an OpenAI-compatible model.
- Review generated article quality and improve drafts with an LLM editing pass.
- Fall back to a deterministic local article template when no LLM key is configured.
- Render WeChat-compatible HTML and generate cover images.
- Create or update WeChat Official Account drafts when credentials are configured.
- Run as a CLI or an MCP server.

## Install

### Conda

```bash
conda env create -p ./.conda/envs/smearglepaper -f environment.yml
conda run -p ./.conda/envs/smearglepaper python -m pip install -e ".[dev]"
```

This repository also includes a helper for the project-local Miniforge setup used during development:

```bash
bash scripts/conda-python -m smearglepaper preflight
```

### Pip

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Quick Start

```bash
smearglepaper preflight
smearglepaper draft --topic nlp_semantics_syntax_pragmatics --days 60 --top-k 3 --dry-run
```

Without installing the console script, run:

```bash
python -m smearglepaper preflight
python -m smearglepaper draft --paper-url https://arxiv.org/abs/2401.00001 --dry-run
```

### Paper Writing Agent

After ingesting a paper, run the evidence-first writing Agent:

```bash
python -m smearglepaper.cli writing-agent \
  --mode paper-writing \
  --paper-title "Attention Is All You Need" \
  --paper-url https://arxiv.org/abs/1706.03762 \
  --input-note workspace/paper_writing/inputs/attention_note.md \
  --target-audience "AI方向研究生和算法岗候选人" \
  --style-mode balanced \
  --max-revisions 3 \
  --upload-images
```

The Agent runs NoteDiagnoser, StyleAnalyst, OutlinePlanner, DraftWriter,
TechnicalReviewer, WeChatEditor, RevisionLoop, and PublishPackager. Run artifacts
are stored under `workspace/paper_writing/<run-id>/`; the final compatible article
is also written to `data/articles/<paper-id>.agent.*`.

The run manifest separates `content_ready` from `publish_ready`. Local image paths
block publication until uploaded, without triggering unnecessary LLM rewrites.
Set `LLM_MAX_TOKENS` when longer or shorter generation limits are required.

To resume only the asset-upload stage for an existing content-ready draft:

```bash
python -m smearglepaper.cli prepare-agent-assets --article-json workspace/paper_writing/<run-id>/drafts/draft_v2.json
```

Generated runtime files are written to `data/`:

```text
data/papers/latest.json
data/papers/ranked_latest.json
data/parsed/*.json
data/figures/*
data/articles/*.json
data/articles/*.md
data/articles/*.html
data/covers/*.png
```

## CLI Commands

```bash
smearglepaper topics
smearglepaper check-llm
smearglepaper collect --query "multimodal large language model" --days 7
smearglepaper scout-run --topic agents --days 30 --max-results 50
smearglepaper collect --topic agents --days 30 --max-results 50
smearglepaper collect-blogs --topic agents --days 30 --max-results 30
smearglepaper agent-check
smearglepaper agent-run --topic agents --days 30 --top-k 5
smearglepaper collect --topic nlp_semantics --days 60
smearglepaper collect --topic nlp_syntax --days 60
smearglepaper collect --topic nlp_pragmatics --days 60
smearglepaper rank --top-k 5
smearglepaper read --paper-id 2401.00001
smearglepaper write --paper-id 2401.00001
smearglepaper review-article data/articles/<paper>.md
smearglepaper improve-article data/articles/<paper>.json
smearglepaper draft --topic latest_ai --dry-run
smearglepaper draft --topic nlp_semantics_syntax_pragmatics --days 60 --top-k 3 --dry-run
smearglepaper wechat-publish --article-json data/articles/<paper>.json --draft-only --dry-run
smearglepaper wechat-update-draft --article-json data/articles/<paper>.json --media-id <media-id> --dry-run
```

Built-in NLP topic presets:

- `nlp_semantics`
- `nlp_syntax`
- `nlp_pragmatics`
- `nlp_semantics_syntax_pragmatics`

If arXiv returns `429 Too Many Requests`, wait a few minutes and rerun the same command. SmearglePaper will now show a short rate-limit message instead of a Python traceback.

## MCP Server

```bash
python -m mcp_server.server
```

Available MCP tools:

- `preflight`
- `collect_papers`
- `collect_blogs`
- `scout_run`
- `agent_run`
- `agent_check`
- `rank_latest`
- `read_paper`
- `write_article`
- `review_article`
- `improve_article`
- `create_draft`
- `publish_article`
- `update_draft`

See [docs/MCP.md](docs/MCP.md) for client configuration examples.

## Configuration

Copy `.env.example` to `.env` or export variables in your shell:

```bash
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_API_KEY=...
OPENAI_MODEL=deepseek-chat
WECHAT_APP_ID=...
WECHAT_APP_SECRET=...
```

If no LLM key is configured, SmearglePaper uses a local template so the workflow remains testable offline.

For real WeChat drafts, the workflow uploads the generated cover as `thumb_media_id` and uploads extracted figures as content images before creating or updating the draft.

## Development

```bash
python -m pytest -q
python -m smearglepaper preflight
python -c 'import mcp_server.server; print("mcp import ok")'
```

## License

MIT
