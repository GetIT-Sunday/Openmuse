"""Unified color theme and CSS for SmearglePaper TUI."""

# ── Color Palette ────────────────────────────────────────────────────────

BG = "#0b0d10"
PANEL = "#15181d"
PANEL2 = "#1c2027"
BORDER = "#303642"
TEXT = "#d7dde8"
MUTED = "#7d8596"
BRAND = "#f59e0b"
ACCENT = "#22d3ee"
SUCCESS = "#22c55e"
WARNING = "#eab308"
ERROR = "#ef4444"
PURPLE = "#a78bfa"


# ── App CSS ──────────────────────────────────────────────────────────────

APP_CSS = """
Screen {
    layout: vertical;
}

/* ── Header ──────────────────────────────────────────────────────────── */

#header-bar {
    height: 1;
    width: 100%;
    background: #1a1d24;
    color: #d7dde8;
    content-align: left middle;
    padding: 0 1;
}

/* ── Main Container ──────────────────────────────────────────────────── */

#main-container {
    layout: horizontal;
    height: 1fr;
    width: 100%;
}

/* ── Sidebar ─────────────────────────────────────────────────────────── */

#sidebar {
    width: 24;
    height: 100%;
    background: #15181d;
    border-right: solid #303642;
    overflow-y: auto;
}

.sidebar-section-title {
    text-style: bold;
    color: #7d8596;
    height: 2;
    padding: 1 2 0 2;
}

.sidebar-separator {
    height: 1;
    border-bottom: solid #303642;
}

#session-list {
    height: 10;
    max-height: 10;
}

#sidebar-workflows {
    max-height: 8;
}

.sidebar-workflow {
    height: 2;
    padding: 0 2;
    color: #7d8596;
}

.sidebar-workflow-active {
    color: #22d3ee;
}

#sidebar-artifacts {
    max-height: 12;
    overflow-y: auto;
}

.artifact-item {
    height: 2;
    padding: 0 2;
    color: #7d8596;
}

.sidebar-action {
    height: 2;
    padding: 0 2;
    color: #22d3ee;
}

.sidebar-project {
    height: 2;
    padding: 0 2;
    color: #7d8596;
}

/* ── Main Area ───────────────────────────────────────────────────────── */

#main-area {
    width: 1fr;
    height: 100%;
    layout: vertical;
}

/* ── Welcome Dashboard ───────────────────────────────────────────────── */

#welcome-wrapper {
    height: 1fr;
    padding: 2 4;
    content-align: center middle;
}

#welcome-card {
    width: 80%;
    border: solid #303642;
    background: #15181d;
    padding: 2 3;
}

/* ── Recent Activity ─────────────────────────────────────────────────── */

#recent-activity {
    height: auto;
    max-height: 8;
    margin: 0 4;
    border: solid #303642;
    background: #15181d;
    padding: 1 3;
}

/* ── Trace Area ──────────────────────────────────────────────────────── */

#trace-area {
    height: 1fr;
    overflow-y: auto;
    padding: 1 2;
}

.trace-tag-system {
    color: #7d8596;
    text-style: italic;
}

.trace-tag-user {
    color: #22d3ee;
    text-style: bold;
}

.trace-tag-agent {
    color: #60a5fa;
    text-style: bold;
}

.trace-tag-tool {
    color: #4ade80;
    text-style: bold;
}

.trace-tag-step {
    color: #facc15;
    text-style: bold;
}

.trace-tag-artifact {
    color: #c084fc;
    text-style: bold;
}

.trace-tag-error {
    color: #f87171;
    text-style: bold;
}

/* ── Input Section ───────────────────────────────────────────────────── */

#input-bar {
    height: 3;
    padding: 0 2;
    background: #0b0d10;
}

#chat-input {
    width: 100%;
    height: 3;
    border: solid #22d3ee;
    background: #15181d;
}

/* ── Footer ──────────────────────────────────────────────────────────── */

#footer-bar {
    height: 1;
    width: 100%;
    background: #1a1d24;
    color: #7d8596;
    content-align: center middle;
    padding: 0 1;
}

/* ── Context Panel ───────────────────────────────────────────────────── */

#context-panel {
    width: 28;
    height: 100%;
    background: #15181d;
    border-left: solid #303642;
    overflow-y: auto;
}

.ctx-section-title {
    text-style: bold;
    color: #7d8596;
    height: 2;
    padding: 1 2 0 2;
}

.ctx-separator {
    height: 1;
    border-bottom: solid #303642;
}

.ctx-row {
    height: 2;
    padding: 0 2;
    color: #d7dde8;
}

.ctx-row-muted {
    height: 2;
    padding: 0 2;
    color: #7d8596;
}

#ctx-status {
    height: 2;
    padding: 0 2;
    color: #22c55e;
}

#ctx-current-task {
    height: 2;
    padding: 0 2;
    color: #d7dde8;
}

#ctx-next-actions {
    max-height: 8;
}

.ctx-action {
    height: 2;
    padding: 0 2;
    color: #22d3ee;
}

#ctx-workflows {
    max-height: 8;
}

#ctx-artifacts-list {
    max-height: 10;
}

#ctx-stats {
    max-height: 12;
}

#ctx-model {
    max-height: 8;
}

/* ── Run Block ───────────────────────────────────────────────────────── */

#run-block-content {
    border: solid #a78bfa;
    background: #15181d;
    padding: 1 2;
    margin: 1 2;
}

#run-summary-content {
    border: solid #a78bfa;
    background: #15181d;
    padding: 1 2;
    margin: 0 2;
}

.run-step-success {
    color: #22c55e;
}

.run-step-running {
    color: #eab308;
    text-style: bold;
}

.run-step-waiting {
    color: #7d8596;
}

.run-step-failed {
    color: #ef4444;
}

/* ── Command Palette ─────────────────────────────────────────────────── */

#command-palette {
    width: 60;
    height: auto;
    max-height: 30;
    border: solid #22d3ee;
    background: #15181d;
    padding: 1 2;
    layer: overlay;
}

#palette-input {
    width: 100%;
}

#palette-list {
    height: auto;
    max-height: 20;
}

.palette-item {
    height: 2;
    padding: 0 1;
    color: #d7dde8;
}

.palette-item-selected {
    background: #1c2027;
    color: #22d3ee;
}
"""


def styled_header_text(session_id: str, model: str, mode: str, status: str, status_color: str) -> str:
    """Build styled header text with Textual markup."""
    return (
        f"[bold #f59e0b]SmearglePaper[/bold #f59e0b]"
        f"  │  Session: [#22d3ee]{session_id}[/]"
        f"  │  Model: [#d7dde8]{model}[/]"
        f"  │  Mode: [#eab308]{mode}[/]"
        f"  │  Status: [{status_color}]{status}[/]"
    )


def styled_footer_text() -> str:
    """Build footer keyboard shortcuts."""
    return (
        "[#eab308]^N[/] New"
        "  [#eab308]^R[/] Run"
        "  [#eab308]^P[/] Commands"
        "  [#eab308]^S[/] Save"
        "  [#eab308]^L[/] Clear"
        "  [#eab308]^B[/] Context"
        "  [#eab308]^?[/] Help"
        "  [#eab308]^Q[/] Quit"
    )
