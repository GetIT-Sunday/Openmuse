# WeChat Drafts

SmearglePaper targets this production boundary:

```text
generate article -> create or update WeChat draft -> publish manually in WeChat backend
```

## Real Draft Creation

Set:

```bash
WECHAT_APP_ID=...
WECHAT_APP_SECRET=...
```

Then run without `--dry-run`:

```bash
smearglepaper run paper-to-wechat --query "LLM reasoning" --days 30
```

## Update Existing Draft

```bash
smearglepaper update-wechat-draft --article-json data/articles/<paper>.json --media-id <media-id>
```

## Notes

- Use dry-run first.
- Generated files live under `data/`.
- Do not commit WeChat credentials or `.env`.
