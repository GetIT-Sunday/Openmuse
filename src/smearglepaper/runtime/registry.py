from __future__ import annotations

from .models import ToolHandler, ToolSpec


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSpec, ToolHandler]] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = (spec, handler)

    def get(self, name: str) -> tuple[ToolSpec, ToolHandler]:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown runtime tool: {name}") from exc

    def specs(self) -> list[ToolSpec]:
        return [item[0] for item in self._tools.values()]
