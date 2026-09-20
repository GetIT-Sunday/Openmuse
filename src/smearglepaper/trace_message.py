"""Trace message model for TUI agent trace display."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from rich.console import Group
from rich.markdown import Markdown
from rich.text import Text


@dataclass
class TraceMessage:
    """A single message in the agent trace."""
    id: str
    time: str
    type: str  # system|user|agent|activity|tool|step|artifact|error|warning
    title: str = ""
    content: str = ""
    detail: list[str] = field(default_factory=list)
    artifact_path: str = ""
    detail_only: bool = False

    @staticmethod
    def create(
        msg_type: str,
        content: str,
        title: str = "",
        detail: list[str] | None = None,
        artifact_path: str = "",
        detail_only: bool = False,
    ) -> TraceMessage:
        now = datetime.now().strftime("%H:%M:%S")
        return TraceMessage(
            id=f"msg-{now}-{id(content) % 10000}",
            time=now,
            type=msg_type,
            title=title,
            content=content,
            detail=detail or [],
            artifact_path=artifact_path,
            detail_only=detail_only,
        )

    def to_rich_text(self) -> str:
        """Format as rich text for RichLog display."""
        tag_map = {
            "system": ("trace-tag-system", "提示"),
            "user": ("trace-tag-user", "你"),
            "agent": ("trace-tag-agent", "OpenMuse"),
            "activity": ("trace-tag-activity", "进度"),
            "tool": ("trace-tag-tool", "工具"),
            "step": ("trace-tag-step", "步骤"),
            "artifact": ("trace-tag-artifact", "文件"),
            "error": ("trace-tag-error", "需要处理"),
            "warning": ("trace-tag-error", "注意"),
        }
        tag_class, tag_text = tag_map.get(self.type, ("trace-tag-system", f"[{self.type}]"))
        # Give conversational roles a stable visual anchor; system/tool traces
        # retain the compact timeline format used by the details view.
        if self.type in {"user", "agent"}:
            lines = [
                "",
                f"[bold {tag_class}]{tag_text}[/] [#7d8596]{self.time}[/]",
                f"  {self.content}",
            ]
        else:
            lines = [f"[#7d8596]{self.time}[/] [#303642]│[/] [{tag_class}]{tag_text}[/] {self.content}"]
        for d in self.detail:
            lines.append(f"[#7d8596]         │[/] {d}")
        return "\n".join(lines)

    def to_renderable(self, *, details: bool = False):
        """Render content without interpreting user or tool text as Rich markup."""
        if self.type == "user":
            return Group(
                Text("你", style="bold #f5a623"),
                Text(self.content, style="#f5f5f7"),
                Text(""),
            )
        if self.type == "agent":
            label = Text("OpenMuse", style="bold #2997ff")
            if details:
                label.append(f"  {self.time}", style="#98989f")
            return Group(label, Markdown(self.content or "正在准备回答…"), Text(""))
        label = {"system": "提示", "activity": "进行中", "tool": "工具", "step": "步骤",
                 "artifact": "文件", "error": "需要处理", "warning": "注意"}.get(self.type, self.type)
        color = "#ff6961" if self.type in {"error", "warning"} else "#2997ff" if self.type == "activity" else "#98989f"
        text = Text(f"{self.time}  " if details else "", style="#98989f")
        text.append(f"{label}  ", style=color)
        text.append(self.content, style=color)
        for line in self.detail:
            text.append(f"\n  {line}", style="#98989f")
        return text
