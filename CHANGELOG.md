# Changelog

## Unreleased

- Unified conversation events/projection, Provider streaming, cancellation and controller routing.
- Added bounded context, durable turn recovery, and consent-gated preference memory.
- Added fail-closed offline Harness acceptance and release distribution checks.
- Bundle portable Skills/Pack in wheel and sdist; keep installed state out of site-packages.
- Added `openmuse --version`, `OPENMUSE_HOME`, security policy and release checklist.
- OpenMuse remains Alpha; passing offline gates does not certify online model quality or publishing availability.

## 0.2.0

- Bound LLM waits and fall back to a local evidence draft instead of losing a paper-reading run on provider timeout.
- Keep real WeChat creation blocked when a fallback draft has not passed the quality gate.
- Added a durable Runtime shared by CLI, TUI, and MCP.
- Added ordered event logs, hash-verified Artifacts, checkpoints, cancellation, retry, and resume.
- Added explicit approval for real WeChat writes and dry-run defaults.
- Replaced the TUI mock workflow executor with real Runtime events and session replay.
- Added `run`, `runs`, `approve`, `artifacts`, and `config` automation commands.
- Added Python 3.10-3.12 CI, Ruff, mypy, pre-commit, and Runtime documentation.

## 0.1.0

- Initial SmearglePaper release.
- arXiv collection and ranking.
- Built-in NLP topic presets for semantics, syntax, and pragmatics.
- PDF text parsing and figure extraction.
- Chinese article generation with LLM or local fallback.
- WeChat-friendly HTML and cover generation.
- WeChat draft create/update support.
- MCP server tools.
