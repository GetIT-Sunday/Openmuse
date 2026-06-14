# Writing Playbook

## Contents

- Evidence ledger
- Reader promise
- Narrative patterns
- Section budgets
- Figure-led explanation
- Editing passes

## Evidence Ledger

Create a compact ledger before drafting:

| Claim | Evidence | Scope | Article use |
|---|---|---|---|
| Core contribution | Abstract/conclusion | Author claim | Opening |
| Method mechanism | Method section/Figure | Direct description | Method |
| Result | Table/metric | Dataset and baseline | Experiment |
| Limitation | Limitations/conclusion | Author-stated or inferred | Limits |

Reject any numeric claim without an identifiable source. For old or influential papers, separate what the original paper showed from what later history established.

## Reader Promise

Good promises are narrow:

- Understand why self-attention improves training parallelism.
- Understand how the paper turns execution failures into harness updates.
- Decide whether the method is relevant to a production retrieval system.

Bad promises are vague:

- Fully understand the paper.
- Learn everything about Transformers.

## Narrative Patterns

### Mechanism Paper

Problem → old bottleneck → key insight → architecture diagram → component walkthrough → experiments → limits.

Turn this into an argument chain:

1. What did the authors claim was wrong with prior approaches?
2. What design hypothesis followed from that diagnosis?
3. Which architectural choices implement the hypothesis?
4. Which experiment tests each claim?
5. Which ablation or comparison weakens alternative explanations?
6. Where does the evidence stop?

Do not replace this chain with a general tutorial about the method's later popularity.

### Benchmark or Empirical Paper

Question → measurement design → dataset/metric → headline result → subgroup results → threats to validity → implications.

### System or Agent Paper

Failure scenario → system loop → component responsibilities → trace/case study → aggregate evaluation → operational risks.

### Survey

Field map → taxonomy → representative approaches → disagreements → open problems → reading path.

## Section Budgets

For a standard 3,000-5,000 character article:

| Section | Budget |
|---|---:|
| Opening and reader promise | 200-350 |
| Background and bottleneck | 400-700 |
| Core insight | 300-500 |
| Method walkthrough | 1,000-1,600 |
| Experiments and evidence | 700-1,200 |
| Limits and implications | 400-700 |
| Closing and paper info | 150-300 |

Do not pad sections to hit a target. Increase length only by adding evidence, useful examples, or clearer causal explanation.

## Figure-Led Explanation

For each figure:

1. **Question**: What should the reader look for?
2. **Orientation**: What do axes, blocks, colors, or columns mean?
3. **Observation**: What pattern is visible?
4. **Meaning**: Which claim does it support?
5. **Boundary**: What can it not establish?

Use a figure only if the explanation adds value beyond its caption.

## Editing Passes

1. **Evidence pass**: verify claims, numbers, and scope.
2. **Structure pass**: ensure each section advances the reader promise.
3. **Clarity pass**: split long paragraphs and explain jargon at first use.
4. **Compression pass**: remove repeated praise, generic history, and filler.
5. **Risk pass**: soften unsupported causal or superlative language.
6. **Visual pass**: verify image order, captions, spacing, and mobile readability.
