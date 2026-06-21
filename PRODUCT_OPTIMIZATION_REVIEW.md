# Product Agent Optimization Review

Date: 2026-06-15

Status: local review only; do not push until the reliability fixes are implemented and verified.

## Implementation Status

Iteration A has now been implemented locally:

- atomic JSON writes
- optimistic task revision checks
- article and asset bundle fingerprints
- publish approval invalidation after content changes
- imported article review gate
- separated dry-run and real WeChat task state
- separated dry-run and real WeChat result files
- explicit retry routing for failed and needs-attention tasks

Still pending:

- true stage-level resume inside `PaperWritingAgent`
- crash-safe WeChat operation journal

Iteration B has now been implemented locally:

- automatic `preparing_assets` stage after content review
- post-upload re-review before publish approval
- exact final HTML desktop/mobile preview bundle
- optional Chrome/Edge headless preview screenshots
- publish approval bound to both article and preview fingerprints

## 1. Executive Decision

The project now has the shape of the intended product:

```text
request -> task -> candidate discovery -> topic approval -> writing -> publish approval -> WeChat draft
```

However, the current implementation is not yet safe enough to call this a reliable closed loop.

The next engineering cycle should prioritize:

1. truthful resume behavior
2. approval invalidation and publication safety
3. automatic asset preparation and real preview
4. draft operation idempotency
5. trustworthy discovery signals

Scheduling, TUI polish, and more ranking profiles should wait until these foundations are complete.

## 2. P0 Reliability Gaps

### P0.1 Writing Resume Is Not Yet a Real Resume

Current behavior:

- `ProductAgent` persists the task as `writing`.
- Resuming a `writing` or `reviewing` task calls `run_writing_agent()` again.
- `PaperWritingAgent` creates a new run ID and starts the writing flow from the beginning.
- A failed or `needs_attention` task has no implemented retry route in `ProductAgent.resume()`.

User impact:

- expensive LLM work may repeat
- a second article version may silently replace the expected result
- "resume" currently means "run again" for the most expensive stage

Required fix:

- add resumable stage execution to `PaperWritingAgent`
- persist stage inputs, outputs, and fingerprints
- reuse valid completed artifacts
- add explicit `retry_from` and `restart` operations
- route `failed` and `needs_attention` tasks to a valid recovery action

Acceptance:

- interrupt after outline, draft, review, or asset upload
- resume without repeating completed valid stages
- user can intentionally restart when desired

### P0.2 Publish Approval Is Bound Only to a File Path

Current behavior:

- publish approval records `final_article_json` as a path string
- if the file is edited in place after approval, the approval remains valid
- imported articles can enter `awaiting_publish_approval` without a fresh quality check

User impact:

- an unreviewed or modified article can be sent to WeChat under an old approval

Required fix:

- compute a SHA-256 fingerprint of the approved article JSON, Markdown, HTML, and assets
- approval records bind to the fingerprint, not only the path
- any edit invalidates publish approval
- imported articles must pass technical, WeChat, and local-image checks before approval

Acceptance:

- editing the article after approval blocks draft creation
- task returns to `reviewing`
- approval history records why the old approval became stale

### P0.3 Dry-Run Draft State Can Pollute Real Draft State

Current behavior:

- dry-run draft creation returns `dry_run_media_id`
- `ProductAgent` stores it as the task's `media_id`
- a later real operation may try to update this fake draft ID

User impact:

- real draft creation can fail or take the wrong update path

Required fix:

- store dry-run results separately from real WeChat state
- never persist a dry-run media ID as a real draft identifier
- record `environment: dry_run | real`

Acceptance:

- dry-run followed by real creation always calls real `create_draft`
- only real API responses populate `wechat.media_id`

### P0.4 Draft Creation Is Not Crash-Safe

Current behavior:

- create/update decision depends on task-local `media_id`
- if WeChat creates the draft successfully but the process crashes before saving the task, resume may create a duplicate

Required fix:

- create a draft operation journal before the API call
- maintain a draft registry keyed by canonical paper ID and approved article fingerprint
- persist operation state: `prepared`, `submitted`, `confirmed`, `unknown`
- when state is `unknown`, require reconciliation before retrying create

Acceptance:

- a crash immediately after the API response does not silently create a second draft
- ambiguous external state is surfaced as `needs_attention`

### P0.5 Task Persistence Is Not Atomic

Current behavior:

- JSON manifests and the task index are written directly
- concurrent task or scheduled writes can leave partial JSON or overwrite index updates

Required fix:

- write to a temporary file and atomically replace the destination
- add optimistic revision numbers or per-task locking
- make index rebuildable from manifests instead of authoritative

Acceptance:

- simulated interrupted writes preserve the last valid manifest
- concurrent task saves do not corrupt task state

## 3. P1 Product Experience Gaps

### P1.1 The Normal Flow Does Not Automatically Prepare Assets

Current behavior:

- `ProductAgent` invokes `run_writing_agent()` with image upload disabled
- articles with local image paths commonly stop at `needs_attention`
- `preparing_assets` exists in the state machine but is not used by `ProductAgent`

Required fix:

```text
content_ready
  -> preparing_assets
  -> upload/cache images
  -> re-review final artifact
  -> build preview
  -> awaiting_publish_approval
```

Asset upload should be an explicit configured product behavior because it is an external side effect.

### P1.2 There Is No Real Product Preview Artifact

Current behavior:

- the task says "review preview"
- task artifacts do not expose final Markdown, HTML, cover, screenshots, or a preview URL
- there is no mobile viewport rendering

Required fix:

- link final Markdown, HTML, review reports, and cover in task artifacts
- generate desktop and mobile preview screenshots
- ensure preview HTML is exactly the content used for WeChat draft creation

Acceptance:

- the user can review title, cover, images, line breaks, tables, and formulas before approval

### P1.3 Intent Routing Is Mostly URL Detection

Current behavior:

- paper URL or ID means `explain_paper`
- all other requests mean `explore_topic`
- scheduled requests, resume requests, style instructions, quotas, and approval policy are not parsed
- the full natural-language request is sent directly as an arXiv search query

Required fix:

- add a structured intent schema
- use deterministic parsing first, optional LLM planning second
- extract topic, time range, ranking profile, candidate count, audience, style, schedule, and approval policy
- compile user language into valid arXiv queries

### P1.4 Candidate Ranking Profiles Overpromise

Current behavior:

- `classic` has no citation signal
- `engineering` has no repository or reproducibility signal
- all profiles mainly remix relevance, freshness, abstract length, and AI category
- blog and GitHub data are not connected to paper candidates

Required fix:

- label current profiles as heuristic/local-only
- add source timestamps and confidence
- enrich candidates using OpenAlex or Semantic Scholar and GitHub/project matching
- do not expose `classic` or `engineering` as trustworthy modes until their key signals exist

### P1.5 ResearchAssembler Does Not Exist Yet

Current behavior:

- after topic approval, only paper ID and URL are passed to the writing workflow
- candidate rationale, blogs, GitHub projects, related work, and user direction are discarded

Required fix:

- create a persisted research packet
- include paper metadata, candidate rationale, related sources, project links, and user-requested angle
- pass it into outline and drafting prompts with source labels

## 4. P2 Capability and UX Gaps

1. ProductTask operations are not exposed through MCP.
2. TUI still does not present real task cards, approval actions, or preview.
3. Task metrics do not record actual elapsed time, LLM calls, token usage, or external API calls.
4. There is no task cancel, retry, restart, archive, or delete operation.
5. There is no notification when a task reaches an approval gate.
6. There is no scheduling implementation yet.
7. Architecture and WeChat documentation still describe the older pipeline.
8. Candidate rejection reasons and cooldown history are not recorded.
9. Formal publication state is not reconciled back into ProductTask.

## 5. Revised Implementation Order

### Iteration A: Make Tasks Truthful and Safe

1. atomic task storage and task revision number
2. artifact fingerprints
3. approval invalidation
4. imported article review gate
5. separate dry-run and real WeChat state
6. recovery operations for `failed` and `needs_attention`

### Iteration B: Complete the Human Approval Experience

1. automatic `preparing_assets` stage
2. image upload cache
3. final artifact re-review
4. preview bundle and mobile screenshot
5. approval bound to preview/article fingerprint

### Iteration C: Make Resume Real

1. resumable `PaperWritingAgent` stages
2. stage artifact fingerprints
3. skip valid completed stages
4. retry/restart controls
5. crash-safe draft operation journal

### Iteration D: Make Discovery Trustworthy

1. structured intent router and arXiv query compiler
2. candidate-source timestamps and confidence
3. citation enrichment
4. GitHub/project matching
5. ResearchAssembler packet

### Iteration E: Product Surfaces and Scheduling

1. ProductTask MCP tools
2. real ProductAgent TUI
3. notifications
4. schedules and child tasks
5. publication feedback loop

## 6. Recommended Immediate Work

Start with Iteration A before any additional product features.

The first local implementation should cover:

```text
atomic writes
  + task revision
  + article fingerprint
  + approval invalidation
  + imported article review
  + dry-run/real draft separation
  + failed-task retry routing
```

This is the smallest group of changes that turns the current ProductAgent from a promising prototype into a safer foundation for real use.
