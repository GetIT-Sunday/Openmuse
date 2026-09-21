"""Inspect distributions and smoke-test an installed wheel outside the checkout.

Build first with `python -m build`. Dependency installation can use the network;
application smoke tests cannot. No credentials are forwarded or published.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

REQUIRED = {
    "skills/auto-publish/SKILL.md",
    "skills/daily-digest/SKILL.md",
    "skills/paper-deep-read/SKILL.md",
    "skills/trend-analysis/SKILL.md",
    "skills/write-paper-wechat/SKILL.md",
    "skills/write-paper-wechat/references/quality-rubric.md",
    "skills/write-paper-wechat/references/style-patterns.md",
    "skills/write-paper-wechat/references/writing-playbook.md",
    "skills/write-paper-wechat/scripts/analyze_article.py",
    "skills/write-paper-wechat/agents/openai.yaml",
    "packs/research-to-wechat/pack.yaml",
    "packs/research-to-wechat/workflows/paper-to-wechat.yaml",
}


class CommandFailed(RuntimeError):
    """Only fixed step names and exit codes are safe for public reports."""

    def __init__(self, step: str, code: int):
        super().__init__(f"{step} failed (exit {code})")
        self.step = step
        self.code = code


def check_names(names: list[str]) -> None:
    forbidden = {".env", ".git", ".aigc", ".venv", "__pycache__", "data", "workspace", "memory", "artifacts"}
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
            raise ValueError("Archive contains an unsafe path")
        if forbidden.intersection(path.parts) or path.suffix in {".pem", ".key", ".sqlite3", ".pyc"}:
            raise ValueError("Archive contains private/generated files")


def inspect_distributions(directory: Path, expected: str | None = None) -> dict:
    wheels, sources = list(directory.glob("*.whl")), list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("Use a fresh output directory with exactly one wheel and one sdist")
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        check_names(names)
        if not {"smearglepaper/_bundled/" + name for name in REQUIRED}.issubset(names):
            raise ValueError("Wheel is missing portable capabilities")
        metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError("Wheel metadata is missing or ambiguous")
        meta = BytesParser().parsebytes(wheel.read(metadata[0]))
        if meta["Name"] != "smearglepaper" or not meta["Version"]:
            raise ValueError("Unexpected distribution identity")
        version = str(meta["Version"])
        if expected is not None and version != expected:
            raise ValueError("Requested version does not match package metadata")
        entries = wheel.read(metadata[0].replace("METADATA", "entry_points.txt")).decode()
        if any(f"{name} = smearglepaper.cli:main" not in entries for name in ("openmuse", "smearglepaper")):
            raise ValueError("Missing compatibility entry points")
    with tarfile.open(sources[0]) as archive:
        members = archive.getmembers()
        check_names([member.name for member in members])
        if any(not (member.isfile() or member.isdir()) for member in members):
            raise ValueError("Source archive contains links or special files")
        prefix = f"smearglepaper-{version}/"
        names = {member.name for member in members}
        if not {prefix + name for name in REQUIRED | {"pyproject.toml", "setup.py", "LICENSE", "SECURITY.md"}}.issubset(names):
            raise ValueError("Source archive identity/resources do not match wheel")
    return {
        "version": version,
        "artifacts": [{"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                      for path in (wheels[0], sources[0])],
    }


def run(command: list[str], *, cwd: Path, environment: dict[str, str], timeout: int = 180, label: str = "subprocess") -> str:
    process = subprocess.Popen(command, cwd=cwd, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=os.name == "posix")
    try:
        stdout, _stderr = process.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.communicate()
        raise
    if process.returncode:
        # Do not copy arbitrary provider/config output into public release reports.
        raise CommandFailed(label, process.returncode)
    return stdout


def smoke(wheel: Path, version: str) -> None:
    with tempfile.TemporaryDirectory(prefix="openmuse-release-") as temporary:
        root = Path(temporary)
        environment = {key: value for key, value in os.environ.items()
                       if key in {"PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "SSL_CERT_FILE"}}
        home = root / "home"
        home.mkdir()
        environment.update(HOME=str(home), USERPROFILE=str(home), OPENMUSE_HOME=str(home / ".openmuse"), PYTHONNOUSERSITE="1")
        command = [sys.executable, "-m", "venv", str(root / "venv")]
        run(command, cwd=root, environment=environment, label="create clean environment")
        binary = root / "venv" / ("Scripts" if os.name == "nt" else "bin")
        python = binary / ("python.exe" if os.name == "nt" else "python")
        install = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input"]
        run([*install, str(wheel.resolve())], cwd=root, environment=environment, timeout=300, label="install wheel and dependencies")
        guard = root / "guard"
        guard.mkdir()
        shutil.copyfile(Path(__file__).parent / "eval_support/sitecustomize.py", guard / "sitecustomize.py")
        environment.update(PYTHONPATH=str(guard), OPENMUSE_EVAL_ROOT=str(root))
        run([str(python), "-m", "pip", "check"], cwd=root, environment=environment, label="declared dependency consistency")
        probe = '''
import json, sys
from pathlib import Path
import smearglepaper
from smearglepaper.config import ROOT_DIR, RESOURCE_ROOT
from smearglepaper.tools import installed_capabilities
from smearglepaper.skill_registry import SkillRegistry
assert Path(smearglepaper.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
assert ROOT_DIR == Path.home().resolve() / ".openmuse"
assert not ROOT_DIR.is_relative_to(Path(sys.prefix).resolve())
assert RESOURCE_ROOT.is_relative_to(Path(sys.prefix).resolve())
registry = SkillRegistry(RESOURCE_ROOT / "skills")
assert len(registry.list()) == 5 and not registry.validate()
assert installed_capabilities()[0]
from smearglepaper.config import save_openai_connection
save_openai_connection("https://example.invalid/v1", "offline", "offline-placeholder")
assert (ROOT_DIR / ".env").is_file()
import asyncio
from smearglepaper.tui import SmearglePaperApp
async def check_tui():
    async with SmearglePaperApp().run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        composer = pilot.app.screen.query_one("#input-bar")
        assert composer.region.bottom <= 24
asyncio.run(check_tui())
'''
        run([str(python), "-c", probe], cwd=root, environment=environment, label="installed resources and state paths")
        for name in ("openmuse", "smearglepaper"):
            executable = str(binary / (name + (".exe" if os.name == "nt" else "")))
            if run([executable, "--version"], cwd=root, environment=environment).strip() != f"OpenMuse {version}":
                raise ValueError("Installed CLI version mismatch")
            run([executable, "--help"], cwd=root, environment=environment)
        cli = [str(python), "-m", "smearglepaper"]
        packs = json.loads(run([*cli, "pack", "list"], cwd=root, environment=environment))
        if not any(pack["id"] == "autowechat/research-to-wechat" for pack in packs):
            raise ValueError("Installed Pack missing")
        run([*cli, "pack", "export", "autowechat/research-to-wechat", "--to", str(root / "export")],
            cwd=root, environment=environment, label="portable Pack export")
        payload = run([*cli, "run", "paper-to-article", "--offline-example", "--json",
                       "--workspace", str(root / "runtime")], cwd=root, environment=environment, label="offline article journey")
        manifest = json.loads(payload)
        if manifest.get("status") != "completed":
            raise ValueError("Installed offline journey did not complete")
        if not list(root.rglob("*.html")):
            raise ValueError("Installed offline journey produced no HTML")
        if (root / "network-attempts").exists():
            raise ValueError("Installed smoke test attempted network access")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--expected-version")
    args = parser.parse_args(argv)
    report = {"status": "failed", "dependency_isolation": True, "phase": "archive-inspection"}
    args.dist.mkdir(parents=True, exist_ok=True)
    report_path = args.dist / "release-check.json"
    # An interrupted/restarted check must not leave a previous green report.
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    try:
        report.update(inspect_distributions(args.dist, args.expected_version))
        report["phase"] = "clean-install-and-smoke"
        smoke(next(args.dist.glob("*.whl")), report["version"])
        report["status"] = "passed"
        report["phase"] = "complete"
    except (ValueError, RuntimeError, OSError, KeyError, tarfile.TarError, zipfile.BadZipFile, subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        report["error_type"] = type(error).__name__
        if isinstance(error, CommandFailed):
            report.update(step=error.step, exit_code=error.code)
        print(f"Release check failed: {type(error).__name__}", file=sys.stderr)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
