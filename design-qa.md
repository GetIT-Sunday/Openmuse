# AutoWechat Entry And Exit Design QA

- Reference: user-provided OpenCode `156x54` initial screen.
- Implementation: AutoWechat TUI initial and terminal exit states.
- Captures: `docs/audits/harness-screenshots/156x54-initial.svg`, `136x51-initial.svg`, and `80x24-initial.svg`.

## Comparison

- The product name is the first visual signal and is centered above the primary input path.
- The existing AutoWechat dark research-editor palette is preserved instead of copying OpenCode's light palette.
- The initial state hides runtime progress and artifact details until a task begins.
- The compact viewport replaces the large wordmark with a one-line brand so the composer remains visible.
- `Ctrl+Q`, `/exit`, and `/quit` render the same wordmark after leaving the alternate screen and confirm automatic saving.

## Result

No P0, P1, or P2 layout issues remain in the checked entry states. Quick Look may render Chinese glyphs with visual doubling; this is a thumbnail renderer limitation already documented for these captures and does not occur in the target terminal fonts.

final result: passed
