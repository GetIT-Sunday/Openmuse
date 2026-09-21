"""Offline Textual screenshots of Stage 6; never calls a provider or publisher."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from textual.widgets import ListView  # noqa: E402

from smearglepaper.tui import SmearglePaperApp  # noqa: E402

OUT = Path(__file__).parent / "harness-screenshots"


async def main() -> None:
    for width, height in ((80, 24), (136, 51), (140, 48)):
        with tempfile.TemporaryDirectory() as temporary, patch("smearglepaper.tui.DATA_DIR", Path(temporary)), patch("smearglepaper.tui.save_session"):
            app = SmearglePaperApp()
            async with app.run_test(size=(width, height)) as pilot:
                console = app.screen
                console._preference_memory.propose("tone", "表达克制，避免标题党；技术结论要有证据。",
                    session_id=console.session.id, turn_id="demo-turn")
                console._refresh_memory_review()
                await pilot.pause()
                await pilot.click("#memory-review")
                await pilot.pause()
                listing = app.screen.query_one("#preference-list", ListView)
                listing.focus()
                listing.index = 0
                await pilot.press("enter")
                await pilot.pause()
                app.save_screenshot(f"stage6-{width}x{height}-memory.svg", str(OUT))


if __name__ == "__main__":
    asyncio.run(main())
