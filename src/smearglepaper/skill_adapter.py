"""Stable adapter contracts between portable Skills and an AIGC Harness.

These are intentionally small protocols. A Codex or Claude Code adapter can
implement them without importing AutoWechat's Runtime or TUI.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Protocol


class ModelGateway(Protocol):
    def stream(self, messages: list[dict[str, object]], *, model: str | None = None) -> Iterator[str]: ...


class MemoryBackend(Protocol):
    def recall(self, query: str, *, limit: int = 10) -> list[dict[str, str]]: ...
    def remember(self, item: dict[str, str]) -> None: ...
    def clear(self) -> None: ...


class ApprovalGate(Protocol):
    def request(self, action: str, details: Mapping[str, object]) -> bool: ...


class ArtifactBackend(Protocol):
    def save(self, name: str, content: bytes, *, media_type: str = "application/octet-stream") -> str: ...
    def list(self) -> list[dict[str, str]]: ...


@dataclass(frozen=True)
class HarnessServices:
    model: ModelGateway
    memory: MemoryBackend
    artifacts: ArtifactBackend
    approval: ApprovalGate


@dataclass(frozen=True)
class SkillRequest:
    skill_id: str
    inputs: dict[str, object] = field(default_factory=dict)
    context: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillResult:
    skill_id: str
    status: str
    outputs: dict[str, object] = field(default_factory=dict)
    artifacts: tuple[str, ...] = ()
    message: str = ""


class SkillAdapter(Protocol):
    skill_id: str

    def run(self, request: SkillRequest, services: HarnessServices) -> SkillResult: ...


class AdapterRegistry:
    """Runtime-local adapter registry; portable Skills never depend on it."""

    def __init__(self) -> None:
        self._adapters: dict[str, SkillAdapter] = {}

    def register(self, adapter: SkillAdapter) -> None:
        if adapter.skill_id in self._adapters:
            raise ValueError(f"Skill adapter already registered: {adapter.skill_id}")
        self._adapters[adapter.skill_id] = adapter

    def get(self, skill_id: str) -> SkillAdapter:
        try:
            return self._adapters[skill_id]
        except KeyError as exc:
            raise KeyError(f"No adapter registered for Skill: {skill_id}") from exc

    def ids(self) -> tuple[str, ...]:
        return tuple(self._adapters)
