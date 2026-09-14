from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from urllib.error import URLError

from smearglepaper.runtime import AgentRuntime, RunRequest, ToolSpec, WorkflowDefinition, WorkflowStep
from smearglepaper.runtime.engine import _failure_code
from smearglepaper.runtime.models import ToolOutcome
from smearglepaper.runtime.registry import ToolRegistry


class RuntimeTests(unittest.TestCase):
    def _runtime(self, root: str, handlers: dict[str, object], steps: tuple[WorkflowStep, ...]) -> AgentRuntime:
        registry = ToolRegistry()
        for name, handler in handlers.items():
            registry.register(ToolSpec(name, retryable=name == "flaky"), handler)  # type: ignore[arg-type]
        return AgentRuntime(
            root,
            workflow=object(),  # type: ignore[arg-type]
            registry=registry,
            workflows={"test": WorkflowDefinition("test", "test workflow", steps)},
        )

    def test_events_are_ordered_and_replayable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(
                tmp,
                {"one": lambda ctx: ToolOutcome(output={"value": 1}, message="done")},
                (WorkflowStep("one", "one", "scout"),),
            )
            result = runtime.run(RunRequest("test"))
            events = runtime.events(result.run_id)
            self.assertEqual(result.status, "completed")
            self.assertEqual([event.sequence for event in events], list(range(1, len(events) + 1)))
            self.assertEqual(events[-1].type, "run.completed")
            self.assertTrue(Path(runtime.list_artifacts(result.run_id)[0]["path"]).exists())

    def test_resume_uses_verified_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calls = {"count": 0}

            def handler(ctx):
                calls["count"] += 1
                return ToolOutcome(output={"count": calls["count"]})

            runtime = self._runtime(tmp, {"one": handler}, (WorkflowStep("one", "one", "scout"),))
            result = runtime.run(RunRequest("test"))
            resumed = runtime.resume(result.run_id)
            self.assertEqual(resumed.status, "completed")
            self.assertEqual(calls["count"], 1)

    def test_retryable_tool_retries_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calls = {"count": 0}

            def flaky(ctx):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise TimeoutError("temporary")
                return ToolOutcome(output={"ok": True})

            runtime = self._runtime(tmp, {"flaky": flaky}, (WorkflowStep("fetch", "flaky", "scout", max_retries=1),))
            result = runtime.run(RunRequest("test"))
            self.assertEqual(result.status, "completed")
            self.assertEqual(calls["count"], 2)
            self.assertIn("step.retrying", [event.type for event in runtime.events(result.run_id)])

    def test_cancel_stops_before_next_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calls: list[str] = []
            runtime = self._runtime(
                tmp,
                {
                    "one": lambda ctx: (calls.append("one") or ToolOutcome()),
                    "two": lambda ctx: (calls.append("two") or ToolOutcome()),
                },
                (WorkflowStep("one", "one", "scout"), WorkflowStep("two", "two", "reader", ("one",))),
            )
            holder = {"run_id": ""}

            def cancel_after_first(event):
                if event.type == "step.completed" and event.payload.get("step") == "one":
                    runtime.cancel(holder["run_id"])

            created = runtime.create_run(RunRequest("test"))
            holder["run_id"] = created.run_id
            runtime.subscribe(created.run_id, cancel_after_first)
            result = runtime.execute(created.run_id)
            self.assertEqual(result.status, "cancelled")
            self.assertEqual(calls, ["one"])

    def test_real_side_effect_waits_for_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calls = {"count": 0}

            def publish(ctx):
                calls["count"] += 1
                return ToolOutcome(output={"external_id": "draft-1"})

            runtime = self._runtime(
                tmp,
                {"publish": publish},
                (WorkflowStep("publish", "publish", "publisher", approval="real"),),
            )
            result = runtime.run(RunRequest("test", dry_run=False))
            self.assertEqual(result.status, "waiting_approval")
            self.assertEqual(calls["count"], 0)
            runtime.resolve_approval(result.run_id, "approve-publish", True)
            completed = runtime.resume(result.run_id)
            self.assertEqual(completed.status, "completed")
            self.assertEqual(calls["count"], 1)
            runtime.resume(result.run_id)
            self.assertEqual(calls["count"], 1)

    def test_event_payload_redacts_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(
                tmp,
                {"one": lambda ctx: (ctx.emit("tool.output", {"api_key": "secret"}) or ToolOutcome())},
                (WorkflowStep("one", "one", "scout"),),
            )
            result = runtime.run(RunRequest("test"))
            output = next(event for event in runtime.events(result.run_id) if event.type == "tool.output")
            self.assertEqual(output.payload["api_key"], "***")

    def test_same_named_artifacts_do_not_overwrite_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "one" / "result.json"
            second = root / "two" / "result.json"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_text('{"value": 1}', encoding="utf-8")
            second.write_text('{"value": 2}', encoding="utf-8")
            runtime = self._runtime(
                str(root / "runtime"),
                {"one": lambda ctx: ToolOutcome(artifact_paths=[str(first), str(second)])},
                (WorkflowStep("one", "one", "scout"),),
            )
            result = runtime.run(RunRequest("test"))
            paths = [
                item["path"]
                for item in result.artifacts
                if item["producer"] == "one" and not item["path"].endswith("one.json")
            ]
            self.assertEqual(len(paths), 2)
            self.assertEqual(len(set(paths)), 2)

    def test_changed_inputs_only_reexecute_downstream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calls = {"ingest": 0, "write": 0, "review": 0}

            def counted(name):
                def handler(ctx):
                    calls[name] += 1
                    return ToolOutcome(output={"name": name, "count": calls[name]})

                return handler

            runtime = self._runtime(
                tmp,
                {name: counted(name) for name in calls},
                (
                    WorkflowStep("ingest", "ingest", "reader", input_keys=()),
                    WorkflowStep("write", "write", "writer", ("ingest",)),
                    WorkflowStep("review", "review", "reviewer", ("write",)),
                ),
            )
            result = runtime.run(RunRequest("test", {"target_audience": "researchers"}))
            runtime.update_inputs(result.run_id, {"target_audience": "engineers"}, restart_step="write")
            resumed = runtime.resume(result.run_id)
            self.assertEqual(resumed.status, "completed")
            self.assertEqual(calls, {"ingest": 1, "write": 2, "review": 2})

    def test_dry_run_does_not_require_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(
                tmp,
                {"publish": lambda ctx: ToolOutcome(output={"dry_run": True})},
                (WorkflowStep("publish", "publish", "publisher", approval="real"),),
            )
            result = runtime.run(RunRequest("test", dry_run=True))
            self.assertEqual(result.status, "completed")
            self.assertNotIn("approval.required", [event.type for event in runtime.events(result.run_id)])

    def test_external_and_configuration_failures_have_stable_codes(self) -> None:
        self.assertEqual(_failure_code(URLError("offline")), 4)
        self.assertEqual(_failure_code(RuntimeError("API key is required for real publishing")), 3)

        try:
            raise RuntimeError("PDF download failed") from URLError("offline")
        except RuntimeError as wrapped:
            self.assertEqual(_failure_code(wrapped), 4)


if __name__ == "__main__":
    unittest.main()
