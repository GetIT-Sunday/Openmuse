# OpenMuse release gates (Stage 8)

OpenMuse remains Alpha. The distribution/module name is `smearglepaper`; both
`openmuse` and `smearglepaper` CLI entry points remain supported. The current
version is not automatically bumped by a passing gate.

## Required gates

1. Ruff, the configured mypy scope, and the full pytest suite pass.
2. All six offline Harness acceptance groups pass, with no skipped cases or
   network attempts. See [HARNESS_EVALUATION.md](HARNESS_EVALUATION.md).
3. Build an sdist, then build the wheel from that sdist (`python -m build`).
4. Inspect archive paths, required resources, version metadata and both CLI entry
   points. Reject known private/generated paths and symlinks in the sdist.
5. Install the wheel with its declared dependencies in a new virtual environment
   outside the checkout. Validate Skills, export the Pack, persist configuration
   to a temporary user home, mount the installed TUI at 80×24 using Textual Pilot,
   and generate an article plus HTML offline.
6. All supported CI matrix jobs pass. Review the changelog, compatibility notes,
   dependency changes and security implications before tagging anything.

Archive checks are structural checks, **not a comprehensive secret scanner**.
Review source and documentation for accidentally pasted secrets as well.
SHA-256 records identify the tested artifacts; they are not a signature or a
reproducible-build guarantee. Local paths/command logs are excluded from the
public gate report.

## Local verification

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
ruff check src tests scripts
mypy
pytest -q
python scripts/evaluate_harness.py
# Use a new output directory for each candidate; stale artifacts are rejected.
python -m build --outdir dist/candidate
python scripts/check_release.py --dist dist/candidate --expected-version 0.2.0
git diff --check
```

Dependency installation may access package indexes. The installed application
smoke test itself runs under the offline network guard and a temporary HOME with
no inherited provider credentials. Dependencies are deliberately not inherited
from the development environment; unavailable package indexes fail the check.

The CI workflow also supports a manual run from Actions (`workflow_dispatch`).
It uploads tested archives and JSON/Markdown reports, but does **not** create a
GitHub Release, tag, push, upload to PyPI, or deploy the Pack registry. Those are
separate, explicitly authorized maintainer actions after reviewing the reports.
Configure branch protection to require the CI jobs; a workflow file alone cannot
enforce repository settings. Do not attach a previous run's artifacts to a new tag.

## Installation and data compatibility

- Source checkout: `.env`, `data/`, `memory/` and `.aigc/` keep existing locations.
- Wheel installation: application config, article data, preferences and Packs live
  under `~/.openmuse`, not site-packages. The durable Runtime retains its existing
  default `~/.local/share/smearglepaper` to preserve previously saved runs.
- `OPENMUSE_HOME=/absolute/path` overrides the mutable state root in either mode.
  Set it **before** starting OpenMuse. Runtime then defaults to its `runtime/`
  subdirectory, unless an explicit workspace, `SMEARGLEPAPER_HOME`, or the existing
  Runtime config sets a workspace. Existing data is not moved automatically.
- Bundled Skills/Pack are read-only package resources; installed Packs live in
  the mutable state root. Do not edit files inside site-packages.
- Back up the state directory before an upgrade. Downgrading durable state formats
  is not guaranteed; restore the backup together with the previous package version.
- Python 3.10–3.12/Linux and 3.12/macOS are the release matrix. Windows remains
  experimental. Online provider behavior, live publishing and arbitrary terminal
  rendering need separate manual checks; CI uses offline fixtures.

## Candidate review checklist

- [ ] CI for the exact intended commit is green on every matrix entry.
- [ ] Acceptance and release-check reports belong to this build, with isolated installation.
- [ ] SHA-256 values match the downloaded wheel and sdist.
- [ ] Version in pyproject, release tag, changelog and README badge is consistent.
- [ ] No credentials, private history, generated drafts or personal resources in artifacts.
- [ ] TUI manually checked at 80×24 and normal desktop terminal size.
- [ ] Optional provider smoke uses disposable credentials, never real publishing by default.
- [ ] Breaking changes, backup/rollback steps and known limitations documented.
- [ ] Maintainer explicitly authorizes the release/publish step.
