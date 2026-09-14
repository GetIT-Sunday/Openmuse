# Durable Runtime

SmearglePaper 0.2 uses one deterministic Runtime for the CLI, TUI, and MCP server. LLM calls create content; they do not decide state transitions, approvals, retries, or publication side effects.

## Workspace

Runtime state is stored under `~/.local/share/smearglepaper` by default. Resolution order is:

1. `--workspace`
2. `SMEARGLEPAPER_HOME`
3. `workspace` in `~/.config/smearglepaper/config.toml`
4. the default above

Each run owns this layout:

```text
runs/<run_id>/
  manifest.json
  events.jsonl
  session.json
  artifacts/
  logs/
  checkpoints/
```

`manifest.json` is the current state. `events.jsonl` is an append-only ordered audit stream. Checkpoints and artifacts are SHA-256 verified before a completed step is reused.

## State and Events

Run states are `pending`, `running`, `waiting_input`, `waiting_approval`, `cancelling`, `cancelled`, `failed`, and `completed`. Step states are `pending`, `running`, `retrying`, `waiting_input`, `waiting_approval`, `skipped`, `failed`, and `completed`.

Events contain `id`, `run_id`, `session_id`, `agent_id`, `type`, `sequence`, `timestamp`, and `payload`. Sequences are strictly increasing within a run. Credentials and token-like payload fields are redacted before persistence.

Topic-based paper workflows pause after ranking with an `interaction.required` event and a persisted candidate list. Resolve the selection and resume from the CLI with:

```bash
smearglepaper runs select RUN_ID PAPER_ID
```

Direct paper URLs and paper IDs skip this interaction. User selection is separate from publication approval and never authorizes an external write.

## Safety

External writes are dry-run by default. A real `paper-to-wechat` run stops at `waiting_approval`; approve the exact operation with:

```bash
smearglepaper approve RUN_ID approve-publish
```

Cancellation is cooperative. The current network or model call completes or times out, then no new step starts. Resume checks the input hash and every Artifact hash before using cached output.

## Provider Contracts

The runtime exposes `SourceProvider`, `LLMProvider`, `Publisher`, and `Notifier` protocols. Built-in providers remain in the main package for 0.2. External Python entry-point plugins are intentionally deferred until the contracts have production feedback.
