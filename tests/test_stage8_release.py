from __future__ import annotations

import importlib.util
import io
import json
import runpy
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_release", ROOT / "scripts/check_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def distributions(directory: Path, *, missing: str = "", extra: str = "") -> None:
    with zipfile.ZipFile(directory / "smearglepaper-0.2.0-py3-none-any.whl", "w") as wheel:
        for name in release.REQUIRED:
            if name != missing:
                wheel.writestr("smearglepaper/_bundled/" + name, "fixture")
        wheel.writestr("smearglepaper-0.2.0.dist-info/METADATA", "Name: smearglepaper\nVersion: 0.2.0\n")
        wheel.writestr("smearglepaper-0.2.0.dist-info/entry_points.txt",
                       "[console_scripts]\nopenmuse = smearglepaper.cli:main\nsmearglepaper = smearglepaper.cli:main\n")
        if extra:
            wheel.writestr(extra, "fixture")
    with tarfile.open(directory / "smearglepaper-0.2.0.tar.gz", "w:gz") as archive:
        for name in release.REQUIRED | {"pyproject.toml", "setup.py", "LICENSE", "SECURITY.md"}:
            info = tarfile.TarInfo("smearglepaper-0.2.0/" + name)
            info.size = 7
            archive.addfile(info, io.BytesIO(b"fixture"))


def test_valid_archives_and_version(tmp_path):
    distributions(tmp_path)
    result = release.inspect_distributions(tmp_path, "0.2.0")
    assert result["version"] == "0.2.0"
    assert len(result["artifacts"]) == 2
    assert all(len(item["sha256"]) == 64 for item in result["artifacts"])
    with pytest.raises(ValueError, match="version"):
        release.inspect_distributions(tmp_path, "0.3.0")


@pytest.mark.parametrize("name", [".env", "data/sessions.json", ".aigc/keys/key.json", "private.key", "memory/profile.json",
                                  "../escape", "/absolute", "C:/escape", "folder\\file"])
def test_reject_private_and_unsafe_archive_paths(tmp_path, name):
    distributions(tmp_path, extra=name)
    with pytest.raises(ValueError):
        release.inspect_distributions(tmp_path)


def test_missing_capability_and_stale_archive_fail(tmp_path):
    distributions(tmp_path, missing="skills/auto-publish/SKILL.md")
    with pytest.raises(ValueError, match="capabilities"):
        release.inspect_distributions(tmp_path)
    (tmp_path / "stale.whl").touch()
    with pytest.raises(ValueError, match="exactly one"):
        release.inspect_distributions(tmp_path)


def test_source_symlink_rejected(tmp_path):
    distributions(tmp_path)
    with tarfile.open(tmp_path / "smearglepaper-0.2.0.tar.gz", "w:gz") as archive:
        link = tarfile.TarInfo("smearglepaper-0.2.0/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "LICENSE"
        archive.addfile(link)
    with pytest.raises(ValueError, match="links"):
        release.inspect_distributions(tmp_path)


def test_failed_check_overwrites_previous_green_report(tmp_path, monkeypatch):
    distributions(tmp_path)
    (tmp_path / "release-check.json").write_text('{"status":"passed"}')

    def fail(*args):
        raise RuntimeError("PRIVATE-ERROR")

    monkeypatch.setattr(release, "smoke", fail)
    assert release.main(["--dist", str(tmp_path)]) == 1
    report = (tmp_path / "release-check.json").read_text()
    assert json.loads(report)["status"] == "failed"
    assert "PRIVATE-ERROR" not in report


def test_home_override_does_not_change_bundled_resources(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMUSE_HOME", str(tmp_path))
    config = runpy.run_path(str(ROOT / "src/smearglepaper/config.py"))
    assert config["ROOT_DIR"] == tmp_path
    assert config["RESOURCE_ROOT"] == ROOT
    assert config["DATA_DIR"] == tmp_path / "data"


def test_installed_config_uses_user_home_not_site_packages(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENMUSE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    target = tmp_path / "site-packages/smearglepaper/config.py"
    target.parent.mkdir(parents=True)
    target.write_text((ROOT / "src/smearglepaper/config.py").read_text())
    config = runpy.run_path(str(target))
    assert config["ROOT_DIR"] == Path.home() / ".openmuse"
    assert config["RESOURCE_ROOT"] == target.parent / "_bundled"
    assert not config["IS_SOURCE_CHECKOUT"]


def test_version_cli_does_not_start_tui(capsys):
    from smearglepaper.cli import build_parser
    from smearglepaper.pack_manager import harness_version

    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"OpenMuse {harness_version()}"


def test_runtime_home_override_preserves_explicit_workspace_precedence(tmp_path, monkeypatch):
    from smearglepaper.runtime.config import resolve_workspace

    monkeypatch.setenv("OPENMUSE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SMEARGLEPAPER_HOME", raising=False)
    assert resolve_workspace(config={}) == tmp_path / "home/runtime"
    assert resolve_workspace(tmp_path / "explicit", config={}) == tmp_path / "explicit"
    assert resolve_workspace(config={"workspace": str(tmp_path / "configured")}) == tmp_path / "configured"
    monkeypatch.setenv("SMEARGLEPAPER_HOME", str(tmp_path / "legacy"))
    assert resolve_workspace(config={}) == tmp_path / "legacy"


def test_interrupted_release_cannot_retain_green_report(tmp_path, monkeypatch):
    distributions(tmp_path)
    report = tmp_path / "release-check.json"
    report.write_text('{"status":"passed"}')

    def interrupt(*args):
        assert json.loads(report.read_text())["status"] == "failed"
        raise KeyboardInterrupt

    monkeypatch.setattr(release, "smoke", interrupt)
    assert release.main(["--dist", str(tmp_path)]) == 1
    assert json.loads(report.read_text())["error_type"] == "KeyboardInterrupt"


def test_release_subprocess_is_bounded(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        release.run([sys.executable, "-c", "import time; time.sleep(10)"], cwd=tmp_path, environment={}, timeout=1)
