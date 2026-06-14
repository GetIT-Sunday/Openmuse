from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

from .config import DATA_DIR
from .storage import read_json, write_json

SESSIONS_DIR = DATA_DIR / "sessions"
_timestamp_lock = Lock()
_last_timestamp = datetime.min.replace(tzinfo=timezone.utc)


def _now_iso() -> str:
    global _last_timestamp
    with _timestamp_lock:
        now = datetime.now(timezone.utc)
        if now <= _last_timestamp:
            now = _last_timestamp + timedelta(microseconds=1)
        _last_timestamp = now
        return now.isoformat()


def _ensure_dir() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Session:
    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[dict[str, object]] = field(default_factory=list)

    @staticmethod
    def create(title: str = "新对话") -> Session:
        now = _now_iso()
        return Session(
            id=uuid.uuid4().hex[:12],
            title=title,
            created_at=now,
            updated_at=now,
            messages=[],
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": self.messages,
        }

    @staticmethod
    def from_dict(data: dict[str, object]) -> Session:
        return Session(
            id=str(data.get("id", "")),
            title=str(data.get("title", "无标题")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            messages=list(data.get("messages", [])),  # type: ignore[arg-type]
        )

    def touch(self) -> None:
        self.updated_at = _now_iso()


def save_session(session: Session) -> Path:
    _ensure_dir()
    session.touch()
    path = SESSIONS_DIR / f"{session.id}.json"
    write_json(path, session.to_dict())
    return path


def load_session(session_id: str) -> Session:
    path = SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Session not found: {session_id}")
    return Session.from_dict(read_json(path, {}))


def list_sessions() -> list[Session]:
    _ensure_dir()
    sessions: list[Session] = []
    for path in SESSIONS_DIR.glob("*.json"):
        try:
            data = read_json(path, {})
            if data.get("id"):
                sessions.append(Session.from_dict(data))
        except Exception:
            continue
    sessions.sort(key=lambda session: session.updated_at, reverse=True)
    return sessions


def delete_session(session_id: str) -> None:
    path = SESSIONS_DIR / f"{session_id}.json"
    if path.exists():
        path.unlink()


def auto_title(messages: list[dict[str, object]]) -> str:
    for message in messages:
        if message.get("role") == "user":
            text = str(message.get("content", ""))
            return text[:30] + ("..." if len(text) > 30 else "")
    return "新对话"
