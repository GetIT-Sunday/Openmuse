from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from smearglepaper.harness_events import EventJournal, HarnessEvent, normalize_event
from smearglepaper.harness_projection import HarnessProjection
from smearglepaper.runtime import AgentRuntime, RunRequest, ToolSpec, WorkflowDefinition, WorkflowStep
from smearglepaper.runtime.models import RunEvent, ToolOutcome
from smearglepaper.runtime.registry import ToolRegistry


class HarnessEventContractTests(unittest.TestCase):
    def test_turn_and_runtime_events_normalize_to_same_shape(self) -> None:
        turn = HarnessEvent.create(
            "model.delta",
            session_id="session-1",
            turn_id="turn-1",
            sequence=1,
            source="model",
            payload={"text": "hello"},
        )
        runtime = RunEvent.create("run-1", "session-1", "writer", "step.started", 1, {"step": "write"})
        normalized = normalize_event(runtime)
        self.assertEqual(normalized.session_id, turn.session_id)
        self.assertEqual(normalized.source, "runtime")
        self.assertEqual(normalized.run_id, "run-1")
        self.assertEqual(normalized.turn_id, "run:run-1")
        projection = HarnessProjection()
        projection.apply(turn)
        projection.apply(runtime)
        self.assertEqual(projection.state.phase, "撰写文章")
        self.assertEqual(projection.state.status, "running")

    def test_event_round_trip_and_session_journal_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = EventJournal(Path(tmp) / "session.events.jsonl")
            first = HarnessEvent.create("turn.started", session_id="s", turn_id="t", sequence=1, source="harness")
            second = HarnessEvent.create("turn.completed", session_id="s", turn_id="t", sequence=9, source="harness")
            journal.append(first)
            journal.append(second)
            events = journal.events()
            self.assertEqual([event.sequence for event in events], [1, 2])
            self.assertEqual(HarnessEvent.from_dict(events[1].to_dict()), events[1])

    def test_projection_replay_matches_live_reduction(self) -> None:
        events = [
            HarnessEvent.create("turn.started", session_id="s", turn_id="t", sequence=1, source="harness"),
            HarnessEvent.create("model.started", session_id="s", turn_id="t", sequence=2, source="model"),
            HarnessEvent.create("model.delta", session_id="s", turn_id="t", sequence=3, source="model", payload={"text": "完成"}),
            HarnessEvent.create("turn.completed", session_id="s", turn_id="t", sequence=4, source="harness"),
        ]
        live = HarnessProjection()
        for event in events:
            live.apply(event)
        replay = HarnessProjection()
        replay.replay(reversed(events))
        self.assertEqual(live.state.status, "completed")
        self.assertEqual(live.state.assistant_text, replay.state.assistant_text)
        self.assertEqual(live.state.event_count, replay.state.event_count)

    def test_runtime_can_mirror_events_into_session_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = EventJournal(Path(tmp) / "session.events.jsonl")
            registry = ToolRegistry()
            registry.register(ToolSpec("one"), lambda context: ToolOutcome(message="ok"))
            runtime = AgentRuntime(
                Path(tmp) / "runtime",
                workflow=object(),  # type: ignore[arg-type]
                registry=registry,
                workflows={"test": WorkflowDefinition("test", "test", (WorkflowStep("one", "one", "writer"),))},
            )
            runtime.attach_event_journal("session-1", journal)
            runtime.run(RunRequest("test"), session_id="session-1")
            events = journal.events()
            self.assertTrue(events)
            self.assertTrue(all(event.source == "runtime" for event in events))
            self.assertEqual(events[-1].type, "run.completed")


if __name__ == "__main__":
    unittest.main()
