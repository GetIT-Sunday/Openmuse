"""Reusable TUI components for SmearglePaper Agent Console."""
from __future__ import annotations

import re
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import (
    Input,
    Label,
    ListItem,
    ListView,
    Static,
)

from .config import DATA_DIR
from .run_state import (
    RunState,
    StepStatus,
    load_model_info,
    scan_artifacts,
)
from .session import Session


# ── Sidebar ──────────────────────────────────────────────────────────────

class Sidebar(Vertical):
    """Left sidebar with sessions, artifacts, actions, project info."""

    def __init__(self, sessions: list[Session] | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.sessions = sessions or []

    def compose(self) -> ComposeResult:
        yield Label("SESSIONS", classes="sidebar-section-title")
        yield ListView(id="session-list")
        yield Static(classes="sidebar-separator")
        yield Label("WORKFLOWS", classes="sidebar-section-title")
        yield Vertical(id="sidebar-workflows")
        yield Static(classes="sidebar-separator")
        yield Label("ARTIFACTS", classes="sidebar-section-title")
        yield Vertical(id="sidebar-artifacts")
        yield Static(classes="sidebar-separator")
        yield Label("ACTIONS", classes="sidebar-section-title")
        yield Label(" + New Session        ^N", classes="sidebar-action")
        yield Label(" > Command Palette    ^P", classes="sidebar-action")
        yield Static(classes="sidebar-separator")
        yield Label("PROJECT", classes="sidebar-section-title")
        yield Label(f" {DATA_DIR.parent.name}", classes="sidebar-project")
        yield Label(" Git: main", classes="sidebar-project")

    def on_mount(self) -> None:
        self._populate_sessions()
        self._populate_workflows()
        self._populate_artifacts()

    def _populate_sessions(self) -> None:
        list_view = self.query_one("#session-list", ListView)
        for s in self.sessions[:10]:
            title = s.title[:20]
            marker = "●" if s.id == (self.sessions[0].id if self.sessions else "") else "○"
            list_view.append(ListItem(Label(f" {marker} {title}")))

    def _populate_workflows(self) -> None:
        container = self.query_one("#sidebar-workflows", Vertical)
        for name in ["arxiv-paper-flow", "github-trending", "wechat-publish"]:
            container.mount(Label(f"  ▸ {name}", classes="sidebar-workflow"))

    def _populate_artifacts(self) -> None:
        container = self.query_one("#sidebar-artifacts", Vertical)
        for a in scan_artifacts()[:6]:
            container.mount(Label(f"  {a.icon} {a.name:<16s} {a.size or ''}", classes="artifact-item"))

    def refresh_sessions(self, sessions: list[Session], active_id: str = "") -> None:
        list_view = self.query_one("#session-list", ListView)
        list_view.clear()
        for s in sessions[:10]:
            title = s.title[:20]
            marker = "●" if s.id == active_id else "○"
            list_view.append(ListItem(Label(f" {marker} {title}")))

    def update_artifacts_for_run(self, artifacts: list[dict[str, str]]) -> None:
        """Update artifacts section with mock run artifacts."""
        container = self.query_one("#sidebar-artifacts", Vertical)
        container.remove_children()
        for a in artifacts[:6]:
            name = a.get("name", "")
            size = a.get("size", "")
            icon = a.get("icon", "📄")
            container.mount(Label(f"  {icon} {name:<16s} {size}", classes="artifact-item"))


# ── Header Bar ───────────────────────────────────────────────────────────

class HeaderBar(Static):
    """Single-line status header."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._session_id = ""
        self._model = ""
        self._mode = "Chat"
        self._status = "Ready"
        self._status_color = "success"

    def update_state(self, run_state: RunState, session_id: str) -> None:
        self._session_id = session_id[:12]
        model = run_state.model
        self._model = f"{model.provider}/{model.name}" if model else "Unknown"
        raw_mode = run_state.mode.value
        self._mode = "Chat" if raw_mode == "idle" else raw_mode.capitalize()
        self._status = run_state.status_text
        self._status_color = run_state.status_color
        self._refresh_display()

    def _refresh_display(self) -> None:
        from datetime import datetime
        now = datetime.now().strftime("%H:%M:%S")
        color_map = {"muted": "#7d8596", "warning": "#eab308", "success": "#22c55e", "error": "#ef4444"}
        sc = color_map.get(self._status_color, "#7d8596")
        mc = "#22d3ee" if self._mode == "Chat" else "#eab308"
        # Truncate model name
        model_str = self._model
        if len(model_str) > 28:
            model_str = model_str[:26] + "…"
        self.update(
            f"[bold #f59e0b]SmearglePaper[/bold #f59e0b]"
            f"  │  Session: [#22d3ee]{self._session_id}[/]"
            f"  │  Model: [#d7dde8]{model_str}[/]"
            f"  │  Mode: [{mc}]{self._mode}[/]"
            f"  │  Status: [{sc}]{self._status}[/]"
            f"  │  [#7d8596]{now}[/]"
        )


# ── Context Panel ────────────────────────────────────────────────────────

class ContextPanel(Vertical):
    """Right panel showing run context: workflow, artifacts, stats, model."""

    def compose(self) -> ComposeResult:
        yield Label("RUN CONTEXT", classes="ctx-section-title")
        yield Label("STATUS", classes="ctx-section-title")
        yield Label(" ● Ready", id="ctx-status")
        yield Label("CURRENT TASK", classes="ctx-section-title")
        yield Label(" No active task", id="ctx-current-task")
        yield Label("NEXT ACTIONS", classes="ctx-section-title")
        yield Vertical(id="ctx-next-actions")
        yield Label("WORKFLOWS", classes="ctx-section-title")
        yield Vertical(id="ctx-workflows")
        yield Label("ARTIFACTS", classes="ctx-section-title")
        yield Vertical(id="ctx-artifacts-list")
        yield Label("STATS", classes="ctx-section-title")
        yield Vertical(id="ctx-stats")
        yield Label("MODEL", classes="ctx-section-title")
        yield Vertical(id="ctx-model")

    def update_state(self, run_state: RunState) -> None:
        self._update_status(run_state)
        self._update_current_task(run_state)
        self._update_next_actions(run_state)
        self._update_workflows(run_state)
        self._update_artifacts(run_state)
        self._update_stats(run_state)
        self._update_model(run_state)

    def _update_status(self, rs: RunState) -> None:
        el = self.query_one("#ctx-status", Label)
        if rs.status.value == "running":
            el.update(" ● Running")
            el.styles.color = "#eab308"
        elif rs.status.value == "success":
            el.update(" ● Done")
            el.styles.color = "#22c55e"
        elif rs.status.value == "failed":
            el.update(" ● Failed")
            el.styles.color = "#ef4444"
        else:
            el.update(" ● Ready")
            el.styles.color = "#22c55e"

    def _update_current_task(self, rs: RunState) -> None:
        el = self.query_one("#ctx-current-task", Label)
        task = rs.current_task or rs.current_step or "No active task"
        # Truncate long tasks
        if len(task) > 24:
            task = task[:22] + "…"
        el.update(f" {task}")

    def _update_next_actions(self, rs: RunState) -> None:
        container = self.query_one("#ctx-next-actions", Vertical)
        container.remove_children()
        if rs.status.value == "running":
            container.mount(Label(" ⟳ Workflow running...", classes="ctx-row-muted"))
        else:
            container.mount(Label(" › Ctrl+P  Commands", classes="ctx-action"))
            container.mount(Label(" › Type    collect papers...", classes="ctx-action"))
            container.mount(Label(" › Ctrl+N  New session", classes="ctx-action"))

    def _update_workflows(self, rs: RunState) -> None:
        container = self.query_one("#ctx-workflows", Vertical)
        container.remove_children()
        if rs.status.value == "running":
            for i, step in enumerate(rs.steps, 1):
                color_map = {
                    StepStatus.WAITING: "#7d8596",
                    StepStatus.RUNNING: "#eab308",
                    StepStatus.SUCCESS: "#22c55e",
                    StepStatus.FAILED: "#ef4444",
                }
                c = color_map.get(step.status, "#7d8596")
                marker = "│" if step.status == StepStatus.RUNNING else " "
                status_label = step.status_label
                container.mount(Label(
                    f" {marker} {i}. {step.label:<18s} [{c}]{step.icon}[/] {status_label}",
                    classes="ctx-row",
                ))
        else:
            container.mount(Label(" 1. arxiv-paper-flow", classes="ctx-row"))
            container.mount(Label(" 2. github-trending", classes="ctx-row"))
            container.mount(Label(" 3. wechat-publish", classes="ctx-row"))

    def _update_artifacts(self, rs: RunState) -> None:
        container = self.query_one("#ctx-artifacts-list", Vertical)
        container.remove_children()
        if not rs.artifacts:
            container.mount(Label(" No artifacts generated", classes="ctx-row-muted"))
            return
        for a in rs.artifacts[:5]:
            size = f"  {a.size}" if a.size else ""
            container.mount(Label(f" {a.icon} {a.name}{size}", classes="ctx-row"))

    def _update_stats(self, rs: RunState) -> None:
        container = self.query_one("#ctx-stats", Vertical)
        container.remove_children()
        s = rs.stats
        rows = [
            ("Collected", str(s.papers_collected) if s.papers_collected else "-"),
            ("Selected", str(s.papers_selected) if s.papers_selected else "-"),
            ("Parsed", str(s.parsed) if s.parsed else "-"),
            ("Tokens", f"{s.tokens:,}" if s.tokens else "-"),
        ]
        for label, value in rows:
            container.mount(Label(f" {label:<12s} {value}", classes="ctx-row"))

    def _update_model(self, rs: RunState) -> None:
        container = self.query_one("#ctx-model", Vertical)
        container.remove_children()
        m = rs.model
        if m:
            model_name = m.name
            if len(model_name) > 16:
                model_name = model_name[:14] + "…"
            container.mount(Label(f" Provider  {m.provider}", classes="ctx-row"))
            container.mount(Label(f" Model     {model_name}", classes="ctx-row"))
        else:
            container.mount(Label(" No model info", classes="ctx-row-muted"))


# ── Welcome Dashboard ────────────────────────────────────────────────────

class WelcomeWidget(Vertical):
    """Welcome dashboard with card border."""

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold #f59e0b]SmearglePaper[/bold #f59e0b]\n"
            "[#7d8596]Research Agent for Papers, Articles and WeChat Drafts[/]\n"
            "\n"
            "[bold #22d3ee]Quick Start[/]\n"
            "[#d7dde8]  › collect recent papers about multimodal agents[/]\n"
            "[#d7dde8]  › generate a WeChat article from latest ranked papers[/]\n"
            "[#d7dde8]  › review current article quality[/]\n"
            "[#d7dde8]  › publish latest draft to WeChat[/]\n"
            "\n"
            "[bold #22d3ee]Suggested Workflows[/]\n"
            "[#d7dde8]  [1] arxiv-paper-flow     collect → rank → ingest → article[/]\n"
            "[#d7dde8]  [2] github-trending       collect → summarize → notify[/]\n"
            "[#d7dde8]  [3] wechat-publish        draft → review → publish[/]\n"
            "\n"
            "[bold #22d3ee]Today[/]\n"
            "[#7d8596]  Papers    0       Drafts    0       Last Run    -[/]\n"
            "\n"
            "[#eab308]^P[/] commands    [#eab308]^N[/] new session",
            id="welcome-content",
        )


# ── Recent Activity ──────────────────────────────────────────────────────

class RecentActivityWidget(Vertical):
    """Recent activity card shown below Welcome."""

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold #22d3ee]Recent Activity[/]\n"
            "[#7d8596]  No runs yet. Start with one of the suggested workflows above.[/]\n"
            "[#7d8596]  Tip: Press ^P to open commands, or type a natural language task.[/]",
            id="recent-activity-content",
        )


# ── Run Block (Agent Trace skeleton) ─────────────────────────────────────

class RunBlockWidget(Vertical):
    """Run status card for active workflow execution."""

    def __init__(self, run_state: RunState | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._run_state = run_state

    def compose(self) -> ComposeResult:
        yield Static("", id="run-block-content")
        yield Static("", id="run-summary-content")

    def update_state(self, rs: RunState) -> None:
        el = self.query_one("#run-block-content", Static)
        summary_el = self.query_one("#run-summary-content", Static)

        if rs.status.value != "running" or not rs.steps:
            el.update("")
            summary_el.update("")
            return

        # Run Block
        lines = [f"[bold #a78bfa]Run: {rs.title or 'workflow'}[/]"]
        for step in rs.steps:
            color_map = {
                StepStatus.WAITING: "#7d8596",
                StepStatus.RUNNING: "#eab308",
                StepStatus.SUCCESS: "#22c55e",
                StepStatus.FAILED: "#ef4444",
            }
            c = color_map.get(step.status, "#7d8596")
            summary_text = f"  {step.summary}" if step.summary else ""
            lines.append(f" [{c}]{step.icon}[/] {step.label}{summary_text}")
        el.update("\n".join(lines))

        # Progress Bar
        if rs.progress and rs.progress.total > 0:
            pct = rs.progress.percent
            filled = int(pct / 5)
            empty = 20 - filled
            bar = f"{'█' * filled}{'░' * empty} {pct}%"
            lines.append(f"\n [#eab308]{bar}[/]")

        # Run Summary
        s = rs.stats
        summary_lines = [
            f"[bold #a78bfa]Run Summary: {rs.id}[/]",
            f" Papers Collected  {s.papers_collected or '-'}     Selected  {s.papers_selected or '-'}",
            f" Parsed            {s.parsed or '-'} / {rs.progress.total if rs.progress else '-'}     Tokens    {s.tokens:,}     Est. Cost  ${s.cost:.2f}",
        ]
        summary_el.update("\n".join(summary_lines))


# ── Command Palette ──────────────────────────────────────────────────────

COMMANDS = [
    ("collect-arxiv", "Search arXiv papers"),
    ("rank-papers", "Rank collected papers"),
    ("ingest-paper", "Parse selected paper PDF"),
    ("generate-article", "Generate WeChat article"),
    ("review-article", "Review article quality"),
    ("improve-article", "Improve article with LLM"),
    ("create-wechat-draft", "Create WeChat draft"),
    ("collect-github", "Collect GitHub trending repos"),
    ("daily-digest", "Run daily digest workflow"),
    ("trend-analysis", "Analyze research trends"),
    ("check-status", "Check system status"),
    ("agent-run", "Run full workflow"),
]


class CommandPalette(Vertical):
    """Modal command palette for quick command execution."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.visible = False
        self._selected_idx = 0
        self._filtered: list[tuple[str, str]] = list(COMMANDS)

    def compose(self) -> ComposeResult:
        yield Static("[bold #22d3ee]Command Palette[/]")
        yield Input(placeholder="Type to filter...", id="palette-input")
        yield Vertical(id="palette-list")

    def on_mount(self) -> None:
        self.visible = False
        self.display = False

    def show(self) -> None:
        self.visible = True
        self.display = True
        self._selected_idx = 0
        self._filtered = list(COMMANDS)
        self._refresh_list()
        self.query_one("#palette-input", Input).value = ""
        self.query_one("#palette-input", Input).focus()

    def hide(self) -> None:
        self.visible = False
        self.display = False

    def filter_commands(self, query: str) -> None:
        q = query.lower().strip()
        if not q:
            self._filtered = list(COMMANDS)
        else:
            self._filtered = [(n, d) for n, d in COMMANDS if q in n.lower() or q in d.lower()]
        self._selected_idx = 0
        self._refresh_list()

    def _refresh_list(self) -> None:
        container = self.query_one("#palette-list", Vertical)
        container.remove_children()
        for i, (name, desc) in enumerate(self._filtered):
            cls = "palette-item palette-item-selected" if i == self._selected_idx else "palette-item"
            container.mount(Label(f"  {name:<28s} {desc}", classes=cls))

    def get_selected(self) -> str | None:
        if 0 <= self._selected_idx < len(self._filtered):
            return self._filtered[self._selected_idx][0]
        return None


# ── Clean Input (filters terminal escape sequences) ──────────────────────

class CleanInput(Input):
    """Input widget that filters out terminal escape sequences."""

    # Kitty keyboard protocol pattern
    _ESCAPE_PATTERNS = [
        r'\[\d+;;\d+:\d+u',  # Kitty protocol: [32;;20320:22909u
        r'\[\d+;\d+[A-Z]',   # CSI sequences: [1;2A
        r'\[\?[\d;]+[hlp]',  # Mode set/reset: [?25h
        r'\[\d+[A-Z]',       # Simple CSI: [2J
        r'\[\d+;\d+~',       # Key sequences: [1;2~
        r'\[[\d;]*[A-Za-z]', # Generic CSI
    ]

    def _clean_text(self, text: str) -> str:
        """Remove terminal escape sequences from text."""
        for pattern in self._ESCAPE_PATTERNS:
            text = re.sub(pattern, '', text)
        return text

    def _on_key(self, event) -> None:
        """Filter escape sequences from key events."""
        # Let parent handle the key first
        super()._on_key(event)
        # Then clean the value if it contains escape sequences
        if self._clean_text(self.value) != self.value:
            self.value = self._clean_text(self.value)


# ── Input Bar ────────────────────────────────────────────────────────────

class InputBar(Vertical):
    """Terminal-style input with cyan border."""

    def compose(self) -> ComposeResult:
        yield CleanInput(
            placeholder="> Ask SmearglePaper or type a command...",
            id="chat-input",
        )

    def set_run_mode(self, running: bool) -> None:
        input_widget = self.query_one("#chat-input", Input)
        if running:
            input_widget.placeholder = "> Agent is running. Type a follow-up or /stop..."
        else:
            input_widget.placeholder = "> Ask SmearglePaper or type a command..."

    def get_text(self) -> str:
        return self.query_one("#chat-input", Input).value

    def clear(self) -> None:
        self.query_one("#chat-input", Input).value = ""
