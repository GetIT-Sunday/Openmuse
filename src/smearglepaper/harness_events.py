"""The append-only event contract shared by the conversation and runtime layers.

The durable runtime keeps its historical ``RunEvent`` shape for compatibility,
while the conversation layer keeps the ``TurnEvent`` name used by clients. Both
are normalized to :class:`HarnessEvent` at the UI and replay boundary.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .turn_store import fcntl


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class HarnessEvent:
    """A serializable event in one session/turn event stream.

    The first seven fields intentionally preserve the old ``TurnEvent``
    positional constructor. New producers should use :meth:`create`.
    """

    type: str
    session_id: str
    turn_id: str
    sequence: int
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    id: str = ""
    run_id: str = ""
    agent_id: str = ""
    schema_version: int = 1

    @classmethod
    def create(
        cls,
        event_type: str,
        *,
        session_id: str,
        turn_id: str,
        sequence: int,
        source: str,
        payload: dict[str, Any] | None = None,
        run_id: str = "",
        agent_id: str = "",
        event_id: str | None = None,
    ) -> HarnessEvent:
        return cls(
            type=event_type,
            session_id=session_id,
            turn_id=turn_id,
            sequence=sequence,
            source=source,
            payload=dict(payload or {}),
            timestamp=utc_now(),
            id=event_id or uuid.uuid4().hex,
            run_id=run_id,
            agent_id=agent_id,
        )

    @property
    def event_id(self) -> str:
        """Protocol spelling for consumers that do not use the legacy ``id``."""
        return self.id

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "event_id": self.id,
            "schema_version": self.schema_version,
            "type": self.type,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "sequence": self.sequence,
            "source": self.source,
            "payload": self.payload,
            "timestamp": self.timestamp or utc_now(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> HarnessEvent:
        return cls(
            type=str(value.get("type", "")),
            session_id=str(value.get("session_id", "")),
            turn_id=str(value.get("turn_id", "")),
            sequence=int(value.get("sequence", 0)),
            source=str(value.get("source", "unknown")),
            payload=dict(value.get("payload", {})) if isinstance(value.get("payload", {}), dict) else {},
            timestamp=str(value.get("timestamp", "")),
            id=str(value.get("id") or value.get("event_id") or uuid.uuid4().hex),
            run_id=str(value.get("run_id", "")),
            agent_id=str(value.get("agent_id", "")),
            schema_version=int(value.get("schema_version", 1)),
        )

    def with_sequence(self, sequence: int) -> HarnessEvent:
        return replace(self, sequence=sequence)


def normalize_event(event: object) -> HarnessEvent:
    """Convert a TurnEvent/RunEvent or serialized event to one protocol type."""
    if isinstance(event, HarnessEvent):
        return event
    if isinstance(event, dict):
        return HarnessEvent.from_dict(event)
    value = getattr(event, "to_harness_event", None)
    if callable(value):
        normalized = value()
        if isinstance(normalized, HarnessEvent):
            return normalized
    payload = getattr(event, "payload", {})
    return HarnessEvent(
        type=str(getattr(event, "type", "")),
        session_id=str(getattr(event, "session_id", "")),
        turn_id=str(getattr(event, "turn_id", "")),
        sequence=int(getattr(event, "sequence", 0)),
        source=str(getattr(event, "source", "unknown")),
        payload=dict(payload) if isinstance(payload, dict) else {},
        timestamp=str(getattr(event, "timestamp", "")),
        id=str(getattr(event, "id", "") or uuid.uuid4().hex),
        run_id=str(getattr(event, "run_id", "")),
        agent_id=str(getattr(event, "agent_id", "")),
    )


class EventJournal:
    """Indexed append-only journal; tolerate only an incomplete final crash row."""

    _locks: dict[str, threading.RLock] = {}

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = self._locks.setdefault(str(path.resolve()), threading.RLock())
        self._rows: list[HarnessEvent] = []
        self._ids: dict[str, HarnessEvent] = {}
        self._stamp: tuple[int, int, int] | None = None
        self._tail = b""
        self._valid_bytes = 0

    def _signature(self) -> tuple[int, int, int] | None:
        if not self.path.exists():
            return None
        stat = self.path.stat()
        return stat.st_ino, stat.st_size, stat.st_mtime_ns

    def _load(self) -> None:
        stamp = self._signature()
        if stamp == self._stamp:
            return
        rows = []
        self._tail = b""
        self._valid_bytes = 0
        data = self.path.read_bytes() if stamp else b""
        lines = data.splitlines(keepends=True)
        for index, line in enumerate(lines):
            try:
                if line.strip():
                    rows.append(HarnessEvent.from_dict(json.loads(line)))
            except (UnicodeDecodeError, json.JSONDecodeError):
                if index != len(lines) - 1 or line.endswith(b"\n"):
                    raise ValueError("会话事件记录中间损坏，已停止回放以避免错误恢复。") from None
                self._tail = line
                break
            except (AttributeError, TypeError, ValueError):
                raise ValueError("会话事件记录格式无效，已停止回放并保留原文件。") from None
            self._valid_bytes += len(line)
        self._rows = rows
        self._ids = {row.id: row for row in rows}
        self._stamp = stamp

    def append(self, event: HarnessEvent) -> HarnessEvent:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.with_suffix(".journal.lock").open("a+") as lock_file:
                if fcntl:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    self._load()
                    if event.id in self._ids:
                        return self._ids[event.id]
                    if self._tail:
                        # Preserve evidence of the interrupted write before repair.
                        self.path.with_name(self.path.name + f".torn-{uuid.uuid4().hex}").write_bytes(self._tail)
                        with self.path.open("r+b") as handle:
                            handle.truncate(self._valid_bytes)
                    persisted = event.with_sequence((self._rows[-1].sequence if self._rows else 0) + 1)
                    with self.path.open("ab") as handle:
                        # Old complete JSON without newline is valid too.
                        if self.path.stat().st_size and not self._tail:
                            with self.path.open("rb") as reader:
                                reader.seek(-1, 2)
                                if reader.read(1) != b"\n":
                                    handle.write(b"\n")
                        handle.write((json.dumps(persisted.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n").encode())
                        handle.flush()
                        os.fsync(handle.fileno())
                    self._rows.append(persisted)
                    self._ids[persisted.id] = persisted
                    self._stamp = self._signature()
                    self._tail = b""
                    return persisted
                finally:
                    if fcntl:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def events(self, after: int = 0) -> list[HarnessEvent]:
        with self._lock:
            self._load()
            return [row for row in self._rows if row.sequence > after]
