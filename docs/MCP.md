# MCP

## Durable Runtime tools (0.2)

CLI, TUI, and MCP now share the same durable Runtime. Long-running MCP work starts asynchronously and returns a `run_id`; clients can reconnect and inspect ordered events later.

| Tool | Purpose |
|---|---|
| `run_workflow` | Start `paper-research`, `paper-to-article`, `paper-to-wechat`, or `daily-digest` |
| `get_run` | Read the manifest and optionally the ordered event stream |
| `list_runs` | List durable runs in a workspace |
| `resume_run` | Resume from hash-verified checkpoints |
| `cancel_run` | Request cooperative cancellation |
| `resolve_approval` | Approve or reject a real external write |
| `list_artifacts` | List run-owned Artifacts with hashes and provenance |

Real WeChat writes always stop at `waiting_approval`; setting `real=true` does not bypass the approval tool. Existing business-specific MCP tools remain available as compatibility adapters.

SmearglePaper exposes its paper-to-draft workflow as an MCP server.

## Run Manually

```bash
python -m mcp_server.server
```

When using the project-local conda environment:

```bash
bash scripts/conda-python -m mcp_server.server
```

## Client Configuration

Use the Python executable inside your environment. Example:

```json
{
  "mcpServers": {
    "smearglepaper": {
      "command": "/absolute/path/to/smearglepaper/.conda/envs/smearglepaper/bin/python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/smearglepaper"
    }
  }
}
```

## Tools

- `preflight`: Check dependencies, Python version, LLM settings, and WeChat settings.
- `collect_papers`: Collect recent arXiv papers and save `data/papers/latest.json`.
- `rank_latest`: Rank papers from the latest local collection.
- `read_paper`: Download and parse a paper from local paper metadata.
- `write_article`: Generate Markdown, HTML, cover, and article JSON for one paper.
- `create_draft`: Run collect/rank/read/write/draft workflow.
- `publish_article`: Create or publish a WeChat draft from an article JSON.
- `update_draft`: Update an existing WeChat draft.
