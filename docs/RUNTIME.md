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

Run states are `pending`, `running`, `waiting_input`, `waiting_approval`, `cancelling`, `cancelled`, `failed`, `recovery_required`, and `completed`. Step states are `pending`, `running`, `retrying`, `waiting_input`, `waiting_approval`, `skipped`, `failed`, and `completed`.

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

## Interrupted conversations and external writes

Reopen a TUI session and choose **继续未完成的对话**, or enter `/resume`.
The current turn and tool receipts are saved beside the session event journal.
Completed tool results are reused; an interrupted model answer may be generated
again and therefore incur another model call. Restored partial text is labelled
as incomplete and is not committed as a finished assistant message.

Local Runtime work resumes from verified checkpoints. Pending paper selection
and draft approval remain pending; recovery does not choose or approve for you.
An interrupted revision retains its version number and does not repeat research.

An external operation is recorded **before** its handler is called. If the
process exits, times out or is cancelled without a reliable completion receipt,
the run stops at `recovery_required` (CLI exit code 9). Do not assume that a
timeout means the draft was not created. Inspect the remote draft box/service,
then record the actual outcome:

```bash
# Only after verifying the draft exists remotely:
smearglepaper runs reconcile RUN_ID --executed --external-id REMOTE_ID --confirmed

# Only after verifying that no operation took place remotely:
smearglepaper runs reconcile RUN_ID --not-executed --confirmed
```

Both commands only record human verification; neither executes nor publishes.
Then explicitly use `/resume` in the session, or `runs resume RUN_ID` for a
Runtime-only task. `--not-executed` discards the old approval and requires fresh
approval before a real draft write. An already completed external operation
cannot be repeated through `runs retry`; use a new, explicitly approved task.
These controls reduce duplicate writes; they are not an exactly-once guarantee
from the external provider.

Interrupted final JSONL rows are quarantined before the next append. Corrupted
middle rows or invalid turn receipts stop recovery rather than silently losing
state. Same-session/process recovery is leased; cross-process exclusion uses
POSIX file locks on macOS/Linux and is not guaranteed on Windows.

Conversation context uses `OPENMUSE_CONTEXT_TOKENS` (default 32768) and
`OPENMUSE_OUTPUT_TOKENS` (default 4096), with 1024 units of safety reserve.
The estimate is conservative UTF-8 byte accounting, not an exact tokenizer or
billing measurement. Set it to the selected model's supported window. Old
transcript entries become a bounded, lossy extractive summary; current task
state is rebuilt from Runtime and the latest user request is not truncated.
Full tool receipts stay on disk even if their prompt excerpts are shortened.
Oversized mandatory input fails before a provider request. This is session
continuity, not cross-session semantic/long-term memory.

## Provider Contracts

The runtime exposes `SourceProvider`, `LLMProvider`, `Publisher`, and `Notifier` protocols. Built-in providers remain in the main package for 0.2. External Python entry-point plugins are intentionally deferred until the contracts have production feedback.
