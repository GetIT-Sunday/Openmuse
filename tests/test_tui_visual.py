from __future__ import annotations

import asyncio
import unittest

from smearglepaper.trace_message import TraceMessage
from smearglepaper.tui import SmearglePaperApp
from smearglepaper.tui_components import OPENMUSE_LOGO
from smearglepaper.tui_presentation import build_run_presentation


class TUIVisualContractTests(unittest.TestCase):
    def test_live_brand_is_openmuse(self) -> None:
        self.assertEqual(OPENMUSE_LOGO, "OpenMuse")

    def test_conversation_roles_have_distinct_labels(self) -> None:
        user = TraceMessage.create("user", "请解读这篇论文").to_rich_text()
        agent = TraceMessage.create("agent", "我会先确认论文材料").to_rich_text()
        self.assertIn("你", user)
        self.assertIn("OpenMuse", agent)
        self.assertNotEqual(user, agent)

    def test_visual_states_have_human_readable_projection(self) -> None:
        for status in ("running", "completed", "failed", "waiting_input"):
            steps = [
                {"id": "select", "status": "completed"},
                {"id": "ingest", "status": "completed"},
                {"id": "write", "status": "running" if status == "running" else "completed"},
                {"id": "review", "status": "completed"},
            ]
            if status == "failed":
                steps[-1] = {"id": "review", "status": "failed", "error": {"message": "审阅超时"}}
            if status == "waiting_input":
                steps[0]["status"] = "waiting_input"
            presentation = build_run_presentation(
                {
                    "workflow": "paper-to-article",
                    "status": status,
                    "steps": steps,
                    "request": {"inputs": {"topic": "研究 Agent"}},
                    "quality": {"publish_ready": True},
                }
            )
            self.assertTrue(presentation.headline)
            self.assertTrue(presentation.phase)
            self.assertGreaterEqual(presentation.progress_total, presentation.progress_current)
            if status == "failed":
                self.assertEqual(presentation.phase, "质量审阅")

    def test_common_terminal_sizes_keep_composer_inside_viewport(self) -> None:
        async def exercise() -> None:
            for size in ((80, 24), (136, 51), (156, 54)):
                app = SmearglePaperApp()
                async with app.run_test(size=size) as pilot:
                    await pilot.pause()
                    screen = app.screen
                    stage = screen.query_one("#effect-stage")
                    conversation = screen.query_one("#conversation-area")
                    composer = screen.query_one("#input-bar")
                    self.assertGreater(stage.region.width, 0)
                    self.assertGreater(conversation.region.height, 0)
                    self.assertGreater(composer.region.width, 0)
                    self.assertLessEqual(composer.region.right, size[0])
                    self.assertLessEqual(composer.region.bottom, size[1])

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
