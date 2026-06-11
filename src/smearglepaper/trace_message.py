"""Trace message model for TUI agent trace display."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class TraceMessage:
    """A single message in the agent trace."""
    id: str
    time: str
    type: str  # system|user|agent|tool|step|artifact|error|warning
    title: str = ""
    content: str = ""
    detail: list[str] = field(default_factory=list)
    artifact_path: str = ""

    @staticmethod
    def create(msg_type: str, content: str, title: str = "", detail: list[str] | None = None, artifact_path: str = "") -> TraceMessage:
        now = datetime.now().strftime("%H:%M:%S")
        return TraceMessage(
            id=f"msg-{now}-{id(content) % 10000}",
            time=now,
            type=msg_type,
            title=title,
            content=content,
            detail=detail or [],
            artifact_path=artifact_path,
        )

    def to_rich_text(self) -> str:
        """Format as rich text for RichLog display."""
        tag_map = {
            "system": ("trace-tag-system", "[system]"),
            "user": ("trace-tag-user", "[user]"),
            "agent": ("trace-tag-agent", "[agent]"),
            "tool": ("trace-tag-tool", "[tool]"),
            "step": ("trace-tag-step", "[step]"),
            "artifact": ("trace-tag-artifact", "[artifact]"),
            "error": ("trace-tag-error", "[error]"),
            "warning": ("trace-tag-error", "[warning]"),
        }
        tag_class, tag_text = tag_map.get(self.type, ("trace-tag-system", f"[{self.type}]"))
        lines = [f"[#7d8596]{self.time}[/] [#303642]│[/] [{tag_class}]{tag_text}[/] {self.content}"]
        for d in self.detail:
            lines.append(f"[#7d8596]         │[/] {d}")
        return "\n".join(lines)
