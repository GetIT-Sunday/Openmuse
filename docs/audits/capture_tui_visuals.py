from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Keep this script runnable from a fresh checkout without requiring an editable
# install first.  The application still owns all rendering behavior.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from smearglepaper.tui import SmearglePaperApp  # noqa: E402
from smearglepaper.tui_presentation import build_run_presentation  # noqa: E402

OUT = Path(__file__).parent / "harness-screenshots"

SIZES = {
    "156x54": (156, 54),
    "136x51": (136, 51),
    "80x24": (80, 24),
}


def _manifest_for(state: str) -> dict[str, object]:
    """Return deterministic, offline data for a visual state fixture."""
    steps: list[dict[str, object]] = [
        {"id": "select", "status": "completed"},
        {"id": "ingest", "status": "completed"},
        {"id": "write", "status": "completed"},
        {"id": "review", "status": "completed"},
        {"id": "package", "status": "completed"},
    ]
    status = "completed"
    interaction: dict[str, object] | None = None
    quality: dict[str, object] = {"publish_ready": True, "technical_score": 92, "wechat_score": 88}
    if state == "running":
        status = "running"
        steps[2]["status"] = "running"
    elif state == "failed":
        status = "failed"
        steps[3] = {"id": "review", "status": "failed", "error": {"message": "质量审阅超时"}}
        quality = {}
    elif state == "waiting":
        status = "waiting_input"
        steps[0]["status"] = "waiting_input"
        interaction = {
            "status": "pending",
            "type": "paper_selection",
            "options": [
                {
                    "paper_id": "2401.00001",
                    "title": "A Reliable Study of Research Agents",
                    "published_at": "2026-09-10",
                    "one_sentence_contribution": "比较研究 Agent 在真实任务中的稳定性。",
                    "recommendation_reasons": ["与主题高度相关", "证据完整"],
                },
                {
                    "paper_id": "2401.00002",
                    "title": "Tools for Evidence-Grounded Writing",
                    "published_at": "2026-09-08",
                    "one_sentence_contribution": "提出面向内容生产的证据链工具。",
                    "recommendation_reasons": ["方法清晰"],
                },
            ],
        }
    return {
        "workflow": "paper-to-article",
        "status": status,
        "steps": steps,
        "request": {"inputs": {"topic": "研究 Agent"}},
        "quality": quality,
        "interaction": interaction,
        "artifacts": [
            {"path": "article.html", "producer": "write", "status": "created", "type": "text/html"},
            {"path": "article.md", "producer": "write", "status": "created", "type": "text/markdown"},
        ],
    }


async def _capture(size_name: str, state: str, filename: str) -> None:
    app = SmearglePaperApp()
    async with app.run_test(size=SIZES[size_name]) as pilot:
        await pilot.pause()
        if state != "initial":
            screen = app.screen
            screen._busy = state == "running"
            presentation = build_run_presentation(_manifest_for(state))
            screen._presentation = presentation
            screen.query_one("#effect-stage").update_presentation(presentation, screen.run_state)
            screen._update_input_placeholder()
            screen._update_session_bar()
            await pilot.pause()
        app.save_screenshot(filename, str(OUT))


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # Preserve the README entry points and add the full state matrix beside
    # them so every visual state can be reviewed at the same terminal sizes.
    for size_name in SIZES:
        await _capture(size_name, "initial", f"{size_name}-initial.svg")
        for state in ("running", "completed", "failed", "waiting"):
            await _capture(size_name, state, f"{size_name}-{state}.svg")


if __name__ == "__main__":
    asyncio.run(main())
