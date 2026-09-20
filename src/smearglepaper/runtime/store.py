from __future__ import annotations

import contextlib
import hashlib
import json
import mimetypes
import os
import shutil
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..storage import write_json
from .models import ArtifactRecord, RunEvent, utc_now

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]


class RunStore:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.runs_dir = workspace / "runs"
        self._lock = threading.RLock()

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def initialize(self, run_id: str, manifest: dict[str, Any], session: dict[str, Any]) -> Path:
        run_dir = self.run_dir(run_id)
        for name in ("artifacts", "logs", "checkpoints"):
            (run_dir / name).mkdir(parents=True, exist_ok=True)
        write_json(run_dir / "manifest.json", manifest)
        write_json(run_dir / "session.json", session)
        (run_dir / "events.jsonl").touch(exist_ok=True)
        return run_dir

    def load(self, run_id: str) -> dict[str, Any]:
        path = self.run_dir(run_id) / "manifest.json"
        if not path.exists():
            raise FileNotFoundError(f"Run not found: {run_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, run_id: str, manifest: dict[str, Any]) -> None:
        manifest["updated_at"] = utc_now()
        write_json(self.run_dir(run_id) / "manifest.json", manifest)

    @contextlib.contextmanager
    def execution_lock(self, run_id: str) -> Iterator[None]:
        lock_path = self.run_dir(run_id) / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as handle:
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeError(f"Run is already executing: {run_id}") from exc
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def append_event(self, event: RunEvent) -> None:
        line = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            with (self.run_dir(event.run_id) / "events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())

    def events(self, run_id: str, after: int = 0) -> list[RunEvent]:
        path = self.run_dir(run_id) / "events.jsonl"
        if not path.exists():
            return []
        rows: list[RunEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = RunEvent.from_dict(json.loads(line))
            if event.sequence > after:
                rows.append(event)
        return rows

    def checkpoint(self, run_id: str, step_id: str, payload: dict[str, Any]) -> Path:
        path = self.run_dir(run_id) / "checkpoints" / f"{step_id}.json"
        write_json(path, payload)
        return path

    def load_checkpoint(self, run_id: str, step_id: str) -> dict[str, Any]:
        path = self.run_dir(run_id) / "checkpoints" / f"{step_id}.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def add_artifact(
        self,
        run_id: str,
        source: Path,
        producer: str,
        dependencies: list[str] | None = None,
        artifact_type: str | None = None,
    ) -> ArtifactRecord:
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"Artifact does not exist: {source}")
        target_name = f"{producer}-{source.name}"
        target = self.run_dir(run_id) / "artifacts" / target_name
        if target.exists() and source.resolve() != target.resolve():
            source_key = hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()[:8]
            target = self.run_dir(run_id) / "artifacts" / f"{producer}-{source_key}-{source.name}"
        if source.resolve() != target.resolve():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        digest = sha256_file(target)
        return ArtifactRecord(
            id=uuid.uuid4().hex,
            type=artifact_type or _artifact_type(target),
            schema_version="1",
            path=str(target),
            sha256=digest,
            producer=producer,
            dependencies=dependencies or [],
            mime_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream",
        )

    def add_json_artifact(
        self,
        run_id: str,
        name: str,
        payload: Any,
        producer: str,
        dependencies: list[str] | None = None,
        artifact_type: str = "application/json",
    ) -> ArtifactRecord:
        target = self.run_dir(run_id) / "artifacts" / name
        write_json(target, payload)
        return self.add_artifact(run_id, target, producer, dependencies, artifact_type)

    def verify_artifact(self, record: dict[str, Any]) -> bool:
        path = Path(str(record.get("path", "")))
        return path.is_file() and sha256_file(path) == record.get("sha256")

    def list_runs(self) -> list[dict[str, Any]]:
        if not self.runs_dir.exists():
            return []
        rows: list[dict[str, Any]] = []
        for path in sorted(self.runs_dir.glob("*/manifest.json"), reverse=True):
            value = json.loads(path.read_text(encoding="utf-8"))
            rows.append(
                {
                    "run_id": value.get("run_id"),
                    "workflow": value.get("workflow"),
                    "status": value.get("status"),
                    "created_at": value.get("created_at"),
                    "updated_at": value.get("updated_at"),
                }
            )
        return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".json": "application/json",
        ".jsonl": "application/x-ndjson",
        ".md": "text/markdown",
        ".html": "text/html",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".pdf": "application/pdf",
    }.get(suffix, "application/octet-stream")
