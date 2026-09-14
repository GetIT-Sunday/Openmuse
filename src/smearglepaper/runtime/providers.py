from __future__ import annotations

from typing import Any, Protocol


class SourceProvider(Protocol):
    def collect(self, request: dict[str, Any]) -> list[dict[str, Any]]: ...


class LLMProvider(Protocol):
    def generate(self, request: dict[str, Any]) -> dict[str, Any]: ...


class Publisher(Protocol):
    def publish(self, package: dict[str, Any]) -> dict[str, Any]: ...


class Notifier(Protocol):
    def notify(self, message: dict[str, Any]) -> dict[str, Any]: ...
