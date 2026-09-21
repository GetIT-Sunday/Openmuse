"""Atomic turn snapshots and write-ahead tool receipts, scoped to one session."""
from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

_active_call: ContextVar[dict[str, Any] | None] = ContextVar("openmuse_active_call", default=None)


def operation_id() -> str:
    call = _active_call.get()
    return f"{call['turn_id']}:{call['call_id']}" if call else ""


@contextmanager
def tool_scope(turn_id: str, call_id: str, record: dict[str, Any], persist: Any) -> Iterator[None]:
    token = _active_call.set({"turn_id": turn_id, "call_id": call_id, "record": record, "persist": persist})
    try:
        yield
    finally:
        _active_call.reset(token)


def bind_target(run_id: str) -> None:
    call = _active_call.get()
    if call:
        call["record"]["target_run_id"] = run_id
        call["persist"]()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class RecoveryRequired(RuntimeError):
    """An operation must not be replayed until its outcome is known."""


class TurnStore:
    _locks: dict[str, Any] = {}

    def __init__(self, path: Path, session_id: str) -> None:
        self.path = path
        self.session_id = session_id
        self._lock = self._locks.setdefault(str(path.resolve()), threading.Lock())

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RecoveryRequired("会话恢复记录损坏；已保留原文件，不会自动重做工具操作。") from exc
        if not isinstance(value, dict) or value.get("session_id") != self.session_id or value.get("schema_version") != 1:
            raise RecoveryRequired("恢复记录与当前会话不匹配或版本不支持。")
        turn_id = str(value.get("turn_id", ""))
        if not turn_id or not turn_id.replace("-", "").replace("_", "").isalnum():
            raise RecoveryRequired("恢复记录的对话标识无效。")
        if value.get("phase") not in {"model", "tools", "answer"} or not isinstance(value.get("messages"), list):
            raise RecoveryRequired("恢复记录缺少必要的阶段或消息信息。")
        if (value.get("status") not in {"running", "completed", "failed", "cancelled", "recovery_required", "abandoned"}
                or not isinstance(value.get("user_text"), str)
                or type(value.get("round")) is not int or value["round"] < 0
                or type(value.get("sequence", 0)) is not int
                or not all(isinstance(m, dict) for m in value["messages"])
                or (value["phase"] == "answer" and not isinstance(value.get("reply"), str))):
            raise RecoveryRequired("恢复记录的状态或消息信息无效；不会自动执行。")
        for key in ("calls", "tool_history"):
            records = value.get(key)
            if not isinstance(records, list):
                raise RecoveryRequired("恢复记录缺少工具执行凭据。")
            for record in records:
                if (not isinstance(record, dict) or not isinstance(record.get("call_id"), str)
                        or not isinstance(record.get("name"), str)
                        or not isinstance(record.get("arguments"), dict)
                        or record.get("operation_id") != f"{turn_id}:{record.get('call_id')}"
                        or record.get("state") not in {"pending", "running", "completed", "failed", "blocked"}
                        or (record["state"] in {"completed", "failed", "blocked"} and not isinstance(record.get("output"), str))):
                    raise RecoveryRequired("工具执行凭据损坏；不会重复执行结果未知的操作。")
        return value

    def save(self, value: dict[str, Any]) -> None:
        atomic_json(self.path, {**value, "schema_version": 1, "session_id": self.session_id})

    def begin(self, value: dict[str, Any]) -> None:
        previous = self.load()
        if previous:
            archive = self.path.with_suffix(".history") / f"{previous['turn_id']}.json"
            atomic_json(archive, previous)
        self.save(value)

    def pending(self) -> bool:
        return self.load().get("status") in {"running", "recovery_required"}

    @contextmanager
    def lease(self) -> Iterator[None]:
        if not self._lock.acquire(blocking=False):
            raise RecoveryRequired("此会话仍在执行，不能同时恢复。")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.with_suffix(".lock").open("a+") as handle:
                if fcntl:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError as exc:
                        raise RecoveryRequired("此会话正在另一个进程中执行。") from exc
                try:
                    yield
                finally:
                    if fcntl:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._lock.release()
