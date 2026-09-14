from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smearglepaper.preview_window import launch_mobile_preview, select_preview_html
from smearglepaper.runtime_preview import RuntimePreviewServer, _preview_shell


class FakeRuntime:
    def __init__(self, workspace: Path, artifact: Path) -> None:
        self.workspace = workspace
        self.artifact = artifact

    def get_run(self, run_id: str) -> dict[str, object]:
        return {
            "run_id": run_id,
            "status": "completed",
            "request": {"inputs": {"revision_number": 2}},
            "artifacts": [{"path": str(self.artifact), "producer": "write", "status": "created"}],
        }


class PreviewWindowTests(unittest.TestCase):
    def test_runtime_preview_detects_new_article_version_and_serves_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            article = root / "final-article.html"
            article.write_text("<h1>Version one</h1>", encoding="utf-8")
            server = RuntimePreviewServer(FakeRuntime(root, article), "run-1")  # type: ignore[arg-type]
            first = server.state()["version"]
            shell = _preview_shell()
            article.write_text("<h1>Version two</h1>", encoding="utf-8")
            second = server.state()["version"]
        self.assertIn("setInterval(sync,1500)", shell)
        self.assertNotEqual(first, second)

    def test_runtime_preview_inlines_only_allowed_local_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            image = root / "figure.png"
            image.write_bytes(b"PNG")
            article = root / "final-article.html"
            article.write_text(
                f'<img src="{image}"><img src="/private/forbidden.png">',
                encoding="utf-8",
            )
            server = RuntimePreviewServer(FakeRuntime(root, article), "run-1")  # type: ignore[arg-type]
            rendered = server.article_html() or ""
        self.assertIn("data:image/png;base64,", rendered)
        self.assertNotIn("/private/forbidden.png", rendered)

    def test_select_preview_html_prefers_final_user_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            generic = root / "package.html"
            final = root / "final-article.html"
            stale = root / "wechat-old.html"
            for path in (generic, final, stale):
                path.write_text("<p>preview</p>", encoding="utf-8")

            selected = select_preview_html(
                [
                    {"path": str(generic), "status": "created"},
                    {"path": str(stale), "status": "stale"},
                    {"path": str(final), "status": "created"},
                ]
            )

        self.assertEqual(selected, final)

    def test_launch_mobile_preview_uses_browser_app_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "article.html"
            source.write_text("<p>preview</p>", encoding="utf-8")
            with (
                patch("smearglepaper.preview_window.find_app_browser", return_value=Path("/Applications/Chrome")),
                patch("smearglepaper.preview_window.subprocess.Popen") as popen,
            ):
                result = launch_mobile_preview(source)

        args = popen.call_args.args[0]
        self.assertEqual(result.mode, "app")
        self.assertIn("--window-size=430,900", args)
        self.assertTrue(any(str(arg).startswith("--app=file:") for arg in args))

    def test_launch_mobile_preview_falls_back_to_default_browser(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "article.html"
            source.write_text("<p>preview</p>", encoding="utf-8")
            with (
                patch("smearglepaper.preview_window.find_app_browser", return_value=None),
                patch("smearglepaper.preview_window.webbrowser.open", return_value=True) as browser_open,
            ):
                result = launch_mobile_preview(source)

        self.assertEqual(result.mode, "browser")
        browser_open.assert_called_once()


if __name__ == "__main__":
    unittest.main()
