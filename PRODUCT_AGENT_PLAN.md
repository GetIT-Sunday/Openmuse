# SmearglePaper Product Agent Execution Plan

> Current optimization review: [PRODUCT_OPTIMIZATION_REVIEW.md](PRODUCT_OPTIMIZATION_REVIEW.md).
> Reliability, approval safety, and preview integrity now take priority over
> scheduling and additional ranking features.

## 1. Product Goal

Turn the existing paper writing pipeline into a user-facing Paper Content Agent:

```text
User request
  -> understand intent and create a task
  -> discover or open papers
  -> rank, deduplicate, and recommend candidates
  -> wait for topic confirmation
  -> research, write, review, revise, and prepare assets
  -> show a WeChat-style preview
  -> wait for publishing confirmation
  -> create or update a WeChat draft
  -> record results and learn from feedback
```

The default automation boundary is **creating a WeChat draft**. Formal publishing remains a separate explicit action.

## 2. Current Baseline

The following capabilities already exist and must be reused:

| Capability | Current implementation | Status |
|---|---|---|
| arXiv collection and ranking | `collector.py`, `ranker.py`, `workflow.py` | Available |
| Blog and GitHub collection | `blogs.py`, `github_tracker.py` | Available |
| PDF download, parsing, figures, evidence | `reader.py`, `figures.py`, `evidence.py` | Available |
| Paper writing and revision loop | `writing_agent.py`, `agent_reviews.py` | Available |
| Content and publish quality gates | `content_ready`, `publish_ready` | Available |
| WeChat image upload | `WechatClient.upload_markdown_images()` | Available |
| WeChat draft create and update | `WechatClient.create_draft()`, `update_draft()` | Available |
| CLI, MCP, and TUI surfaces | `cli.py`, `src/mcp_server/server.py`, `tui.py` | Partially available |
| Persisted writing manifest | `workspace/paper_writing/<run-id>/manifest.json` | Available |

The missing product layer is a durable task orchestrator above these capabilities.

## 3. Product Principles

1. Users describe goals; they do not manually chain CLI commands.
2. The Agent may act autonomously between approval gates.
3. Topic selection and external publishing actions are explicit and auditable.
4. Every task can resume after interruption from its last completed stage.
5. Re-running a task must not create duplicate drafts or repeatedly upload identical images.
6. Recommendations explain why a paper is suitable, not only its numerical score.
7. New papers and influential papers use different ranking logic because new papers rarely have meaningful citation counts.

## 4. Supported Intents

The intent router initially supports four task types:

| Intent | Example | First action |
|---|---|---|
| `explain_paper` | "解读这篇 arXiv 论文" | Resolve and inspect the supplied paper |
| `explore_topic` | "找近期值得讲的 Agent Memory 论文" | Search and rank candidates |
| `scheduled_topic` | "每周找两篇多模态论文生成草稿" | Create a recurring task definition |
| `resume_task` | "继续上次的文章" | Load task state and continue |

Ambiguous requests are converted into a draft task plan and shown to the user before expensive execution.

## 5. Unified Task State Machine

### 5.1 States

```text
created
  -> planning
  -> discovering
  -> awaiting_topic_approval
  -> researching
  -> writing
  -> reviewing
  -> preparing_assets
  -> awaiting_publish_approval
  -> creating_draft
  -> draft_created
  -> published
```

Exceptional states:

```text
needs_attention
failed
cancelled
```

### 5.2 Approval Gates

| Gate | Default | User can disable |
|---|---|---|
| Topic approval | Required for topic discovery | Yes, in managed mode |
| Writing direction approval | Optional | Yes |
| WeChat draft approval | Required | Yes, but only by explicit configuration |
| Formal publish approval | Always required | No |

### 5.3 Task Manifest

Store each task at:

```text
workspace/tasks/<task-id>/manifest.json
```

Minimum schema:

```json
{
  "schema_version": 1,
  "task_id": "task-...",
  "intent": "explore_topic",
  "status": "awaiting_topic_approval",
  "active_stage": "candidate-ranking",
  "request": {},
  "plan": {},
  "candidates": [],
  "selected_paper": null,
  "approvals": [],
  "artifacts": {},
  "writing_run_id": null,
  "wechat": {
    "media_id": null,
    "publish_id": null
  },
  "metrics": {
    "started_at": "",
    "updated_at": "",
    "elapsed_seconds": 0,
    "llm_calls": 0,
    "tokens": 0
  },
  "error": null
}
```

All state transitions must be persisted before the next external or expensive operation begins.

## 6. Candidate Discovery and Ranking

### 6.1 Sources

Initial sources:

- arXiv papers
- existing blog feeds
- GitHub repositories associated with papers
- local publication history

Later sources:

- Semantic Scholar or OpenAlex citations
- Papers with Code
- author/project pages
- community discussions

### 6.2 Filtering

Before ranking:

1. Normalize arXiv IDs, DOI, title, and canonical URL.
2. Remove papers already drafted or published unless explicitly requested.
3. Remove withdrawn, inaccessible, or clearly irrelevant papers.
4. Group revisions and duplicate listings into one candidate.

### 6.3 Ranking Profiles

Do not use a single fixed ranking formula.

| Profile | Main weights |
|---|---|
| `frontier` | freshness, relevance, novelty, community momentum |
| `classic` | citations, long-term influence, teaching value |
| `engineering` | code availability, reproducibility, practical value |
| `balanced` | relevance, explainability, impact, freshness |

Each candidate must include:

- one-sentence contribution
- recommendation reason
- evidence and source links
- freshness and impact signals
- code/project availability
- suitability for a WeChat explanation
- duplicate/publication status
- score breakdown and uncertainty

## 7. Agent Architecture

Add a product orchestration layer without replacing the writing implementation:

```text
ProductAgent
  -> IntentRouter
  -> TaskPlanner
  -> DiscoveryAgent
  -> CandidateRanker
  -> ApprovalManager
  -> ResearchAssembler
  -> PaperWritingAgent
  -> PreviewBuilder
  -> DraftPublisher
  -> HistoryManager
```

Responsibilities:

| Component | Responsibility |
|---|---|
| `ProductAgent` | Own task lifecycle and state transitions |
| `IntentRouter` | Convert user input into a structured intent |
| `TaskPlanner` | Decide required stages and approval gates |
| `DiscoveryAgent` | Search papers, blogs, GitHub, and local history |
| `CandidateRanker` | Filter, deduplicate, score, and explain candidates |
| `ApprovalManager` | Record approvals and reject stale approvals |
| `ResearchAssembler` | Build a source/evidence package for the selected paper |
| `PaperWritingAgent` | Reuse the existing writing and review loop |
| `PreviewBuilder` | Produce a mobile/WeChat-style preview artifact |
| `DraftPublisher` | Idempotently create or update a WeChat draft |
| `HistoryManager` | Record drafted, published, and rejected topics |

## 8. Implementation Phases

### Phase 0: Align Documentation and Contracts

Goal: establish a trustworthy implementation baseline.

Work:

1. Update architecture and product status documents to reflect current capabilities.
2. Define task, candidate, approval, and draft-result schemas.
3. Define state transition rules and failure behavior.
4. Add a migration/version field to new manifests.

Acceptance:

- A single document describes the real current workflow.
- Task state and transition contracts have unit tests.
- Existing writing and publishing commands remain compatible.

### Phase 1: Durable Product Task Orchestrator

Goal: create, inspect, approve, and resume a task.

Work:

1. Add `product_agent.py` with persisted state transitions.
2. Add task storage and task index.
3. Wrap the existing `PaperWritingAgent` as a task stage.
4. Add CLI commands:

```text
smearglepaper agent "<natural language request>"
smearglepaper tasks
smearglepaper task-show --task-id <id>
smearglepaper task-approve --task-id <id> --gate <gate>
smearglepaper task-resume --task-id <id>
```

5. Record progress, elapsed time, errors, and next required user action.

Acceptance:

- A task interrupted during writing resumes without repeating completed stages.
- A failed task reports the failing stage and can be resumed.
- Existing `writing-agent` behavior remains unchanged.

### Phase 2: Discovery, Deduplication, and Topic Approval

Goal: turn a research direction into explainable paper candidates.

Work:

1. Implement structured intent routing for paper URL and topic requests.
2. Combine arXiv, blog, GitHub, and history data into candidate records.
3. Add canonical paper identity and published-history deduplication.
4. Implement ranking profiles and score explanations.
5. Persist 1-3 recommended candidates.
6. Stop at `awaiting_topic_approval`.

Acceptance:

- A topic request produces candidates with recommendation reasons.
- Already published papers are excluded by default.
- User selection resumes the same task with the chosen paper.
- New papers are not unfairly penalized for having zero citations.

### Phase 3: End-to-End Writing and Preview Approval

Goal: run the selected paper through the existing content workflow and show a realistic preview.

Work:

1. Assemble paper, project, blog, and repository evidence.
2. Start and link a `PaperWritingAgent` run.
3. Upload and replace images only after `content_ready`.
4. Produce desktop and mobile preview artifacts.
5. Add revision feedback input and re-review manually edited content.
6. Stop at `awaiting_publish_approval`.

Acceptance:

- The task links all writing artifacts and review reports.
- Preview uses the same HTML/assets that will be sent to WeChat.
- Local image paths prevent publish approval.
- Manual edits invalidate old approval and trigger re-review.

### Phase 4: Idempotent WeChat Draft Creation

Goal: make one approval reliably produce one WeChat draft.

Work:

1. Add image upload cache keyed by file hash.
2. Add draft registry keyed by task and paper identity.
3. On first approval, create a draft and save `media_id`.
4. On later runs, update the existing draft instead of creating duplicates.
5. Save WeChat responses and actionable error messages.
6. Keep formal publication as a separate explicit action.

Acceptance:

- Re-running draft creation does not create duplicate drafts.
- Existing WeChat image URLs are not uploaded again.
- `media_id` is stored in the task manifest and publication history.
- Network/API failure leaves the task resumable at `creating_draft`.

### Phase 5: Scheduled Research Tasks

Goal: periodically discover and prepare articles for configured directions.

Work:

1. Add schedule definitions with topic, frequency, ranking profile, and article quota.
2. Run discovery on schedule and create child tasks.
3. Respect publication history and candidate cooldown.
4. Notify the user when approval is required.
5. Support pause, resume, and missed-run recovery.

Acceptance:

- Scheduled runs do not duplicate the same paper.
- The default stops at topic or draft approval.
- Every scheduled execution has an auditable task record.

### Phase 6: User Experience and Learning Loop

Goal: make CLI, MCP, and TUI expose the same real task lifecycle.

Work:

1. Replace TUI mock execution with ProductAgent calls.
2. Expose task and approval operations through MCP.
3. Add task list, candidate cards, progress, preview, and approval actions.
4. Record publication results and user edits.
5. Use history to tune ranking and writing preferences.

Acceptance:

- CLI, MCP, and TUI show consistent task state.
- Users can complete the normal flow without knowing internal commands.
- Product metrics identify where tasks fail or await action.

## 9. Reliability and Safety Requirements

1. External side effects use idempotency keys where possible.
2. WeChat draft creation requires a valid, current approval record.
3. Formal publishing is never triggered by a vague natural-language request.
4. Failed LLM calls preserve the last valid artifact.
5. Source URLs and evidence are retained for factual review.
6. Citation, popularity, and ranking signals show their source and timestamp.
7. Credentials never appear in manifests, logs, prompts, or artifacts.

## 10. Test Strategy

### Unit Tests

- intent classification and structured extraction
- state transitions and invalid transitions
- candidate identity, deduplication, and ranking profiles
- approval creation, expiry, and invalidation
- image cache and draft idempotency

### Integration Tests

- paper URL -> writing -> preview approval
- topic -> candidates -> topic approval -> writing
- interrupted task -> resume
- publish-ready article -> create draft -> update same draft
- scheduled task -> candidate generation without duplicates

### Real Smoke Tests

- real arXiv download and parse
- real configured LLM writing run
- real WeChat image upload
- real WeChat draft creation after explicit approval

Real smoke tests must be opt-in and clearly mark external side effects.

## 11. Delivery Order

Implement in this order:

1. Phase 0: contracts and documentation
2. Phase 1: durable task orchestrator
3. Phase 4 subset: idempotent draft creation for existing `publish_ready` articles
4. Phase 2: discovery and topic approval
5. Phase 3: linked writing flow and preview approval
6. Phase 5: schedules
7. Phase 6: TUI/MCP and learning loop

The Phase 4 subset is pulled forward because it closes the currently missing final mile with low architectural risk.

## 12. First Engineering Iteration

The first implementation iteration should deliver:

1. Versioned `ProductTask` schema and transition validator.
2. File-backed task repository and task index.
3. `ProductAgent` support for a supplied paper URL or paper ID.
4. Resume from persisted task state.
5. `awaiting_publish_approval` gate.
6. Idempotent create/update WeChat draft action.
7. CLI commands for task creation, inspection, approval, and resume.
8. Unit and dry-run integration tests.

This iteration intentionally does not implement scheduling or broad web discovery. It creates the stable product backbone those features require.
