# Configuration

SmearglePaper works without secrets in dry-run mode. Real LLM writing and WeChat draft creation require environment variables.

## LLM

```bash
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_API_KEY=...
OPENAI_MODEL=deepseek-chat
LLM_TIMEOUT_SECONDS=60
LLM_USER_AGENT=
```

The LLM endpoint must be OpenAI-compatible and support `/v1/chat/completions`.
Provider calls wait at most 60 seconds by default. If a paper-writing call times
out, SmearglePaper preserves the evidence and generates a deterministic local
draft marked for human review. This fallback never bypasses the WeChat quality
gate. `LLM_TIMEOUT_SECONDS` accepts values from 5 to 300 seconds.
OpenAI-compatible calls send a browser-compatible User-Agent by default so
gateways behind Cloudflare or similar bot filters can accept the request. Set
`LLM_USER_AGENT` only when a gateway requires a specific value.

## WeChat

```bash
WECHAT_APP_ID=...
WECHAT_APP_SECRET=...
```

Real WeChat draft creation uploads the generated cover as `thumb_media_id` and uploads extracted figures as content images.

## Dry Run

Runtime workflows are dry-run by default. You can also state it explicitly:

```bash
smearglepaper run paper-to-wechat --query "LLM reasoning" --dry-run
```

For NLP semantics, syntax, and pragmatics discovery:

```bash
smearglepaper run paper-to-wechat --topic nlp_semantics_syntax_pragmatics --days 60 --top-k 3 --dry-run
```
