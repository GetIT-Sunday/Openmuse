from __future__ import annotations

import html
import shutil
import subprocess
import tempfile
from pathlib import Path

from .artifact_integrity import fingerprint_paths
from .models import Article
from .storage import write_json


def build_preview_bundle(article: Article, output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    article_html = output_dir / "article.html"
    desktop_html = output_dir / "desktop.html"
    mobile_html = output_dir / "mobile.html"
    article_html.write_text(article.html, encoding="utf-8")
    desktop_html.write_text(_preview_shell(article, article_html.name, mobile=False), encoding="utf-8")
    mobile_html.write_text(_preview_shell(article, article_html.name, mobile=True), encoding="utf-8")

    screenshots: dict[str, str] = {}
    browsers = _find_browsers()
    screenshot_errors: list[str] = []
    if browsers:
        for name, source, width, height in (
            ("desktop", desktop_html, 1280, 1800),
            ("mobile", mobile_html, 430, 1800),
        ):
            target = output_dir / f"{name}.png"
            errors: list[str] = []
            for browser in browsers:
                try:
                    _capture(browser, source, target, width, height)
                    screenshots[name] = str(target)
                    break
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    errors.append(f"{browser.name}: {exc}")
            if name not in screenshots:
                screenshot_errors.append(f"{name}: {_short_error('; '.join(errors))}")
    else:
        screenshot_errors.append("Chrome or Edge was not found; HTML previews remain available.")

    preview_files = [article_html, desktop_html, mobile_html, *[Path(path) for path in screenshots.values()]]
    manifest = {
        "status": "ready",
        "article_title": article.title,
        "article_html": str(article_html),
        "desktop_html": str(desktop_html),
        "mobile_html": str(mobile_html),
        "screenshots": screenshots,
        "screenshot_errors": screenshot_errors,
        "mobile_preview_ready": mobile_html.exists(),
        "screenshot_ready": "mobile" in screenshots and "desktop" in screenshots,
        "fingerprint": fingerprint_paths(preview_files),
    }
    manifest_path = output_dir / "preview.json"
    write_json(manifest_path, manifest)
    manifest["manifest"] = str(manifest_path)
    return manifest


def preview_bundle_fingerprint(preview_manifest: Path) -> dict[str, object]:
    payload = __import__("json").loads(preview_manifest.read_text(encoding="utf-8"))
    paths = [preview_manifest]
    for field in ("article_html", "desktop_html", "mobile_html"):
        if payload.get(field):
            paths.append(Path(str(payload[field])))
    screenshots = payload.get("screenshots", {})
    if isinstance(screenshots, dict):
        paths.extend(Path(str(path)) for path in screenshots.values())
    return fingerprint_paths(paths)


def _preview_shell(article: Article, article_file: str, *, mobile: bool) -> str:
    viewport = "390px" if mobile else "760px"
    label = "Mobile preview · 390px" if mobile else "Desktop preview · 760px"
    cover = ""
    if article.cover_path and Path(article.cover_path).exists():
        cover = f'<img class="cover" src="{html.escape(Path(article.cover_path).resolve().as_uri())}" alt="cover">'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(article.title)} - {label}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; padding: 28px 12px 60px; background: #eef1f5; color: #1f2937; font-family: -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; }}
    .meta {{ width: {viewport}; max-width: 100%; margin: 0 auto 12px; color: #667085; font-size: 12px; }}
    .device {{ width: {viewport}; max-width: 100%; margin: 0 auto; background: #fff; border: 1px solid #d7dce3; border-radius: {"28px" if mobile else "10px"}; overflow: hidden; box-shadow: 0 16px 45px rgba(15,23,42,.14); }}
    .header {{ padding: 24px 22px 16px; border-bottom: 1px solid #edf0f4; }}
    .cover {{ display: block; width: 100%; max-height: 260px; object-fit: cover; border-radius: 8px; margin-bottom: 18px; }}
    h1 {{ font-size: 23px; line-height: 1.45; margin: 0 0 10px; color: #111827; }}
    .digest {{ font-size: 13px; line-height: 1.7; color: #667085; }}
    iframe {{ display: block; width: 100%; min-height: 1200px; border: 0; background: #fff; }}
  </style>
</head>
<body>
  <div class="meta">{label} · The iframe below is the exact final article HTML.</div>
  <section class="device">
    <header class="header">
      {cover}
      <h1>{html.escape(article.title)}</h1>
      <div class="digest">{html.escape(article.digest)}</div>
    </header>
    <iframe src="{html.escape(article_file)}" title="article preview"></iframe>
  </section>
</body>
</html>"""


def _find_browsers() -> list[Path]:
    candidates = [
        shutil.which("chrome"),
        shutil.which("msedge"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    return list(dict.fromkeys(Path(path) for path in candidates if path and Path(path).exists()))


def _capture(browser: Path, source: Path, target: Path, width: int, height: int) -> None:
    with tempfile.TemporaryDirectory(prefix="smearglepaper-preview-") as profile:
        result = subprocess.run(
            [
                str(browser),
                "--headless=new",
                "--disable-gpu",
                "--disable-gpu-compositing",
                "--disable-extensions",
                "--disable-features=Vulkan",
                "--hide-scrollbars",
                "--no-first-run",
                "--no-sandbox",
                "--allow-file-access-from-files",
                f"--user-data-dir={profile}",
                f"--window-size={width},{height}",
                f"--screenshot={target}",
                source.resolve().as_uri(),
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
    if result.returncode != 0 or not target.exists():
        detail = (
            (result.stderr or b"").decode("utf-8", errors="replace").strip()
            or (result.stdout or b"").decode("utf-8", errors="replace").strip()
            or f"exit code {result.returncode}"
        )
        raise RuntimeError(detail)


def _short_error(value: str, limit: int = 700) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else f"{compact[:limit]}..."
