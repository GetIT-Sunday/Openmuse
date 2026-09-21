# Contributing

Thanks for helping improve OpenMuse (the Python package remains `smearglepaper`).

## Development Setup

```bash
conda env create -p ./.conda/envs/smearglepaper -f environment.yml
conda run -p ./.conda/envs/smearglepaper python -m pip install -e ".[dev]"
conda run -p ./.conda/envs/smearglepaper python -m pytest -q
```

## Guidelines

- Keep generated runtime files under `data/` and out of commits.
- Prefer deterministic tests that do not require network access.
- Do not commit credentials or `.env`.
- Keep WeChat real API calls behind explicit dry-run/real-run controls.

## Before submitting

```bash
ruff check src tests scripts
mypy
pytest -q
python scripts/evaluate_harness.py
git diff --check
```

Add regression tests for behavior changes, especially recovery, cancellation,
approval and privacy boundaries. Mypy/Ruff currently cover explicitly configured
paths, not every legacy module. Do not describe a narrow check as full coverage.
Provider calls are optional manual checks, not a requirement for contributors.

Release packaging and clean-install checks are documented in
[docs/RELEASING.md](docs/RELEASING.md). Report vulnerabilities according to
[SECURITY.md](SECURITY.md), never by posting credentials or private logs.
