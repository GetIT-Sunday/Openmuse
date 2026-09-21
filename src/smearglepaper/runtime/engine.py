from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import copy_context
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

from ..cancellation import CancellationToken, Cancelled, cancellation_scope, current_token
from ..harness_events import EventJournal, HarnessEvent, normalize_event
from ..turn_store import RecoveryRequired
from ..workflow import SmearglePaperWorkflow
from .config import resolve_workspace
from .models import (
    RunEvent,
    RunRequest,
    RunResult,
    RunStatus,
    StepStatus,
    ToolContext,
    ToolOutcome,
    WorkflowDefinition,
    utc_now,
)
from .registry import ToolRegistry
from .store import RunStore
from .workflows import QualityGateError, build_registry, built_in_workflows

EventHandler = Callable[[RunEvent], None]
_DATA_DIR_LOCK = threading.RLock()


class RuntimeErrorCode(RuntimeError):
    def __init__(self, message: str, exit_code: int = 5) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class AgentRuntime:
    def __init__(
        self,
        workspace: Path | str | None = None,
        *,
        workflow: SmearglePaperWorkflow | None = None,
        registry: ToolRegistry | None = None,
        workflows: dict[str, WorkflowDefinition] | None = None,
    ) -> None:
        self.workspace = resolve_workspace(workspace)
        self.store = RunStore(self.workspace)
        self.workflow = workflow or SmearglePaperWorkflow()
        self.registry = registry or build_registry()
        self.workflows = workflows or built_in_workflows()
        self._subscribers: dict[str, list[EventHandler]] = {}
        self._session_journals: dict[str, EventJournal] = {}
        self._mutex = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}
        self._cancellations: dict[str, CancellationToken] = {}

    def attach_event_journal(self, session_id: str, journal: EventJournal) -> None:
        """Mirror normalized Runtime events into a session-wide event stream."""
        with self._mutex:
            self._session_journals[session_id] = journal

    def create_run(self, request: RunRequest, *, session_id: str | None = None) -> RunResult:
        if request.workflow not in self.workflows:
            raise RuntimeErrorCode(f"Unknown workflow: {request.workflow}", 2)
        if request.workspace and resolve_workspace(request.workspace) != self.workspace:
            raise RuntimeErrorCode("RunRequest workspace must match the AgentRuntime workspace.", 2)
        run_id = request.resume_from or _run_id()
        if request.resume_from:
            return self.result(run_id)
        definition = self.workflows[request.workflow]
        now = utc_now()
        manifest: dict[str, Any] = {
            "schema_version": 2,
            "run_id": run_id,
            "session_id": session_id or uuid.uuid4().hex[:12],
            "workflow": request.workflow,
            "status": RunStatus.PENDING.value,
            "request": request.to_dict(),
            "created_at": now,
            "updated_at": now,
            "last_sequence": 0,
            "cancel_requested": False,
            "steps": [
                {
                    "id": step.id,
                    "tool": step.tool,
                    "agent": step.agent,
                    "status": StepStatus.PENDING.value,
                    "attempts": 0,
                    "input_hash": None,
                    "artifacts": [],
                    "error": None,
                    "started_at": None,
                    "completed_at": None,
                }
                for step in definition.steps
            ],
            "artifacts": [],
            "approvals": {},
            "interaction": None,
            "quality": {},
            "usage": {"input_tokens": 0, "output_tokens": 0, "cost": None},
            "next_actions": ["Run the workflow"],
        }
        self.store.initialize(
            run_id,
            manifest,
            {"session_id": manifest["session_id"], "messages": [], "created_at": now},
        )
        return self.result(run_id)

    def run(self, request: RunRequest, *, session_id: str | None = None, event_handler: EventHandler | None = None) -> RunResult:
        created = self.create_run(request, session_id=session_id)
        if event_handler:
            self.subscribe(created.run_id, event_handler)
        return self.execute(created.run_id)

    def start(self, request: RunRequest, *, session_id: str | None = None, event_handler: EventHandler | None = None) -> RunResult:
        created = self.create_run(request, session_id=session_id)
        if event_handler:
            self.subscribe(created.run_id, event_handler)
        parent_context = copy_context()
        thread = threading.Thread(target=parent_context.run, args=(self.execute, created.run_id), daemon=True, name=f"smearglepaper-{created.run_id}")
        self._threads[created.run_id] = thread
        thread.start()
        return created

    def execute(self, run_id: str, *, _resuming: bool = False) -> RunResult:
        with self.store.execution_lock(run_id):
            manifest = self.store.load(run_id)
            if _resuming:
                if (manifest["status"] == RunStatus.WAITING_INPUT.value
                        and (manifest.get("interaction") or {}).get("status") == "pending"):
                    return self.result(run_id)
                if (manifest["status"] == RunStatus.WAITING_APPROVAL.value
                        and any(a.get("status") == "pending" for a in manifest.get("approvals", {}).values())):
                    return self.result(run_id)
                manifest["cancel_requested"] = False
            request = RunRequest.from_dict(dict(manifest["request"]))
            definition = self.workflows[str(manifest["workflow"])]
            if manifest["status"] == RunStatus.COMPLETED.value:
                return self.result(run_id)
            if self._uncertain_effect(manifest):
                self._require_recovery(manifest)
                return self.result(run_id)
            manifest["cancel_requested"] = False if manifest["status"] == RunStatus.CANCELLED.value else manifest.get("cancel_requested", False)
            self._set_run_status(manifest, RunStatus.RUNNING, ["Wait for workflow completion"])
            self._emit(manifest, "main", "run.started", {"workflow": definition.id})
            outputs = self._load_outputs(run_id, definition)
            active_agent = ""
            token = CancellationToken(parent=current_token())
            self._cancellations[run_id] = token
            try:
                for definition_step in definition.steps:
                    manifest = self.store.load(run_id)
                    request = RunRequest.from_dict(dict(manifest["request"]))
                    step = _step(manifest, definition_step.id)
                    if manifest.get("cancel_requested"):
                        self._set_run_status(manifest, RunStatus.CANCELLED, [f"Resume run {run_id}"])
                        self._emit(manifest, active_agent or "main", "run.status_changed", {"status": RunStatus.CANCELLED.value})
                        return self.result(run_id)
                    token.check()

                    if definition_step.condition and not definition_step.condition(request):
                        step["status"] = StepStatus.SKIPPED.value
                        step["completed_at"] = utc_now()
                        self.store.save(run_id, manifest)
                        self._emit(manifest, definition_step.agent, "step.completed", {"step": definition_step.id, "status": "skipped"})
                        continue

                    input_hash = self._input_hash(request, definition_step, manifest)
                    if self._can_resume(step, input_hash, manifest):
                        checkpoint = self.store.load_checkpoint(run_id, definition_step.id)
                        outputs[definition_step.id] = checkpoint.get("output", {})
                        if checkpoint.get("interaction") and (manifest.get("interaction") or {}).get("status") != "resolved":
                            self._wait_for_interaction(manifest, definition_step, checkpoint["interaction"])
                            return self.result(run_id)
                        self._emit(manifest, definition_step.agent, "step.completed", {"step": definition_step.id, "status": "cached"})
                        continue

                    spec, _ = self.registry.get(definition_step.tool)
                    if (step.get("external_effect") == "completed" and spec.side_effect
                            and (spec.approval != "real" or not request.dry_run)):
                        step["external_effect"] = "unknown"
                        self.store.save(run_id, manifest)
                        raise RecoveryRequired("已执行的外部操作无法验证本地凭据；禁止自动重复执行。")

                    if definition_step.approval == "real" and not request.dry_run:
                        approval_id = f"approve-{definition_step.id}"
                        approval = manifest["approvals"].get(approval_id)
                        if not approval or approval.get("decision") != "approved":
                            manifest["approvals"][approval_id] = {
                                "id": approval_id,
                                "step": definition_step.id,
                                "status": "pending",
                                "decision": None,
                                "requested_at": utc_now(),
                                "side_effect": "Create a real WeChat draft",
                                "target": "configured WeChat Official Account",
                                "artifact_paths": [
                                    item["path"]
                                    for item in manifest.get("artifacts", [])
                                    if item.get("producer") in definition_step.dependencies and item.get("status") != "stale"
                                ],
                                "quality": dict(manifest.get("quality", {})),
                                "reversible": "The draft can be deleted in WeChat; no article is published by this step.",
                            }
                            step["status"] = StepStatus.WAITING_APPROVAL.value
                            self._set_run_status(manifest, RunStatus.WAITING_APPROVAL, [f"Approve {approval_id}"])
                            self._emit(manifest, definition_step.agent, "approval.required", manifest["approvals"][approval_id])
                            return self.result(run_id)

                    if active_agent != definition_step.agent:
                        if active_agent:
                            self._emit(manifest, active_agent, "agent.completed", {"agent": active_agent})
                        active_agent = definition_step.agent
                        self._emit(manifest, active_agent, "agent.started", {"agent": active_agent})

                    outcome = self._execute_step(manifest, request, definition_step, outputs, input_hash)
                    outputs[definition_step.id] = outcome.output
                    if outcome.interaction:
                        manifest = self.store.load(run_id)
                        self._wait_for_interaction(manifest, definition_step, outcome.interaction)
                        return self.result(run_id)

                if active_agent:
                    self._emit(manifest, active_agent, "agent.completed", {"agent": active_agent})
                manifest = self.store.load(run_id)
                if manifest.get("cancel_requested"):
                    token.cancel()
                token.check()
                manifest["revision_backup"] = []
                manifest["fallback_revision_active"] = False
                self._set_run_status(manifest, RunStatus.COMPLETED, ["Inspect generated artifacts"])
                self._emit(manifest, "main", "run.completed", {"status": RunStatus.COMPLETED.value})
            except RecoveryRequired:
                manifest = self.store.load(run_id)
                self._require_recovery(manifest)
            except Cancelled:
                manifest = self.store.load(run_id)
                for step in manifest["steps"]:
                    if step["status"] in {StepStatus.RUNNING.value, StepStatus.RETRYING.value}:
                        step["status"] = StepStatus.PENDING.value
                self._restore_revision_backup(manifest)
                self._set_run_status(manifest, RunStatus.CANCELLED, [f"Resume run {run_id}"])
                self._emit(manifest, active_agent or "main", "run.cancelled", {"reason": "cancelled"})
            except QualityGateError as exc:
                manifest = self.store.load(run_id)
                self._restore_revision_backup(manifest)
                self._set_run_status(manifest, RunStatus.FAILED, ["Review quality reports and retry the review step"])
                manifest["failure_code"] = 6
                self.store.save(run_id, manifest)
                self._emit(manifest, active_agent or "main", "run.failed", {"error": str(exc), "code": 6})
            except Exception as exc:
                manifest = self.store.load(run_id)
                self._restore_revision_backup(manifest)
                self._set_run_status(manifest, RunStatus.FAILED, ["Inspect the failed step and retry"])
                manifest["failure_code"] = _failure_code(exc)
                self.store.save(run_id, manifest)
                self._emit(
                    manifest,
                    active_agent or "main",
                    "run.failed",
                    {"error": str(exc), "code": manifest["failure_code"]},
                )
            finally:
                self._cancellations.pop(run_id, None)
            return self.result(run_id)

    def resume(self, run_id: str) -> RunResult:
        # Reset cancellation only while holding the execution lease. A second
        # process must not overwrite a live worker's write-ahead marker.
        return self.execute(run_id, _resuming=True)

    def _wait_for_interaction(self, manifest: dict[str, Any], step: Any, value: dict[str, Any]) -> None:
        interaction = {**value, "id": f"interaction-{step.id}", "step": step.id,
                       "status": "pending", "selected_id": None, "requested_at": utc_now()}
        manifest["interaction"] = interaction
        self._set_run_status(manifest, RunStatus.WAITING_INPUT, ["选择一篇论文继续"])
        self._emit(manifest, step.agent, "interaction.required", interaction)

    def retry(self, run_id: str, step_id: str | None = None) -> RunResult:
        with self.store.execution_lock(run_id):
            result = self._prepare_retry(run_id, step_id)
            if result:
                return result
        return self.execute(run_id)

    def _prepare_retry(self, run_id: str, step_id: str | None) -> RunResult | None:
        manifest = self.store.load(run_id)
        if self._uncertain_effect(manifest):
            self._require_recovery(manifest)
            return self.result(run_id)
        definition = self.workflows[str(manifest["workflow"])]
        target = step_id or next((item["id"] for item in manifest["steps"] if item["status"] == StepStatus.FAILED.value), None)
        if not target:
            raise RuntimeErrorCode("No failed step to retry.", 2)
        ids = [step.id for step in definition.steps]
        if target not in ids:
            raise RuntimeErrorCode(f"Unknown step: {target}", 2)
        start = ids.index(target)
        request = RunRequest.from_dict(manifest["request"])
        for item in manifest["steps"][start:]:
            spec, _ = self.registry.get(str(item["tool"]))
            if (item.get("external_effect") == "completed" and spec.side_effect
                    and (spec.approval != "real" or not request.dry_run)):
                raise RuntimeErrorCode("此步骤已完成外部写入，不能重放；如需新的操作，请新建任务并重新审批。", 2)
        stale_producers = set(ids[start:])
        for item in manifest["steps"][start:]:
            item.update({"status": StepStatus.PENDING.value, "error": None, "completed_at": None, "input_hash": None, "artifacts": []})
        for artifact in manifest.get("artifacts", []):
            if artifact.get("producer") in stale_producers:
                artifact["status"] = "stale"
        manifest["status"] = RunStatus.PENDING.value
        manifest["failure_code"] = None
        self.store.save(run_id, manifest)
        return None

    def cancel(self, run_id: str) -> RunResult:
        manifest = self.store.load(run_id)
        if manifest["status"] in {RunStatus.COMPLETED.value, RunStatus.FAILED.value, RunStatus.CANCELLED.value, RunStatus.RECOVERY_REQUIRED.value}:
            return self.result(run_id)
        token = self._cancellations.get(run_id)
        manifest["cancel_requested"] = True
        manifest["status"] = RunStatus.CANCELLING.value
        self.store.save(run_id, manifest)
        try:
            self._emit(manifest, "main", "run.status_changed", {"status": RunStatus.CANCELLING.value})
        finally:
            # Publish the request before waking a cooperative worker: it may
            # immediately persist its terminal state when the token is signalled.
            if token:
                token.cancel()
        return self.result(run_id)

    def resolve_approval(self, run_id: str, approval_id: str, approve: bool) -> RunResult:
        manifest = self.store.load(run_id)
        approval = manifest.get("approvals", {}).get(approval_id)
        if not approval:
            raise RuntimeErrorCode(f"Approval not found: {approval_id}", 2)
        if approval.get("status") == "resolved":
            return self.result(run_id)
        approval.update({"status": "resolved", "decision": "approved" if approve else "rejected", "resolved_at": utc_now()})
        step = _step(manifest, str(approval["step"]))
        step["status"] = StepStatus.PENDING.value if approve else StepStatus.SKIPPED.value
        manifest["status"] = RunStatus.PENDING.value if approve else RunStatus.CANCELLED.value
        manifest["next_actions"] = [f"Resume run {run_id}"] if approve else ["Review the rejected publication request"]
        self.store.save(run_id, manifest)
        self._emit(manifest, str(step["agent"]), "approval.resolved", dict(approval))
        return self.result(run_id)

    def resolve_interaction(self, run_id: str, selected_id: str, *, operation_id: str = "") -> RunResult:
        manifest = self.store.load(run_id)
        interaction = manifest.get("interaction")
        if not isinstance(interaction, dict) or interaction.get("status") != "pending":
            raise RuntimeErrorCode("No pending user selection is available.", 2)
        options = list(interaction.get("options", []))
        selected = next(
            (item for item in options if isinstance(item, dict) and str(item.get("paper_id")) == selected_id),
            None,
        )
        if selected is None:
            raise RuntimeErrorCode(f"Candidate not found: {selected_id}", 2)
        request = dict(manifest["request"])
        inputs = dict(request.get("inputs", {}))
        inputs["paper_id"] = selected_id
        if operation_id:
            inputs["_operation_id"] = operation_id
        request["inputs"] = inputs
        manifest["request"] = request
        interaction.update(
            {
                "status": "resolved",
                "selected_id": selected_id,
                "selected_title": selected.get("title", ""),
                "resolved_at": utc_now(),
            }
        )
        manifest["status"] = RunStatus.PENDING.value
        manifest["next_actions"] = ["继续理解所选论文"]
        self.store.save(run_id, manifest)
        self._emit(manifest, "scout", "interaction.resolved", dict(interaction))
        return self.result(run_id)

    def prepare_article(self, run_id: str, *, model: str | None = None,
                        inputs: dict[str, Any] | None = None) -> RunResult:
        """Extend completed research without re-running trusted research steps."""
        with self.store.execution_lock(run_id):
            manifest = self.store.load(run_id)
            if manifest["workflow"] != "paper-research" or manifest["status"] != RunStatus.COMPLETED.value:
                raise RuntimeErrorCode("请先完成论文研究；已有文章请使用修订操作。", 2)
            if any(not self.store.verify_artifact(item) for item in manifest.get("artifacts", []) if item.get("status") != "stale"):
                raise RuntimeErrorCode("研究产物缺失或已被修改，请先恢复研究材料。", 2)
            manifest["workflow"] = "paper-to-article"
            manifest["request"].update(workflow="paper-to-article", dry_run=True)
            manifest["request"]["inputs"].update(inputs or {})
            if model:
                manifest["request"]["model"] = model
            request = RunRequest.from_dict(manifest["request"])
            existing = {item["id"]: item for item in manifest["steps"]}
            for definition in self.workflows["paper-to-article"].steps:
                if definition.id in existing:
                    # The workflow name/model is part of the cache key, but
                    # neither changes the already verified research outputs.
                    existing[definition.id]["input_hash"] = self._input_hash(request, definition, manifest)
                else:
                    manifest["steps"].append({"id": definition.id, "tool": definition.tool,
                        "agent": definition.agent, "status": StepStatus.PENDING.value,
                        "attempts": 0, "input_hash": None, "artifacts": [], "error": None,
                        "started_at": None, "completed_at": None})
            manifest["cancel_requested"] = False
            self._set_run_status(manifest, RunStatus.PENDING, ["使用已确认的论文证据撰写文章"])
        return self.result(run_id)

    def update_inputs(self, run_id: str, updates: dict[str, Any], *, restart_step: str = "write",
                      model: str | None = None, dry_run: bool | None = None) -> RunResult:
        manifest = self.store.load(run_id)
        if self._uncertain_effect(manifest):
            raise RecoveryRequired("外部写入结果尚未核对，不能通过修订或更新输入跳过恢复检查。")
        previous_request = RunRequest.from_dict(manifest["request"])
        request = dict(manifest["request"])
        request_inputs = dict(request.get("inputs", {}))
        request_inputs.update(updates)
        request["inputs"] = request_inputs
        if model:
            request["model"] = model
        if dry_run is not None:
            request["dry_run"] = dry_run
        manifest["request"] = request
        definition = self.workflows[str(manifest["workflow"])]
        ids = [step.id for step in definition.steps]
        start = ids.index(restart_step) if restart_step in ids else 0
        if model and model != previous_request.model:
            # Model selection only affects the steps being restarted. Preserve
            # cache keys for verified upstream work, not changed input values.
            previous_request.model = model
            for definition_step in definition.steps[:start]:
                item = _step(manifest, definition_step.id)
                if item["status"] == StepStatus.COMPLETED.value:
                    item["input_hash"] = self._input_hash(previous_request, definition_step, manifest)
        stale_producers = set(ids[start:])
        if "choose" in ids and start <= ids.index("choose"):
            manifest["interaction"] = None
        if updates.get("revision_instruction"):
            manifest["revision_backup"] = [
                item.get("id")
                for item in manifest.get("artifacts", [])
                if item.get("producer") in stale_producers and item.get("status") != "stale"
            ]
        active = next(
            (item for item in manifest["steps"] if item["status"] in {StepStatus.RUNNING.value, StepStatus.RETRYING.value}),
            None,
        )
        if active and ids.index(str(active["id"])) >= start:
            manifest["cancel_requested"] = True
            manifest["restart_requested"] = True
            manifest["status"] = RunStatus.CANCELLING.value
        for item in manifest["steps"][start:]:
            item.update({"status": StepStatus.PENDING.value, "input_hash": None, "error": None, "completed_at": None, "artifacts": []})
        for artifact in manifest.get("artifacts", []):
            if artifact.get("producer") in stale_producers:
                artifact["status"] = "stale"
        if not active or ids.index(str(active["id"])) < start:
            manifest["status"] = RunStatus.RUNNING.value if active else RunStatus.PENDING.value
        self.store.save(run_id, manifest)
        self._emit(manifest, "main", "agent.message", {"message": "Updated run inputs; downstream steps are stale.", "updates": updates})
        return self.result(run_id)

    def _restore_revision_backup(self, manifest: dict[str, Any]) -> None:
        backup_ids = set(manifest.get("revision_backup", []))
        if not backup_ids:
            return
        for artifact in manifest.get("artifacts", []):
            if artifact.get("id") in backup_ids:
                artifact["status"] = "created"
            elif artifact.get("producer") in {"write", "review", "package", "publish"}:
                artifact["status"] = "stale"
        manifest["fallback_revision_active"] = True
        self.store.save(str(manifest["run_id"]), manifest)

    def get_run(self, run_id: str, *, include_events: bool = False) -> dict[str, Any]:
        value = self.store.load(run_id)
        if include_events:
            value["events"] = [event.to_dict() for event in self.store.events(run_id)]
        return value

    def list_runs(self) -> list[dict[str, Any]]:
        return self.store.list_runs()

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        return list(self.store.load(run_id).get("artifacts", []))

    def events(self, run_id: str, after: int = 0) -> list[RunEvent]:
        return self.store.events(run_id, after)

    def harness_events(self, run_id: str, after: int = 0) -> list[HarnessEvent]:
        """Return runtime history through the shared Harness event contract."""
        return [normalize_event(event) for event in self.store.events(run_id, after)]

    def subscribe(self, run_id: str, handler: EventHandler) -> Callable[[], None]:
        with self._mutex:
            self._subscribers.setdefault(run_id, []).append(handler)

        def unsubscribe() -> None:
            with self._mutex:
                handlers = self._subscribers.get(run_id, [])
                if handler in handlers:
                    handlers.remove(handler)

        return unsubscribe

    def result(self, run_id: str) -> RunResult:
        manifest = self.store.load(run_id)
        return RunResult(
            run_id=run_id,
            status=str(manifest["status"]),
            artifacts=list(manifest.get("artifacts", [])),
            quality=dict(manifest.get("quality", {})),
            next_actions=list(manifest.get("next_actions", [])),
        )

    def _execute_step(
        self,
        manifest: dict[str, Any],
        request: RunRequest,
        definition_step: Any,
        outputs: dict[str, dict[str, Any]],
        input_hash: str,
    ) -> ToolOutcome:
        run_id = str(manifest["run_id"])
        step = _step(manifest, definition_step.id)
        spec, handler = self.registry.get(definition_step.tool)
        attempts = max(1, definition_step.max_retries + 1)
        stale_ids: set[str] = set(step.get("artifacts", []))
        for record in manifest.get("artifacts", []):
            if record.get("id") in stale_ids:
                record["status"] = "stale"
        step["artifacts"] = []
        self.store.save(run_id, manifest)
        for attempt in range(attempts):
            token = self._cancellations.get(run_id) or CancellationToken(parent=current_token())
            if self.store.load(run_id).get("cancel_requested"):
                token.cancel()
            token.check()
            step["status"] = StepStatus.RUNNING.value if attempt == 0 else StepStatus.RETRYING.value
            step["attempts"] = int(step.get("attempts", 0)) + 1
            step["started_at"] = step.get("started_at") or utc_now()
            step["input_hash"] = input_hash
            step["error"] = None
            self.store.save(run_id, manifest)
            self._emit(manifest, definition_step.agent, "step.started" if attempt == 0 else "step.retrying", {"step": definition_step.id, "attempt": attempt + 1})
            self._emit(manifest, definition_step.agent, "tool.started", {"step": definition_step.id, "tool": spec.name})
            started = time.monotonic()
            try:
                current_manifest = manifest
                current_agent = definition_step.agent

                def emit_tool_event(
                    event_type: str,
                    payload: dict[str, Any] | None = None,
                    *,
                    _manifest: dict[str, Any] = current_manifest,
                    _agent: str = current_agent,
                ) -> None:
                    self._emit(_manifest, _agent, event_type, payload or {})

                context = ToolContext(
                    run_id=run_id,
                    request=request,
                    step=definition_step,
                    outputs=outputs,
                    run_dir=self.store.run_dir(run_id),
                    emit=emit_tool_event,
                    workflow=self.workflow,
                    cancellation=token,
                )
                external = spec.side_effect and (spec.approval != "real" or not request.dry_run)
                if external:
                    step["external_effect"] = "started"
                    self.store.save(run_id, manifest)
                with cancellation_scope(token), _workspace_data_context(self.workspace):
                    outcome = handler(context)
                    token.check()
                artifact_records = []
                path_map: dict[str, str] = {}
                dependency_ids = [artifact["id"] for artifact in manifest.get("artifacts", []) if artifact.get("producer") in definition_step.dependencies and artifact.get("status") != "stale"]
                for raw_path in outcome.artifact_paths:
                    path = Path(raw_path)
                    if path.is_file():
                        record = self.store.add_artifact(run_id, path, definition_step.id, dependency_ids)
                        artifact_records.append(record)
                        path_map[str(path.resolve())] = record.path
                outcome.output = _rewrite_paths(outcome.output, path_map)
                checkpoint = {
                    "schema_version": 1,
                    "step": definition_step.id,
                    "input_hash": input_hash,
                    "output": outcome.output,
                    "quality": outcome.quality,
                    "interaction": outcome.interaction,
                    "completed_at": utc_now(),
                }
                checkpoint_path = self.store.checkpoint(run_id, definition_step.id, checkpoint)
                artifact_records.append(self.store.add_artifact(run_id, checkpoint_path, definition_step.id, dependency_ids, "application/json"))
                manifest = self.store.load(run_id)
                step = _step(manifest, definition_step.id)
                records = [record.to_dict() for record in artifact_records]
                manifest["artifacts"].extend(records)
                step["artifacts"] = [record["id"] for record in records]
                step["status"] = StepStatus.COMPLETED.value
                if external:
                    step["external_effect"] = "completed"
                step["completed_at"] = utc_now()
                step["duration_seconds"] = round(time.monotonic() - started, 3)
                if outcome.quality:
                    manifest["quality"].update(outcome.quality)
                if outcome.usage:
                    _merge_usage(manifest["usage"], outcome.usage.to_dict())
                self.store.save(run_id, manifest)
                for record in records:
                    self._emit(manifest, definition_step.agent, "artifact.created", record)
                if outcome.usage:
                    self._emit(manifest, definition_step.agent, "usage.updated", dict(manifest["usage"]))
                output_preview = json.dumps(outcome.output, ensure_ascii=False, default=str)
                self._emit(
                    manifest,
                    definition_step.agent,
                    "tool.completed",
                    {
                        "step": definition_step.id,
                        "tool": spec.name,
                        "duration_seconds": step["duration_seconds"],
                        "message": outcome.message,
                        "output_preview": output_preview[:2000],
                        "output_truncated": len(output_preview) > 2000,
                    },
                )
                self._emit(manifest, definition_step.agent, "step.completed", {"step": definition_step.id, "message": outcome.message})
                return outcome
            except Cancelled:
                if self._uncertain_effect(self.store.load(run_id)):
                    raise RecoveryRequired("取消时外部写入的结果未知，请先核对远端结果。") from None
                raise
            except Exception as exc:
                if self._uncertain_effect(self.store.load(run_id)):
                    raise RecoveryRequired("外部写入未获得可靠完成记录，请先核对远端结果。") from exc
                token.check()
                manifest = self.store.load(run_id)
                step = _step(manifest, definition_step.id)
                step["error"] = {"type": type(exc).__name__, "message": str(exc), "retryable": bool(spec.retryable)}
                self.store.save(run_id, manifest)
                self._emit(manifest, definition_step.agent, "tool.failed", {"step": definition_step.id, "tool": spec.name, "error": str(exc)})
                if attempt + 1 < attempts and spec.retryable:
                    token.wait(min(0.25 * (2**attempt), 2.0))
                    token.check()
                    continue
                step["status"] = StepStatus.FAILED.value
                step["completed_at"] = utc_now()
                self.store.save(run_id, manifest)
                self._emit(manifest, definition_step.agent, "step.failed", {"step": definition_step.id, "error": str(exc)})
                raise
        raise AssertionError("unreachable")

    def _uncertain_effect(self, manifest: dict[str, Any]) -> dict[str, Any] | None:
        request = RunRequest.from_dict(manifest["request"])
        for step in manifest["steps"]:
            spec, _ = self.registry.get(str(step["tool"]))
            if not spec.side_effect or (spec.approval == "real" and request.dry_run):
                continue
            if step.get("external_effect") in {"started", "unknown"} or (
                step.get("external_effect") is None and step["status"] in {"running", "retrying"}
            ):
                return step
        return None

    def _require_recovery(self, manifest: dict[str, Any]) -> None:
        step = self._uncertain_effect(manifest)
        if step is None:
            return
        step["external_effect"] = "unknown"
        manifest["recovery"] = {"kind": "external_result_unknown", "step": step["id"],
            "message": "外部写入结果未知。请先在微信草稿箱或目标服务核对，禁止自动重试。"}
        self._set_run_status(manifest, RunStatus.RECOVERY_REQUIRED, [manifest["recovery"]["message"]])
        self._emit(manifest, str(step["agent"]), "run.recovery_required", dict(manifest["recovery"]))

    def reconcile_external(self, run_id: str, *, executed: bool, external_id: str = "") -> RunResult:
        """Explicit human reconciliation only; never exposed as an LLM tool."""
        with self.store.execution_lock(run_id):
            manifest = self.store.load(run_id)
            step = self._uncertain_effect(manifest)
            if step is None:
                raise RuntimeErrorCode("没有等待核对的外部写入。", 2)
            if executed:
                if not external_id.strip():
                    raise RuntimeErrorCode("确认已执行时必须提供远端草稿或结果 ID。", 2)
                output = {"external_id": external_id, "draft": {"media_id": external_id}, "reconciled": True}
                checkpoint = self.store.checkpoint(run_id, str(step["id"]), {"output": output, "input_hash": step.get("input_hash"), "reconciled": True})
                old_ids = set(step.get("artifacts", []))
                for artifact in manifest.get("artifacts", []):
                    if artifact.get("id") in old_ids:
                        artifact["status"] = "stale"
                receipt = self.store.add_artifact(run_id, checkpoint, str(step["id"]))
                manifest["artifacts"].append(receipt.to_dict())
                step["artifacts"] = [receipt.id]
                step.update(status=StepStatus.COMPLETED.value, external_effect="completed", completed_at=utc_now())
            else:
                step.update(status=StepStatus.PENDING.value, external_effect="not_executed")
                manifest.get("approvals", {}).pop(f"approve-{step['id']}", None)
            manifest["recovery"] = None
            manifest["cancel_requested"] = False
            self._set_run_status(manifest, RunStatus.PENDING, ["已记录人工核对结果，可以显式继续任务。"])
            self._emit(manifest, str(step["agent"]), "run.reconciled", {"executed": executed, "external_id": external_id})
        return self.result(run_id)

    def _emit(self, manifest: dict[str, Any], agent_id: str, event_type: str, payload: dict[str, Any]) -> RunEvent:
        with self._mutex:
            latest = self.store.load(str(manifest["run_id"]))
            persisted_events = self.store.events(str(manifest["run_id"]))
            persisted_sequence = persisted_events[-1].sequence if persisted_events else 0
            latest["last_sequence"] = max(int(latest.get("last_sequence", 0)), persisted_sequence) + 1
            event = RunEvent.create(
                str(latest["run_id"]),
                str(latest["session_id"]),
                agent_id,
                event_type,
                int(latest["last_sequence"]),
                _redact(payload),
            )
            self.store.append_event(event)
            self.store.save(str(latest["run_id"]), latest)
            journal = self._session_journals.get(str(latest["session_id"]))
            if journal:
                journal.append(event.to_harness_event())
            manifest.update(latest)
            handlers = list(self._subscribers.get(event.run_id, []))
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                continue
        return event

    def _set_run_status(self, manifest: dict[str, Any], status: RunStatus, next_actions: list[str]) -> None:
        manifest["status"] = status.value
        manifest["next_actions"] = next_actions
        self.store.save(str(manifest["run_id"]), manifest)

    def _load_outputs(self, run_id: str, definition: WorkflowDefinition) -> dict[str, dict[str, Any]]:
        return {
            step.id: dict(checkpoint.get("output", {}))
            for step in definition.steps
            if (checkpoint := self.store.load_checkpoint(run_id, step.id))
        }

    def _input_hash(self, request: RunRequest, definition_step: Any, manifest: dict[str, Any]) -> str:
        dependency_artifacts = [
            {"id": artifact.get("id"), "sha256": artifact.get("sha256")}
            for artifact in manifest.get("artifacts", [])
            if artifact.get("producer") in definition_step.dependencies and artifact.get("status") != "stale"
        ]
        selected_inputs = (
            request.inputs
            if definition_step.input_keys is None
            else {key: request.inputs.get(key) for key in definition_step.input_keys if key in request.inputs}
        )
        payload = {
            "workflow": request.workflow,
            "model": request.model,
            "dry_run": request.dry_run if definition_step.approval == "real" else None,
            "inputs": selected_inputs,
            "step": definition_step.id,
            "dependencies": dependency_artifacts,
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def _can_resume(self, step: dict[str, Any], input_hash: str, manifest: dict[str, Any]) -> bool:
        if step.get("status") != StepStatus.COMPLETED.value or step.get("input_hash") != input_hash:
            return False
        if not self.store.load_checkpoint(str(manifest["run_id"]), str(step["id"])):
            return False
        ids = set(step.get("artifacts", []))
        if not ids:
            return True
        records = [item for item in manifest.get("artifacts", []) if item.get("id") in ids and item.get("status") != "stale"]
        return bool(records) and all(self.store.verify_artifact(item) for item in records)


def _run_id() -> str:
    return "run_" + utc_now().replace("-", "").replace(":", "").replace("+00:00", "Z").replace(".", "") + "_" + uuid.uuid4().hex[:6]


def _step(manifest: dict[str, Any], step_id: str) -> dict[str, Any]:
    for step in manifest["steps"]:
        if step["id"] == step_id:
            return step
    raise KeyError(step_id)


def _merge_usage(current: dict[str, Any], update: dict[str, Any]) -> None:
    current["input_tokens"] = int(current.get("input_tokens", 0)) + int(update.get("input_tokens", 0))
    current["output_tokens"] = int(current.get("output_tokens", 0)) + int(update.get("output_tokens", 0))
    if update.get("cost") is not None:
        current["cost"] = float(current.get("cost") or 0) + float(update["cost"])


def _redact(value: Any) -> Any:
    secrets = ("api_key", "app_secret", "token", "webhook", "authorization")
    if isinstance(value, dict):
        return {key: "***" if any(secret in key.lower() for secret in secrets) else _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _rewrite_paths(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _rewrite_paths(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_paths(item, mapping) for item in value]
    if isinstance(value, str):
        try:
            resolved = str(Path(value).resolve())
        except (OSError, ValueError):
            return value
        return mapping.get(resolved, value)
    return value


@contextmanager
def _workspace_data_context(workspace: Path):
    """Temporarily route legacy DATA_DIR globals into the Runtime workspace.

    The 0.2 runtime is deliberately single-process/single-active-run. The lock keeps
    legacy module globals safe while providers are migrated to explicit paths.
    """
    target = workspace / "data"
    target.mkdir(parents=True, exist_ok=True)
    replaced: list[tuple[Any, Any]] = []
    previous_env = os.environ.get("SMEARGLEPAPER_HOME")
    with _DATA_DIR_LOCK:
        os.environ["SMEARGLEPAPER_HOME"] = str(workspace)
        for name, module in tuple(sys.modules.items()):
            if not name.startswith("smearglepaper") or module is None or not hasattr(module, "DATA_DIR"):
                continue
            current = module.__dict__["DATA_DIR"]
            if isinstance(current, Path):
                replaced.append((module, current))
                module.__dict__["DATA_DIR"] = target
        try:
            yield
        finally:
            for module, value in replaced:
                module.__dict__["DATA_DIR"] = value
            if previous_env is None:
                os.environ.pop("SMEARGLEPAPER_HOME", None)
            else:
                os.environ["SMEARGLEPAPER_HOME"] = previous_env


def _failure_code(exc: Exception) -> int:
    if isinstance(exc, RuntimeErrorCode):
        return exc.exit_code
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, HTTPError | URLError | TimeoutError | ConnectionError):
            return 4
        current = current.__cause__ or current.__context__
    message = str(exc).lower()
    if any(term in message for term in ("api key", "app_secret", "not configured", "required for real")):
        return 3
    return 5
