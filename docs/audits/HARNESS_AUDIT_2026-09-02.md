# AutoWechat Harness Audit

Date: 2026-09-02

## Verdict

The current product has a useful durable workflow runtime, but it is not yet a conversational agent harness. The TUI presents a chat surface while the main path maps text to a fixed workflow and waits for blocking model calls. This mismatch causes the four reported symptoms: weak role identity, no streaming, poor liveness feedback, and ineffective memory.

Do not solve these as four isolated UI patches. Preserve the durable workflow engine, and add a turn-oriented conversation harness above it.

## Evidence

1. Initial state: `harness-screenshots/png/01-initial.svg.png`
2. Running state: `harness-screenshots/png/02-running.svg.png`
3. Completed state: `harness-screenshots/png/03-complete.svg.png`

The screenshots are Textual-rendered states captured at 136x42. Quick Look conversion can differ from a real terminal font, so CJK overlap must also be verified in Terminal/iTerm2/WezTerm before treating it as renderer-independent.

## Critical Findings

### P0: The TUI does not use the agent loop

- `AgentConsole._handle_user_input` routes ordinary input to `_run_agent`.
- `_runtime_request` classifies intent with keyword checks and creates one fixed workflow.
- `agentic_loop` and `chat_with_tools` exist, but production code does not call them; only unit tests exercise them.
- Session history is persisted for replay, but it is not sent to the model in the TUI workflow path.

Impact: the interface promises a conversation, but the model neither sees nor reasons over the conversation. Follow-up understanding is implemented as special cases such as article revision and candidate selection.

### P0: All model transports are blocking

- OpenAI-compatible calls perform one `urlopen(...).read()` and parse the final JSON.
- Anthropic calls do the same.
- No request sets `stream: true`; there is no SSE parser, delta event, incremental message buffer, or first-token event.
- Writing calls are nested inside a single runtime `write` step, so model output cannot reach the TUI until the step completes.

Impact: there is no true streaming output and no reliable evidence that the model is still responding.

### P0: Progress is too coarse to express real activity

- Runtime events cover step start and completion, not internal model stages.
- A single `write` step may perform planning, drafting, multiple reviews, and several revisions.
- The TUI refreshes when events arrive; it has no heartbeat timer, elapsed-time display, last-activity timestamp, or stalled-call state.
- Cancellation is cooperative only between workflow steps. A blocked network/model call must finish or time out first.

Impact: `1/5` can remain unchanged for most of the run. The user cannot distinguish slow generation, a retry, a frozen connection, or a dead process.

### P0: Conversation identity is visually and structurally weak

- User and assistant messages are both rendered as one-line entries in the same `RichLog` timeline.
- Identity is encoded by a small inline label (`你` or `AutoWechat`) rather than stable message blocks, alignment, spacing, or avatar/initial columns.
- System, workflow, and assistant text share the same stream, so product status can be mistaken for model speech.
- Captured states show CJK text collisions in the current rendered output.

Impact: users need to parse timestamps and labels to understand who said what, and model speech is not clearly separated from deterministic system status.

### P1: Memory exists, but it is not conversational memory

- `MemoryManager` stores static source/style profiles and an append-only article history.
- Source and style profiles influence article generation, but dialogue-derived preferences are not extracted or retrieved.
- `user_style_profile.json` has no observed production reader in the writing path.
- There is no per-user memory namespace, episodic summary, semantic retrieval, relevance score, provenance, expiry, or user-facing edit/forget control.
- TUI sessions and Runtime `session.json` are separate stores; the Runtime session file is initialized with an empty message list and is not subsequently updated.

Impact: history is storage, not memory. A returning user cannot rely on AutoWechat remembering audience, tone, preferred depth, rejected topics, or prior corrections.

### P1: Harness responsibilities are fragmented

- TUI owns conversational special cases and session replay.
- Runtime owns durable DAG execution, approvals, checkpoints, and artifacts.
- `PaperWritingAgent` owns a second internal stage manifest and model retry/fallback behavior.
- `llm.py` owns two separate blocking model APIs plus an unused tool loop.

Impact: there is no single turn lifecycle, event schema, context builder, model adapter, or cancellation boundary. Adding streaming directly in the TUI would deepen this fragmentation.

### P1: The isolated tool loop is not production-grade

- It has only a maximum round count.
- It lacks streaming, usage reporting, retry/backoff, context budgeting, tool-output truncation policy, approval integration, durable checkpoints, and explicit terminal reasons when max rounds are exhausted.
- Tool calls are executed synchronously and errors are returned as ordinary strings.

Impact: wiring the existing loop directly into the TUI would not provide a robust harness.

## Target Harness V2

```text
Textual UI
  <- structured TurnEvent stream
ConversationController
  |- ContextBuilder (recent turns + task state + retrieved memory)
  |- AgentLoop (model/tool/approval policy)
  |- DurableWorkflowTool (existing AgentRuntime)
  |- MemoryService (profile + episodic + task memory)
  `- ModelGateway (OpenAI/Anthropic streaming adapters)
```

The current `AgentRuntime` should remain the durable executor. It becomes a long-running tool used by the conversation controller rather than pretending to be the conversation itself.

## Required Event Contract

Use one ordered event stream for persistence and rendering:

- `turn.started`
- `context.prepared`
- `model.started`
- `model.delta`
- `model.completed`
- `tool.started`
- `tool.progress`
- `tool.completed`
- `workflow.phase_changed`
- `heartbeat`
- `approval.required`
- `memory.proposed`
- `turn.completed`
- `turn.failed`
- `turn.cancelled`

Every event needs `event_id`, `sequence`, `session_id`, `turn_id`, timestamp, source, status, and a typed payload. The TUI should be a projection of these events, not an independent message state machine.

## Recommended Delivery Order

### Phase 1: Trust and liveness

1. Introduce structured message blocks for user, AutoWechat, and system activity.
2. Add `turn_id` and the unified event contract.
3. Add elapsed time, last activity, animated heartbeat, current operation, and stalled-state thresholds.
4. Surface internal writing stages as user-facing progress without exposing implementation names.

Acceptance: the user can always identify the speaker and tell whether work is active, waiting, retrying, stalled, or finished.

### Phase 2: True streaming

1. Add streaming adapters for OpenAI-compatible SSE and Anthropic events.
2. Send deltas through a thread-safe queue to one mutable assistant message.
3. Record first-token latency, token usage, finish reason, retry count, and provider request ID.
4. Define cancellation behavior for the active HTTP stream.

Acceptance: the first visible text arrives before completion, output grows in place, and cancellation closes the stream.

### Phase 3: Real conversation harness

1. Build `ConversationController` around a durable turn lifecycle.
2. Use the model for intent/tool decisions; expose the existing workflows as bounded tools.
3. Keep publishing approvals and other side effects in the Runtime policy boundary.
4. Remove keyword routing from the primary path, retaining it only as an offline fallback.

Acceptance: follow-up requests use prior turns naturally, while deterministic workflows retain resumability and safety.

### Phase 4: Useful memory

1. Short-term memory: recent transcript plus a rolling conversation summary.
2. Task memory: selected paper, article version, open issues, approvals, and generated artifacts.
3. Long-term profile: audience, tone, depth, recurring topics, and explicit user corrections.
4. Retrieve only relevant memories with provenance and a strict token budget.
5. Add `/memory`, `/forget`, and confirmation before persisting inferred preferences.

Acceptance: preferences survive a new session, can be inspected and deleted, and irrelevant old content does not leak into prompts.

### Phase 5: Reliability and evaluation

1. Retry/backoff and provider error normalization.
2. Context-window budgeting and tool-output compaction.
3. Crash recovery in the middle of model/tool turns.
4. Golden conversation tests, streaming parser tests, hung-call tests, memory retrieval tests, and terminal screenshot tests.

## What To Keep

- Durable manifests, checkpoints, artifact integrity, resume, retry, cancellation intent, and approval semantics.
- Candidate-selection interaction state.
- Deterministic `RunPresentation` as a workflow projection.
- Local mobile preview and versioned article artifacts.

## What To Replace Or Reframe

- Replace inline `RichLog` chat rendering with structured message widgets.
- Replace blocking provider functions with a shared streaming gateway.
- Replace keyword routing as the main conversation brain.
- Reframe `MemoryManager` as legacy editorial defaults; build a separate explicit conversation memory service.
- Consolidate TUI session messages and Runtime session events into one authoritative conversation store.

## Audit Limits

- No live provider request was made, so provider-specific SSE quirks and cancellation behavior remain unverified.
- Screenshots were generated by Textual and converted by macOS Quick Look; real-terminal CJK rendering needs separate verification.
- This audit did not test screen readers or claim WCAG compliance. Keyboard focus, live-region equivalents, and non-color status cues require implementation-level testing.
