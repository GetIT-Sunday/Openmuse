from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("evaluate_harness", ROOT / "scripts/evaluate_harness.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def raw_report(outcome="passed"):
    file = evaluation.GROUPS["events"][1][0]
    return {"finished": True, "collected": 1, "cases": [{"nodeid": file + "::test_contract", "outcome": outcome}]}


def test_gate_accepts_complete_pass_and_rejects_empty_report():
    assert evaluation.summarize(raw_report(), 0, ["events"])["status"] == "passed"
    assert evaluation.summarize({}, 0, ["events"])["status"] == "failed"


@pytest.mark.parametrize("outcome", ["failed", "skipped", "incomplete"])
def test_skip_failure_and_incomplete_cannot_be_green(outcome):
    assert evaluation.summarize(raw_report(outcome), 0, ["events"])["status"] == "failed"


@pytest.mark.parametrize("field,value", [("finished", False), ("collection_errors", 1), ("deselected", 1), ("collected", 2), ("network_attempts", 1)])
def test_incomplete_or_filtered_run_cannot_be_green(field, value):
    raw = raw_report()
    raw[field] = value
    assert evaluation.summarize(raw, 0, ["events"])["status"] == "failed"


def test_nonzero_exit_and_missing_required_file_fail_gate():
    assert evaluation.summarize(raw_report(), 124, ["events"])["status"] == "failed"
    assert evaluation.summarize(raw_report(), 0, ["journey"])["status"] == "failed"


def test_runner_timeout_writes_failed_report_and_ignores_stale_output(tmp_path):
    previous = tmp_path / "old"
    previous.mkdir()
    (previous / "report.json").write_text('{"status":"passed"}')
    with patch.object(evaluation, "run_tests", return_value=124):
        assert evaluation.main(["--output", str(tmp_path), "--group", "events", "--timeout", "1"]) == 1
    new = next(p for p in tmp_path.iterdir() if p.name != "old")
    report = json.loads((new / "report.json").read_text())
    assert report["pytest_exit_code"] == 124
    assert report["status"] == "failed" and report["scope"] == "subset"
    assert "模型理解准确率" in (new / "report.md").read_text()
    assert json.loads((previous / "report.json").read_text())["status"] == "passed"


def test_runner_does_not_forward_credentials_or_pytest_filters(tmp_path):
    def child(command, **kwargs):
        env = kwargs["environment"]
        assert "GITHUB_TOKEN" not in env and "PYTEST_ADDOPTS" not in env
        assert env["OPENAI_API_KEY"] == "offline-test-only"
        Path(env["OPENMUSE_EVAL_REPORT"]).write_text(json.dumps(raw_report()))
        return 0
    with patch.dict(os.environ, {"GITHUB_TOKEN": "private-fixture", "PYTEST_ADDOPTS": "-k nonexistent"}), \
         patch.object(evaluation, "run_tests", side_effect=child):
        assert evaluation.main(["--output", str(tmp_path), "--group", "events"]) == 0


def test_runner_enforces_process_timeout(tmp_path):
    assert evaluation.run_tests([sys.executable, "-c", "import time; time.sleep(10)"],
                                environment=dict(os.environ), sandbox=tmp_path, timeout=1) == 124


def test_pytest_plugin_captures_teardown_failures_and_xfail(tmp_path):
    suite = tmp_path / "test_outcomes.py"
    suite.write_text('''import pytest
@pytest.fixture
def broken_cleanup():
    yield
    raise RuntimeError("PRIVATE-DETAIL-DO-NOT-EXPORT")
def test_pass():
    pass
def test_cleanup(broken_cleanup):
    pass
@pytest.mark.xfail(reason="known problem")
def test_xfail():
    assert False
''', encoding="utf-8")
    report = tmp_path / "results.json"
    env = {**os.environ, "OPENMUSE_EVAL_ROOT": str(tmp_path), "OPENMUSE_EVAL_REPORT": str(report),
           "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
           "PYTHONPATH": os.pathsep.join([str(ROOT / "scripts/eval_support"), str(ROOT / "src")])}
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "-p", "eval_plugin", str(suite)],
                            env=env, capture_output=True, timeout=20)
    assert result.returncode == 1
    raw = json.loads(report.read_text())
    outcomes = {r["nodeid"].split("::")[-1]: r["outcome"] for r in raw["cases"]}
    assert outcomes == {"test_pass": "passed", "test_cleanup": "failed", "test_xfail": "skipped"}
    assert "PRIVATE-DETAIL-DO-NOT-EXPORT" not in report.read_text()


def test_offline_guard_blocks_socket_access_in_child_process(tmp_path):
    env = {**os.environ, "OPENMUSE_EVAL_ROOT": str(tmp_path), "PYTHONPATH": str(ROOT / "scripts/eval_support")}
    result = subprocess.run([sys.executable, "-c", "import socket; socket.create_connection(('127.0.0.1', 9))"],
                            env=env, capture_output=True, timeout=10)
    assert result.returncode != 0
    assert b"Offline acceptance forbids network access" in result.stderr
    assert "socket." in (tmp_path / "network-attempts").read_text()
