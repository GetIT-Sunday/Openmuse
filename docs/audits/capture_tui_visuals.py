from __future__ import annotations

import asyncio
from pathlib import Path

from smearglepaper.tui import SmearglePaperApp

OUT = Path(__file__).parent / "harness-screenshots"


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size, name in [
        ((156, 54), "156x54-initial.svg"),
        ((136, 51), "136x51-initial.svg"),
        ((80, 24), "80x24-initial.svg"),
    ]:
        app = SmearglePaperApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            app.save_screenshot(name, str(OUT))


if __name__ == "__main__":
    asyncio.run(main())
