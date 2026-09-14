# CLI Reference

Run `smearglepaper` without arguments to open the interactive Agent console.

```bash
smearglepaper run paper-research --topic agents
smearglepaper run paper-research --offline-example --workspace ./demo-workspace
smearglepaper run paper-to-article --paper-url https://arxiv.org/abs/1706.03762
smearglepaper run paper-to-wechat --paper-url URL --real
smearglepaper agent "解读这篇论文并生成公众号草稿" --paper-url URL
smearglepaper runs list --json
smearglepaper runs show RUN_ID --events --json
smearglepaper runs resume RUN_ID
smearglepaper runs retry RUN_ID --step review
smearglepaper runs cancel RUN_ID
smearglepaper artifacts list RUN_ID --json
smearglepaper config check --json
```

New automation commands emit their machine result on stdout. Live progress is sent to stderr unless `--json` is used. Exit codes are: `0` success, `2` invalid arguments, `3` invalid configuration, `4` external service failure, `5` workflow failure, `6` quality gate failure, `7` waiting for approval, and `130` cancellation.

The original atomic commands remain available in 0.2. The previous natural-language ProductTask command moved from `agent` to `product-agent`.
