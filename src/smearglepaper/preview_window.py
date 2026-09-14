from __future__ import annotations

import shutil
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PreviewLaunch:
    mode: str
    target: str
    browser: str


def select_preview_html(artifacts: object) -> Path | None:
    """Pick the most useful final HTML artifact from a Runtime manifest."""
    if not isinstance(artifacts, list):
        return None
    candidates: list[tuple[int, Path]] = []
    for item in artifacts:
        if not isinstance(item, dict) or item.get("status") == "stale":
            continue
        path = Path(str(item.get("path", "")))
        if path.suffix.lower() != ".html" or not path.is_file():
            continue
        name = path.name.lower()
        priority = 0 if any(label in name for label in ("final", "article", "wechat")) else 1
        candidates.append((priority, path))
    return min(candidates, key=lambda candidate: (candidate[0], candidate[1].name))[1] if candidates else None


def launch_mobile_preview(source: Path, *, width: int = 430, height: int = 900) -> PreviewLaunch:
    """Open final article HTML in a compact browser app window."""
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Preview HTML not found: {source}")

    return launch_mobile_preview_target(source.as_uri(), width=width, height=height)


def launch_mobile_preview_target(target: str, *, width: int = 430, height: int = 900) -> PreviewLaunch:
    """Open a local preview URL in a compact browser app window."""
    browser = find_app_browser()
    if browser is not None:
        subprocess.Popen(
            [
                str(browser),
                f"--app={target}",
                f"--window-size={width},{height}",
                "--new-window",
                "--no-first-run",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return PreviewLaunch("app", target, browser.name)

    if not webbrowser.open(target, new=1):
        raise RuntimeError("No supported browser could open the mobile preview.")
    return PreviewLaunch("browser", target, "system default")


def find_app_browser() -> Path | None:
    candidates: list[str | Path | None] = []
    if sys.platform == "darwin":
        candidates.extend(
            [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                Path.home() / "Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            ]
        )
    elif sys.platform == "win32":
        candidates.extend(
            [
                Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe",
                Path.home() / "AppData/Local/Microsoft/Edge/Application/msedge.exe",
            ]
        )
    candidates.extend(shutil.which(name) for name in ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge", "brave"))
    return next((Path(path) for path in candidates if path and Path(path).is_file()), None)
