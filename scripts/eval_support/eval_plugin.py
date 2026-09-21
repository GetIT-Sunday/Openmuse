"""Pytest adapter: isolate storage and record outcomes without raw failure text."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

RESULTS = {"finished": False, "collected": 0, "collection_errors": 0, "deselected": 0, "cases": []}
CASES = {}


def persist():
    path = Path(os.environ["OPENMUSE_EVAL_REPORT"])
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({**RESULTS, "cases": list(CASES.values())}), encoding="utf-8")
    temporary.replace(path)


def pytest_collection_finish(session):
    RESULTS["collected"] = len(session.items)
    for item in session.items:
        CASES[item.nodeid] = {"nodeid": item.nodeid, "outcome": "incomplete", "duration_seconds": 0.0}
    persist()


def pytest_configure(config):
    # Load the source package with dotenv disabled, before test collection. The
    # project config checks exists() before reading its .env; only that exact
    # file is masked during this import, not general filesystem access.
    dotenv = Path(__file__).resolve().parents[2] / ".env"
    exists = Path.exists
    with patch.object(Path, "exists", lambda path: False if path == dotenv else exists(path)):
        import smearglepaper.config as project_config
    project_config.load_dotenv = lambda *args, **kwargs: None


def pytest_collectreport(report):
    if report.failed:
        RESULTS["collection_errors"] += 1
        persist()


def pytest_deselected(items):
    RESULTS["deselected"] += len(items)


def pytest_runtest_logreport(report):
    row = CASES.setdefault(report.nodeid, {"nodeid": report.nodeid, "outcome": "incomplete", "duration_seconds": 0.0})
    row["duration_seconds"] = round(row["duration_seconds"] + report.duration, 6)
    if report.failed:
        row["outcome"] = "failed"
        row["failure_phase"] = report.when
    elif row["outcome"] != "failed":
        if report.skipped or hasattr(report, "wasxfail"):
            row["outcome"] = "skipped"
        elif report.when == "call" and row["outcome"] != "skipped":
            row["outcome"] = "passed"
    persist()


def pytest_sessionfinish(session, exitstatus):
    RESULTS["finished"] = True
    persist()


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    from smearglepaper import config
    # Imported modules may hold aliases to project-level paths. Redirect those
    # aliases before each test, not only the runtime workspace environment.
    for name, module in list(sys.modules.items()):
        if name.startswith("smearglepaper") and module:
            for attribute, value in (("DATA_DIR", tmp_path / "data"), ("SESSIONS_DIR", tmp_path / "data/sessions"),
                                     ("MEMORY_DIR", tmp_path / "editor-defaults")):
                if hasattr(module, attribute):
                    monkeypatch.setattr(module, attribute, value)
    monkeypatch.setenv("SMEARGLEPAPER_HOME", str(tmp_path / "runtime"))
    # The package can load the project .env on import. These values are never
    # used by this suite; erase secrets and prevent a later reload.
    for key in list(os.environ):
        if any(word in key.upper() for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "WEBHOOK")):
            if key not in {"OPENMUSE_CONTEXT_TOKENS", "OPENMUSE_OUTPUT_TOKENS"}:
                monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: None)
    with patch("webbrowser.open", side_effect=AssertionError("Offline acceptance must not launch a browser")):
        yield
