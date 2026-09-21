# OpenMuse Harness Event Protocol

Stage 1 introduces one event contract at the boundary between execution and
presentation. Conversation turns emit `HarnessEvent` directly. The durable
Runtime keeps its compatible `RunEvent` record in `runs/<run_id>/events.jsonl`
and exposes `AgentRuntime.harness_events()` to normalize those records.

## Event shape

Every normalized event contains:

```text
event_id / id
schema_version
type
session_id
turn_id
run_id
agent_id
sequence
source
timestamp
payload
```

`sequence` is ordered within the producer stream. A session `EventJournal`
assigns a second, session-wide sequence when conversation and runtime events
are persisted together.

## Projection rules

`HarnessProjection` is a pure reducer. It accepts either a `HarnessEvent`, a
legacy `TurnEvent`, a Runtime `RunEvent`, or a serialized event dictionary.
The TUI applies this reducer for both live events and Runtime replay, so phase,
activity, waiting requests, errors, and terminal status do not have separate
rendering state machines.

The reducer intentionally does not own Runtime persistence, approvals, or
artifact semantics. Those remain in `AgentRuntime`; Projection only describes
what a user should see.

## Stage 2 liveness events

Conversation model streams use the same event contract for provider lifecycle
and activity feedback:

- `provider.connected` records a successful stream connection and a non-secret
  request id when the gateway provides one.
- `provider.event` records lightweight provider activity metadata, never API
  credentials or raw response bodies.
- `provider.first_token` and `model.first_token` record first-token latency.
- `model.delta` carries incremental answer text; `model.completed` carries
  finish reason, usage, first-token latency, and request id.
- `heartbeat` is emitted independently of token arrival, with elapsed time,
  current operation, and whether the operation is stalled.
- `model.stalled` / `tool.stalled` are one-shot warnings for a quiet operation.
- `tool.progress` keeps long-running tools visible and includes the same
  stalled signal.
- `turn.cancelled` includes the cancellation boundary (`model`, `tool`, or
  `tool.completed`) and prevents later tool rounds or assistant persistence.

`HarnessProjection` reduces these events to `operation`, `stalled`,
`first_token_ms`, `usage`, and `request_id`. The TUI renders the projection as
Chinese activity text while detailed provider metadata remains available in
the details view.

## Stage 3 execution contract

All article-writing and conversational model calls now use the same
`ProviderAdapter` interface. OpenAI-compatible and Anthropic adapters translate
requests, fragmented tool arguments, text and reasoning activity, usage, and
finish reasons through a shared SSE transport. Reasoning content is not stored
in the Harness journal; only activity is recorded.

Transient connection, HTTP 408/429, and server failures retry at most twice,
with exponential backoff and bounded numeric Retry-After support.
`provider.retrying` exposes the delay and attempt. Authentication and malformed
requests do not retry. Once text, reasoning or tool arguments have arrived,
automatic replay is disabled to avoid duplicate answers or tool execution.
Truncated streams and malformed/truncated tool arguments fail explicitly.

A Turn owns a `CancellationToken`; child tool scopes and nested Runtime model
calls inherit cancellation and deadlines. Network connect/read and retry waits
are interruptible for the caller. The HTTP reader owns cleanup, uses a bounded
queue, and cannot deliver late callbacks. A connected urllib socket is shut
down on cancellation; a connection attempt without an acquired socket remains
bounded by its configured network timeout.

Cooperative tools call `current_token().check()`, or use the Runtime
`ToolContext.cancellation`. Legacy blocking tools are awaited at a safe
boundary: the UI shows `turn.cancelling` rather than claiming the operation
has stopped. Arbitrary threads and external writes are not force-killed.
No subsequent tool runs and no assistant reply is committed after cancellation.
The Runtime preserves its durable approval and checkpoint ownership.

Incremental answer text is coalesced at roughly 40ms intervals and flushed
before model completion/failure. Event numbering, journaling and delivery are
serialized; no monitor events are emitted after a terminal Turn event.
Tool failure/blocked/cancelled outcomes are not followed by false completion.
Reaching the tool-round limit is a failure, not an empty successful reply.

TUI activity and session status come from `HarnessProjection`. Live and
replayed Runtime details share one renderer. The legacy RunState and
RunPresentation remain compatibility views for artifacts, quality and actions;
they do not generate conversation activity text. New-session/switch-session
actions wait until the current worker has finished safely.

Regression coverage: `tests/test_stage3_harness.py` includes both wire formats,
retry/cancel boundaries, partial output, ordering, batching, safe tool stops,
Runtime cancellation, and a Textual Pilot projection check. Static checks now
include the new Provider, cancellation, Harness and Projection modules rather
than only the Runtime package.

## Stage 4 conversation controller

`ConversationController` is the single online natural-language entry point.
The TUI no longer classifies text by paper/article keywords, candidate numbers,
or the presence of a completed article. Questions about candidates and article
weaknesses go through the same model turn as greetings, new tasks and revisions.
The model may answer without tools, read the current article, or choose a
bounded Runtime operation. The former low-level tool catalogue is not exposed
on this path; the first Pack's controller operations are explicit adapters,
not yet a general downloadable-Pack execution API.

The task context contains the current subject, candidate metadata, selected
paper, article excerpt, version, writing brief/audience/style, quality issues
and pending interaction. It is reconstructed from the manifest/checkpoints
after session restore. Article reading is paginated and read-only; model tools
cannot supply arbitrary local paths, run IDs, approval decisions or quality
scores. Article/source data is explicitly labelled as untrusted prompt data.

The available operations are `start_research`, `select_candidate`,
`write_article`, `revise_article`, `read_article`, `rediscover`,
`preview_article`, and `request_draft`. Each model turn can perform at most one
mutating task operation plus reads/preview. Newly discovered candidates cannot
be selected within the same turn. Existing candidate choices require explicit
user intent; understanding that intent remains a model responsibility, not a
claim of deterministic semantic correctness.

`AgentRuntime.prepare_article()` extends completed paper research with writing,
review and packaging, retaining verified research checkpoints. Revision restarts
at write, preserves previous article artifacts on failure, and keeps upstream
cache keys when the selected writing model changes. Task parameters carry the
user's writing brief, audience and style into the writing pipeline.

Creating a draft remains a separate `publish-existing` run that stops at durable
Runtime approval. No model tool can resolve that approval. A source-run link
preserves access to the article after the publication request; rejecting a draft
does not discard the article. Revisions of legacy real `paper-to-wechat` runs
explicitly switch to dry-run, so an old approval cannot authorize another real
write. Explicit candidate buttons and approval decisions
use controller operations without an extra intent-model call.

Runtime activity keeps the outer tool alive. Conversation completion includes
the task's waiting state so finishing a model reply cannot erase a pending
selection/approval during live rendering or journal replay. The TUI defers
approval dialogs until the turn worker finishes and performs cleanup before
accepting another submission. Input entered while busy is preserved in the
composer, not applied to a running workflow by keyword rules.

Online provider failure never silently switches to keyword routing or a local
example. Missing configuration keeps the user's input and points to `/connect`.
`/offline` explicitly runs a labelled local example with dry-run semantics.
The underlying writing agent's pre-existing fallback/quality rules are unchanged.

Regression coverage in `tests/test_conversation.py` runs scripted provider
decisions through the real Harness and Runtime: discovery, candidate comparison,
selection, article generation, read-only critique, revision, preview and draft
approval. Textual Pilot runs the journey at 80×24, 136×51 and 140×48. These are
offline contract tests, not measurements of a live provider's intent accuracy;
preview launch is mocked here and covered separately by preview-service tests.

## Stage 5 durable turns and bounded context

`TurnStore` checkpoints one active turn beside the session journal; starting a
new turn archives the previous receipt. The versioned, session-scoped snapshot
stores model/reasoning settings, wire messages, current model/tool/answer phase,
round, stable call IDs, task targets and full tool outputs. Atomic replacement
and fsynced tool receipts provide process-interruption recovery. A per-session
lease prevents overlapping execution; POSIX file locks provide cross-process
exclusion on macOS/Linux (Windows cross-process exclusion is not guaranteed).

Tool state is written as `running` **before** invocation and `completed` with
the full result **before** completion events. Recovery reuses completed results;
an unknown in-flight tool requires its recovery adapter and otherwise stops
with `turn.recovery_required`. Controller mutations store a stable operation ID
atomically with the Runtime change, so recovery can find a created task even
if its UI task link was never saved. Revision recovery continues at the saved
version instead of incrementing or repeating upstream research. Candidate
interaction metadata is also checkpointed, preserving the waiting state after
a crash between the step checkpoint and its interaction event.

Runtime external writes have a separate write-ahead outcome marker. Unknown
results, missing completed receipts and cancellation during external writes do
not automatically retry. `run.recovery_required` instructs human remote
verification. `runs reconcile --confirmed` records executed/not-executed;
executed requires a remote ID and not-executed removes stale approval. Neither
reconciliation nor the model authorizes a new write. This is conservative
duplicate prevention, not a remote exactly-once delivery guarantee.

The TUI offers `/resume` and a recovery button. `session.interrupted` projects
an honest non-running state. Incomplete deltas are replayed only as labelled
fragments, not persisted as complete answers. A committed answer missing from
the session transcript is reconciled by turn ID; the memory log also deduplicates
user/assistant commits. Model streams cannot be resumed token-for-token: if no
complete answer was committed, recovery starts another model request.

`ContextBudget` budgets each provider round, including system/task context, tool
schemas, result excerpts and output reserve. `context.compacted` exposes dropped
history and shortened tool results in details. The estimator is conservative
UTF-8 bytes, **not** the selected provider's tokenizer. Configuration defaults
are 32768 context / 4096 output / 1024 safety. Full tool receipts remain durable;
call IDs, arguments, pairing, task context and the latest user request are not
silently discarded. Mandatory input that cannot fit fails before provider I/O.

`ConversationMemory` maintains recent transcript plus a deterministic extractive
summary (early user anchors and recent archived snippets). Quotations are marked
as historical background, never fresh publication authorization. This is bounded,
lossy session continuity, not semantic long-term memory or a claim that every
old constraint is preserved. Active writing constraints come from Runtime.

Journals quarantine only torn final rows and reject corrupted middle rows. The
session journal indexes events to avoid rereading the entire log per delta.
Regression tests in `tests/test_stage5_recovery.py` cover phase-boundary crashes,
a real subprocess exit after a stub external write, revision continuity,
candidate/approval preservation, exclusive recovery, damaged receipts,
per-round budgeting, full-output retention, CLI confirmation and TUI recovery at
80×24, 136×51 and 140×48. Tests use offline fixtures; no real provider or external
publication is invoked.

## Stage 6 consent-gated preference memory

`PreferenceMemory` is a separate, injectable service, not a replacement for the
Stage 5 transcript/summary or Runtime task state. The TUI shares one local SQLite
store across project sessions; clients can isolate stores by path and profiles
by namespace. Records have workspace/Pack scope, category, provenance, version,
expiry and pending/active lifecycle. No legacy editorial defaults or transcripts
are automatically imported as user preferences.

The model exposes only `propose_memory`. Proposals are locally durable but not
retrieved until the user confirms through the TUI. Confirmation uses an
optimistic base version, preventing stale proposals from overwriting newer
edits. Rejection/forgetting removes values while retaining empty idempotency
receipts, so replay cannot resurrect discarded proposals. Recovery refreshes
the preference section from the current store instead of the old turn prompt.
`/memory off` persists an opt-out from retrieval and model proposals without
deleting the user's existing records; `/memory on` re-enables it.

Only matching-scope, nonexpired active records are eligible. Retrieval uses
bounded lexical matching plus explicit writing-task context, not embeddings or
semantic recall. Pack-specific values beat workspace defaults for the same
category. Optional memory uses at most 1800 conservative budget units, further
limited by available prompt space. Preference data is labelled historical and
non-authorizing; current instructions and task constraints outrank it. The
controller passes these defaults to the model, not a keyword workflow router,
and does not deterministically overwrite explicit tool arguments.

Memory lifecycle events share the session journal: `memory.proposed`,
`memory.retrieved`, `memory.confirmed`, `memory.rejected`, `memory.forgotten`,
and `memory.settings_changed`. Events contain identifiers/metadata, not a second
copy of preference text. No memory operation approves publication. Profile
deletion is not transcript deletion or secure erasure of external backups.

Regression coverage: `tests/test_stage6_memory.py`, including inter-session
retrieval, confirmation, scope isolation, expiry, budgets, optimistic conflicts,
rejection/deletion during recovery, opt-out and TUI interaction at 80×24,
136×51 and 140×48. See `docs/MEMORY.md` for user-facing semantics and limits.

## Stage 7 offline acceptance gates

`scripts/evaluate_harness.py` runs a curated acceptance suite grouped into
journey, events, streaming, recovery, memory and presentation gates. Every
required file must contribute tests and every collected test must pass; skips,
xfails, missing collection, teardown errors and process timeouts fail closed.
Subset diagnostics are explicitly labelled and do not replace full acceptance.

The runner isolates test storage, excludes project dotenv and inherited secrets,
disables automatic pytest plugins/filters, blocks Python network requests and
bounds the test process lifetime. Structured reports contain only test outcome
metadata, source fingerprint and execution environment—not raw prompts or
exception bodies. This is an accidental-network-call guard, not an untrusted-code
sandbox. POSIX timeout cleanup includes the process group.

`tests/test_stage7_journeys.py` adds cross-stage scenarios over real Controller,
Harness, Runtime, memory and preview-state code with scripted model decisions.
The scripts test workflow/permission contracts, not semantic intent accuracy,
writing quality or live Provider latency. `tests/test_stage7_evaluation.py`
tests the evaluator itself, including false-green prevention, teardown failures,
timeouts, secret-free reports and the network guard. Existing CI runs the
acceptance command and uploads JSON/Markdown reports after the full unit suite.
See `docs/HARNESS_EVALUATION.md` for commands and explicit non-goals.

## Compatibility

- `smearglepaper.harness.TurnEvent` remains an alias for `HarnessEvent`.
- Existing Runtime `events.jsonl` records remain readable.
- Existing `RunEvent` subscribers still receive `RunEvent` objects.
- New clients can use `AgentRuntime.harness_events()` and
  `harness_events.normalize_event()` without knowing the legacy types.
