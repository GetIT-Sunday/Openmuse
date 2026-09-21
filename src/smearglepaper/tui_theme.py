"""Unified color theme and CSS for SmearglePaper TUI."""

# ── Color Palette ────────────────────────────────────────────────────────

BG = "#0b0b0d"
PANEL = "#151518"
PANEL2 = "#1f1f23"
BORDER = "#343438"
TEXT = "#f5f5f7"
MUTED = "#98989f"
BRAND = "#f5a623"
ACCENT = "#2997ff"
SUCCESS = "#22c55e"
WARNING = "#eab308"
ERROR = "#ef4444"
PURPLE = "#a78bfa"


# ── App CSS ──────────────────────────────────────────────────────────────

APP_CSS = """
Screen {
    layout: vertical;
    background: #0b0b0d;
    color: #f5f5f7;
}

#resume-turn, #memory-review {
    display: none;
    height: 3;
    margin: 0 2;
}

ModalScreen {
    align: center middle;
    background: #000000 65%;
}

#approval-modal {
    width: 60;
    height: 17;
    padding: 2 3;
    background: #15181d;
    border: solid #eab308;
    align: center middle;
}

#approval-modal Horizontal {
    height: 3;
    margin-top: 2;
    align: right middle;
}

#approval-modal Button {
    margin-left: 1;
}

#model-connect-modal {
    width: 72;
    max-width: 94%;
    height: auto;
    max-height: 100%;
    padding: 1 2;
    background: #141416;
    border: solid #3a3a40;
    overflow-y: auto;
}

Screen.small-terminal #model-connect-modal {
    width: 72;
    height: auto;
    padding: 0 2;
}

Screen.small-terminal #connect-providers {
    min-height: 8;
    height: 8;
}

Screen.small-terminal #connect-providers ListItem {
    height: 2;
    padding: 0 1;
}

Screen.small-terminal #model-connect-modal Label {
    margin-top: 0;
}

Screen.small-terminal #model-connect-modal .connect-title {
    height: 1;
}

Screen.small-terminal #connect-step-title,
Screen.small-terminal #connect-provider-name {
    height: 1;
}

Screen.small-terminal #model-connect-modal Input {
    height: 3;
}

Screen.small-terminal #connect-status {
    height: 2;
    margin-top: 0;
}

Screen.small-terminal #model-connect-modal Horizontal {
    height: 3;
    margin-top: 0;
}

Screen.small-terminal #model-connect-modal Button {
    min-width: 9;
    height: 3;
    margin-left: 0;
}

#model-connect-modal Label {
    height: 1;
    color: #a1a1a8;
    margin-top: 0;
}

#connect-config {
    height: auto;
}

#model-connect-modal .connect-title {
    height: 2;
    color: #f5f5f7;
    text-style: bold;
    content-align: left middle;
}

#connect-step-title {
    height: 1;
    color: #8f8f98;
    padding-left: 1;
}

#connect-provider-name {
    height: 1;
    color: #f5f5f7;
    text-style: bold;
    content-align: left middle;
    padding-left: 1;
}

#model-connect-modal Input {
    height: 3;
    margin: 0;
    border: tall transparent;
    background: #222226;
    color: #f5f5f7;
}

#model-connect-modal Input:focus {
    border: tall #2997ff;
}

#connect-providers {
    height: 8;
    min-height: 8;
    margin: 1 0;
    background: #141416;
    border: none;
}

#connect-providers ListItem {
    height: 2;
    padding: 0 2;
    color: #d8d8de;
}

#connect-providers ListItem.--highlight {
    background: #f5a623;
    color: #151515;
    text-style: bold;
}

#connect-providers ListItem Label {
    margin: 0;
    color: #f5f5f7;
}

#connect-providers .provider-description {
    color: #98989f;
}

#connect-providers ListItem.--highlight Label {
    color: #151515;
}

#connect-hint {
    height: 1;
    color: #98989f;
    margin-top: 1;
}

Screen.small-terminal #connect-hint {
    margin-top: 0;
}

#model-connect-modal Horizontal {
    height: 3;
    margin-top: 1;
    align: right middle;
}

#model-connect-modal Button {
    min-width: 11;
    margin-left: 1;
    border: none;
    background: #29292e;
    color: #f5f5f7;
}

#model-connect-modal Button.-primary {
    background: #2997ff;
    color: #ffffff;
}

#model-connect-modal Button:hover {
    background: #3a3a42;
}

#connect-status {
    height: 2;
    color: #7d8596;
    margin-top: 0;
}

#connect-status.connect-error {
    color: #ef4444;
}

#model-picker-modal, #session-picker-modal {
    width: 58;
    max-width: 94%;
    height: auto;
    max-height: 22;
    padding: 1 2;
    background: #17171a;
    border: solid #45454b;
    align: center middle;
}

#model-picker-modal Static, #session-picker-modal Static {
    color: #7d8596;
    height: auto;
    padding: 0 1;
}

#model-filter {
    height: 3;
    margin: 1 0;
}

#model-list, #session-picker-list {
    height: 1fr;
    min-height: 5;
    max-height: 10;
    border: solid #303642;
    background: #101319;
}

#model-list ListItem, #session-picker-list ListItem {
    height: 2;
    padding: 0 1;
}

#model-list ListItem.--highlight, #session-picker-list ListItem.--highlight {
    background: #26303a;
    color: #ffffff;
}

#model-picker-modal Horizontal, #session-picker-modal Horizontal {
    height: 3;
    margin-top: 1;
    align: right middle;
}

#model-picker-modal Button, #session-picker-modal Button {
    min-width: 10;
    margin-left: 1;
    border: none;
    background: #2a2a2e;
    color: #f5f5f7;
}

#model-picker-modal Button.-primary, #session-picker-modal Button.-primary {
    background: #2997ff;
}

#session-picker-modal Button.-error {
    background: #8e3b45;
}

#session-picker-status {
    height: 2;
    color: #eab308;
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
    layout: vertical;
    height: 1fr;
    width: 100%;
    padding: 1 7 0 7;
}

#session-bar {
    height: 2;
    width: 100%;
    padding: 0 2;
    background: #0b0b0d;
    color: #98989f;
    content-align: left middle;
    border-bottom: solid #242428;
}

Screen.small-terminal #session-bar {
    height: 1;
    padding: 0 1;
}

/* ── AutoWechat Effect Stage ───────────────────────────────────────── */

#effect-stage {
    height: 10;
    min-height: 9;
    max-height: 12;
    width: 100%;
    padding: 0 2;
    background: transparent;
    border: none;
    content-align: center middle;
}

#stage-brand {
    height: 1;
    width: 100%;
    color: #f5a623;
    text-style: bold;
    text-align: center;
    padding-left: 0;
}

#stage-brand.logo {
    height: 2;
    color: #f5a623;
    text-style: bold;
    text-align: center;
    content-align: center middle;
    padding-left: 0;
}

#stage-current.idle {
    text-align: center;
    padding-left: 0;
}

#stage-subtitle {
    height: 1;
    width: 100%;
    color: #98989f;
    text-align: center;
    padding-left: 0;
}

#stage-subtitle.logo {
    text-align: center;
    padding-left: 0;
}

#stage-status {
    height: 2;
    width: 100%;
    margin-top: 1;
    color: #d7dde8;
    text-style: bold;
    text-align: center;
    padding-left: 0;
}

#stage-workflow {
    height: 4;
    width: 100%;
    color: #98989f;
    text-align: center;
    padding: 0 1;
    background: transparent;
}

#stage-current {
    height: 3;
    width: 100%;
    margin-top: 1;
    color: #d7dde8;
    text-align: center;
    padding-left: 0;
}

#stage-quick-actions, #stage-result-actions {
    height: 3;
    width: 100%;
    margin-top: 1;
    align: center middle;
}

#stage-quick-actions Button, #stage-result-actions Button {
    height: 3;
    min-width: 13;
    margin: 0 1;
    border: none;
    background: #1f1f23;
    color: #f5f5f7;
}

#stage-quick-actions Button:hover, #stage-result-actions Button:hover {
    background: #343438;
}

#stage-quick-actions Button.-primary, #stage-result-actions Button.-primary {
    background: #2997ff;
    color: #ffffff;
}

#stage-candidates {
    height: 1fr;
    min-height: 4;
    max-height: 15;
    width: 100%;
    background: #101319;
    border: none;
}

#stage-candidates ListItem {
    height: auto;
    min-height: 4;
    padding: 0 2;
    color: #d7dde8;
}

#stage-candidates ListItem.--highlight {
    background: #1c2027;
    color: #ffffff;
}

#stage-artifacts {
    height: 3;
    width: 100%;
    margin-top: 1;
    color: #7d8596;
    text-align: center;
    padding: 0 1;
}

#effect-stage.compact {
    height: 10;
    min-height: 10;
    max-height: 10;
    padding: 0 2;
}

Screen.small-terminal #effect-stage {
    height: 9;
    min-height: 8;
    max-height: 9;
}

Screen.small-terminal #main-container {
    padding: 0 2 0 2;
}

Screen.small-terminal #stage-quick-actions Button,
Screen.small-terminal #stage-result-actions Button {
    min-width: 10;
    margin: 0;
}

#quick-connect {
    min-width: 18;
}

#effect-stage.compact #stage-brand {
    height: 2;
    text-align: left;
    content-align: left middle;
    padding-left: 1;
}

#effect-stage.compact #stage-subtitle,
#effect-stage.compact #stage-workflow,
#effect-stage.compact #stage-artifacts,
#effect-stage.compact #quick-paper,
#effect-stage.compact #quick-continue,
#effect-stage.compact #action-revise,
#effect-stage.compact #action-open,
#effect-stage.compact #action-draft {
    display: none;
}

/* ── Conversation ──────────────────────────────────────────────────── */

#conversation-area {
    height: 1fr;
    min-height: 8;
    width: 100%;
    margin-top: 1;
    background: #0b0d10;
}

#conversation-area.has-messages {
    height: 1fr;
    min-height: 8;
}

#conversation-label {
    height: 1;
    color: #98989f;
    padding-left: 1;
    text-style: bold;
}

#conversation-empty {
    height: auto;
    min-height: 8;
    margin: 2 1 0 1;
    padding: 1 2;
    color: #98989f;
    background: #111114;
    border: solid #242428;
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
    min-height: 0;
    overflow-y: auto;
    padding: 1 2;
    margin: 0 1;
    background: #0b0b0d;
    border-left: none;
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

.trace-tag-activity {
    color: #facc15;
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
    height: 6;
    padding: 0 1;
    background: #0b0d10;
    dock: bottom;
}

#composer-row {
    height: 3;
    width: 100%;
    background: #17171b;
    border: round #3b3b42;
    padding: 0;
}

#chat-input {
    width: 1fr;
    height: 3;
    border: none;
    background: transparent;
    padding: 0 1;
}

#chat-input:focus {
    border: none;
    background: #1b1b20;
}

#composer-stop {
    display: none;
    width: 8;
    height: 3;
    margin: 0 1 0 0;
    border: none;
    background: #8e3b45;
    color: #ffffff;
}

#composer-meta {
    height: 1;
    color: #d7dde8;
    padding: 0 2;
    width: 1fr;
    background: #0b0d10;
    content-align: left middle;
    text-overflow: ellipsis;
    text-wrap: nowrap;
}


/* ── Footer ──────────────────────────────────────────────────────────── */

#footer-bar {
    height: 2;
    width: 100%;
    background: #0b0d10;
    color: #98989f;
    content-align: center middle;
    padding: 0 1;
    text-wrap: wrap;
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
    width: 64;
    height: auto;
    max-height: 28;
    border: solid #45454b;
    background: #17171a;
    padding: 2 2;
    layer: overlay;
}

#slash-command-popup {
    dock: bottom;
    width: 72;
    height: auto;
    max-height: 19;
    margin: 0 0 7 7;
    padding: 1 0;
    background: #151518;
    border: solid #3a3a40;
    layer: overlay;
}

Screen.small-terminal #slash-command-popup {
    width: 1fr;
    max-height: 12;
    margin: 0 1 7 1;
}

#slash-title {
    height: 1;
    color: #8f8f98;
    padding-left: 2;
}

#slash-list {
    height: auto;
    max-height: 16;
    background: #17171a;
}

#slash-list ListItem {
    height: 2;
    padding: 0 2;
    color: #d1d1d6;
}

#slash-list ListItem.--highlight {
    background: #f5a623;
    color: #151515;
    text-style: bold;
}

#palette-title {
    height: 2;
    color: #f5f5f7;
}

#palette-hint {
    height: 1;
    color: #98989f;
    padding-bottom: 1;
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
    background: #2a2a2e;
    color: #2997ff;
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
