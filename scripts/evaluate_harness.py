"""Repository-only, offline Harness acceptance runner; requires the dev extra."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    "journey": ("核心对话与审批", ["tests/test_conversation.py", "tests/test_stage7_journeys.py"]),
    "events": ("事件与重放", ["tests/test_harness_events.py"]),
    "streaming": ("流式、取消与失败恢复", ["tests/test_stage2_streaming.py", "tests/test_stage3_harness.py"]),
    "recovery": ("持久化恢复与上下文预算", ["tests/test_stage5_recovery.py"]),
    "memory": ("偏好同意、隔离与遗忘", ["tests/test_stage6_memory.py"]),
    "presentation": ("终端与只读预览契约", ["tests/test_tui_visual.py", "tests/test_preview_window.py", "tests/test_tui_presentation.py"]),
}


def summarize(raw: dict[str, Any], exit_code: int, groups: list[str]) -> dict[str, Any]:
    """Fail closed: missing, skipped, xfailed, or incomplete tests are not passes."""
    cases = raw.get("cases", [])
    gates = []
    for group in groups:
        label, files = GROUPS[group]
        rows = [r for r in cases if r["nodeid"].split("::", 1)[0] in files]
        covered = {r["nodeid"].split("::", 1)[0] for r in rows}
        passed = sum(r["outcome"] == "passed" for r in rows)
        gates.append({"id": group, "label": label, "passed": passed, "total": len(rows),
                      "status": "passed" if covered == set(files) and passed == len(rows) else "failed"})
    complete = (exit_code == 0 and raw.get("finished") is True and raw.get("deselected", 0) == 0
                and raw.get("collection_errors", 0) == 0 and raw.get("collected") == len(cases)
                and raw.get("network_attempts", 0) == 0)
    return {"status": "passed" if complete and all(g["status"] == "passed" for g in gates) else "failed",
            "pytest_exit_code": exit_code, "finished": bool(raw.get("finished")),
            "collected": raw.get("collected", 0), "collection_errors": raw.get("collection_errors", 0),
            "deselected": raw.get("deselected", 0), "network_attempts": raw.get("network_attempts", 0),
            "gates": gates, "cases": cases}


def markdown(report: dict[str, Any]) -> str:
    lines = ["# OpenMuse Harness 离线验收", "", f"结果：**{report['status']}**", "",
             "这是固定模型行为下的系统契约测试，不是真实模型理解准确率、文章质量或线上性能评分。", "",
             "| 验收项 | 通过 / 总数 | 结果 |", "| --- | --- | --- |"]
    for gate in report["gates"]:
        lines.append(f"| {gate['label']} | {gate['passed']} / {gate['total']} | {gate['status']} |")
    lines.extend(["", f"执行耗时：{report['duration_seconds']} 秒（仅本机测试耗时）",
                  f"被阻止的网络尝试：{report['network_attempts']}（必须为 0）",
                  f"代码指纹：`{report['source_sha256']}`", "",
                  "未验证：真实模型意图理解、写作事实质量、微信线上效果、远端服务 SLA。", "",
                  "## 未通过项目", ""])
    failed = [r for r in report["cases"] if r["outcome"] != "passed"]
    lines.extend(f"- `{r['nodeid']}`：{r['outcome']}" for r in failed)
    if not failed:
        lines.append("无失败用例。" if report["status"] == "passed" else "测试未完整执行，请检查环境、收集错误或超时。")
    lines.extend(["", "报告只含测试标识和结果元信息，不包含模型原文、工具结果或异常正文。", ""])
    return "\n".join(lines)


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    paths = [ROOT / "pyproject.toml"]
    for directory in ("src", "tests", "scripts"):
        paths.extend(p for p in (ROOT / directory).rglob("*") if p.suffix in {".py", ".json"})
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def run_tests(command: list[str], *, environment: dict[str, str], sandbox: Path, timeout: int) -> int:
    """Bound the runner and its Python subprocesses, not just the pytest parent."""
    with (sandbox / "pytest.log").open("w", encoding="utf-8") as log:
        try:
            process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=os.name == "posix")
        except OSError:
            return 127
        try:
            return process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait()
            return 124 if isinstance(exc, subprocess.TimeoutExpired) else 130


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "evaluations")
    parser.add_argument("--group", action="append", choices=list(GROUPS), help="诊断子集；不能代替完整验收")
    parser.add_argument("--timeout", type=int, default=180, help="整套测试超时秒数（默认 180）")
    args = parser.parse_args(argv)
    if args.timeout < 1:
        parser.error("timeout 必须大于 0")
    groups = list(dict.fromkeys(args.group or GROUPS))
    now = datetime.now(timezone.utc)
    output = args.output.resolve() / (now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    output.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {"schema_version": 1, "suite_version": 1, "mode": "offline_contract",
        "scope": "full" if set(groups) == set(GROUPS) else "subset", "created_at": now.isoformat(),
        "python": platform.python_version(), "platform": sys.platform, "source_sha256": source_fingerprint()}
    import time
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="openmuse-eval-") as temporary:
        sandbox = Path(temporary)
        raw_path = sandbox / "results.json"
        # Deliberately do not forward provider credentials, pytest flags/plugins,
        # model settings, or local workspace/profile paths to the test child.
        allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL"}
        environment = {k: v for k, v in os.environ.items() if k in allowed}
        environment.update({"HOME": str(sandbox), "SMEARGLEPAPER_HOME": str(sandbox / "runtime"),
            "OPENMUSE_EVAL_ROOT": str(sandbox), "OPENMUSE_EVAL_REPORT": str(raw_path),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": os.pathsep.join([str(ROOT / "scripts" / "eval_support"), str(ROOT / "src"), str(ROOT)]),
            "OPENAI_API_KEY": "offline-test-only", "OPENAI_BASE_URL": "https://example.test/v1",
            "OPENAI_MODEL": "offline-contract", "OPENMUSE_CONTEXT_TOKENS": "32768", "OPENMUSE_OUTPUT_TOKENS": "4096"})
        targets = [file for group in groups for file in GROUPS[group][1]]
        command = [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "-p", "eval_plugin",
                   "-p", "no:cacheprovider", *targets]
        code = run_tests(command, environment=environment, sandbox=sandbox, timeout=args.timeout)
        try:
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        attempts = sandbox / "network-attempts"
        raw["network_attempts"] = len(attempts.read_text().splitlines()) if attempts.exists() else 0
        report.update(summarize(raw, code, groups))
    report["duration_seconds"] = round(time.monotonic() - started, 3)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "report.md").write_text(markdown(report), encoding="utf-8")
    print(f"OpenMuse offline acceptance: {report['status']} ({report['scope']})")
    for gate in report["gates"]:
        print(f"  {gate['id']}: {gate['passed']}/{gate['total']} {gate['status']}")
    print(f"Report: {output / 'report.md'}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
