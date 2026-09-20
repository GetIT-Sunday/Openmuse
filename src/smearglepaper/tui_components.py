"""Reusable TUI components for SmearglePaper Agent Console."""
from __future__ import annotations

import re
from typing import cast

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
)

from .config import DATA_DIR
from .run_state import RunState, StepStatus, scan_artifacts
from .session import Session
from .tui_presentation import RunPresentation

# A restrained wordmark works better than block-art in narrow terminals and
# remains legible with proportional and CJK-capable terminal fonts.
OPENMUSE_LOGO = "OpenMuse"
# Compatibility alias for older integrations and tests. AutoWechat is now the
# first official Pack, while OpenMuse is the Harness brand.
AUTOWECHAT_LOGO = OPENMUSE_LOGO

# ── AutoWechat Effect Stage ─────────────────────────────────────────────

class AutoWechatStage(Vertical):
    """Primary canvas for workflow progress and generated artifacts."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._idle = True
        self._compact = False

    def set_compact(self, compact: bool) -> None:
        self._compact = compact
        self.set_class(compact, "compact")
        self._update_brand()

    def _update_brand(self) -> None:
        brand = self.query_one("#stage-brand", Static)
        brand.update(OPENMUSE_LOGO if self._compact or not self._idle else OPENMUSE_LOGO)
        brand.set_class(self._idle and not self._compact, "logo")
        self.query_one("#stage-subtitle", Static).set_class(
            self._idle and not self._compact, "logo"
        )

    def _set_idle_layout(self, idle: bool) -> None:
        self._idle = idle
        self._update_brand()
        self.query_one("#stage-status", Static).display = not idle
        self.query_one("#stage-workflow", Static).display = not idle
        self.query_one("#stage-artifacts", Static).display = not idle
        self.query_one("#stage-current", Static).set_class(idle, "idle")

    def compose(self) -> ComposeResult:
        yield Static(OPENMUSE_LOGO, id="stage-brand")
        yield Static("AutoWechat Pack · 把研究变成值得发布的文章", id="stage-subtitle")
        yield Static("● 准备就绪", id="stage-status")
        yield Static(
            "选择材料   →   理解论文   →   撰写文章   →   质量审阅   →   准备预览",
            id="stage-workflow",
        )
        yield Static("粘贴论文链接，或者告诉我你关心的研究主题。", id="stage-current")
        yield Horizontal(
            Button("解读一篇论文", id="quick-paper"),
            Button("从主题寻找选题", id="quick-topic", variant="primary"),
            Button("继续上次任务", id="quick-continue"),
            Button("配置模型", id="quick-connect", variant="primary"),
            id="stage-quick-actions",
        )
        yield ListView(id="stage-candidates")
        yield Static("文章和研究材料会自动保存在本地", id="stage-artifacts")
        yield Horizontal(
            Button("继续修改", id="action-revise"),
            Button("手机预览", id="action-preview", variant="primary"),
            Button("打开文章", id="action-open"),
            Button("创建微信草稿", id="action-draft"),
            id="stage-result-actions",
        )

    def update_idle(self) -> None:
        self._set_idle_layout(True)
        self.query_one("#stage-status", Static).update("[#22c55e]● 准备就绪[/]")
        self.query_one("#stage-workflow", Static).update(
            "[#7d8596]1 选择材料   2 理解论文   3 撰写文章[/]\n"
            "[#7d8596]4 质量审阅   5 准备预览[/]"
        )
        self.query_one("#stage-current", Static).update("准备好后，直接在下方输入。")
        self.query_one("#stage-artifacts", Static).update("文章和研究材料会自动保存在本地")
        # The composer is the single entry point. Keep the legacy buttons
        # mounted for keyboard compatibility, but do not present three
        # competing choices on the welcome screen.
        self.query_one("#stage-quick-actions", Horizontal).display = False
        self.query_one("#stage-candidates", ListView).display = False
        self.query_one("#stage-result-actions", Horizontal).display = False
        self.query_one("#quick-connect", Button).display = False

    def set_setup_required(self, required: bool) -> None:
        """Show one clear first-run action when no model is configured."""
        quick_actions = self.query_one("#stage-quick-actions", Horizontal)
        quick_actions.display = required
        for button_id in ("quick-paper", "quick-topic", "quick-continue"):
            self.query_one(f"#{button_id}", Button).display = not required
        self.query_one("#quick-connect", Button).display = required
        if required:
            self.query_one("#stage-current", Static).update("先连接一个模型服务，再开始生成文章。")

    def update_liveness(self, phase: str, elapsed: int) -> None:
        """Keep long-running work visibly alive without exposing internals."""
        self.query_one("#stage-status", Static).update(
            f"[#eab308]● 正在处理[/]  ·  {escape(phase)}  ·  已用时 {elapsed}s"
        )

    def update_running(self, run_state: RunState) -> None:
        self.update_state(run_state)

    def update_completed(self, run_state: RunState) -> None:
        self.update_state(run_state)

    def update_failed(self, message: str, run_state: RunState | None = None) -> None:
        if run_state is not None:
            self.update_state(run_state, error=message)
            return
        self.query_one("#stage-status", Static).update("[#ef4444]● 未完成[/]")
        self.query_one("#stage-current", Static).update(f"失败 · {message}")

    def update_presentation(self, presentation: RunPresentation, run_state: RunState) -> None:
        """Render a workflow-aware result without exposing storage paths."""
        self._set_idle_layout(False)
        status_map = {
            "pending": ("准备中", "#22c55e"),
            "running": ("正在处理", "#eab308"),
            "waiting_input": ("等待选择", "#eab308"),
            "waiting_approval": ("等待确认", "#eab308"),
            "cancelling": ("正在取消", "#eab308"),
            "completed": ("已完成", "#22c55e"),
            "failed": ("未完成", "#ef4444"),
            "cancelled": ("已取消", "#ef4444"),
        }
        label, color = status_map.get(presentation.status, ("准备就绪", "#22c55e"))
        metrics = f"{presentation.progress_current}/{presentation.progress_total}"
        self.query_one("#stage-status", Static).update(
            f"[{color}]● {label}[/]  ·  {escape(presentation.phase)}  ·  {metrics}"
        )
        phases = ("选择材料", "理解论文", "撰写文章", "质量审阅", "准备预览")
        phase_index = phases.index(presentation.phase) if presentation.phase in phases else len(phases) if presentation.status == "completed" else 0
        parts = [
            f"[{'#22c55e' if index < phase_index else '#eab308' if index == phase_index else '#7d8596'}]"
            f"{'✓' if index < phase_index else '●' if index == phase_index else '·'} {phase}[/]"
            for index, phase in enumerate(phases)
        ]
        self.query_one("#stage-workflow", Static).update(
            "\n".join(("   ".join(parts[:3]), "   ".join(parts[3:])))
        )

        subject = f"\n{escape(presentation.subject)}" if presentation.subject else ""
        quality_text = f"  ·  {escape(presentation.quality_verdict)}" if presentation.quality_verdict else ""
        self.query_one("#stage-current", Static).update(
            f"[bold]{escape(presentation.headline)}[/]{quality_text}{subject}"
        )
        issue_text = " · ".join(escape(issue) for issue in presentation.issues)
        if presentation.artifacts and presentation.workflow in {"paper-to-article", "paper-to-wechat"}:
            file_text = "文章已自动保存"
        elif presentation.artifacts:
            file_text = "研究材料已自动保存"
        else:
            file_text = "尚未生成文章"
        self.query_one("#stage-artifacts", Static).update(
            f"{file_text}" + (f"\n建议处理：{issue_text}" if issue_text else f"\n下一步：{escape(presentation.next_action)}")
        )

        self.query_one("#stage-quick-actions", Horizontal).display = False
        candidate_list = self.query_one("#stage-candidates", ListView)
        interaction = presentation.interaction or {}
        options = list(interaction.get("options", [])) if interaction.get("status") == "pending" else []
        candidate_list.display = bool(options) or presentation.status == "waiting_input"
        candidate_list.clear()
        if presentation.status == "waiting_input" and not options:
            empty_message = str(interaction.get("empty_message", "暂时没有找到合适的论文。"))
            suggestions = " · ".join(str(item) for item in interaction.get("suggested_actions", []))
            self.query_one("#stage-current", Static).update(empty_message)
            self.query_one("#stage-artifacts", Static).update(f"下一步：{suggestions or '输入新的研究主题'}")
        for index, option in enumerate(options[:3], 1):
            if not isinstance(option, dict):
                continue
            reasons = " · ".join(str(item) for item in list(option.get("recommendation_reasons", []))[:2])
            contribution = str(option.get("one_sentence_contribution", ""))
            published = str(option.get("published_at", ""))[:10]
            label_text = f"{index}. {option.get('title', '')}\n   {published}  {reasons}\n   {contribution}"
            candidate_list.append(ListItem(Label(label_text), name=str(option.get("paper_id", ""))))

        action_map = {action.id: action for action in presentation.actions}
        result_actions = self.query_one("#stage-result-actions", Horizontal)
        result_actions.display = presentation.status == "completed" and bool(action_map)
        for action_id in ("revise", "preview", "open", "draft"):
            button = self.query_one(f"#action-{action_id}", Button)
            action = action_map.get(action_id)
            button.display = action is not None
            button.disabled = not action.enabled if action else True
        self.query_one("#quick-connect", Button).display = False

    def update_state(self, run_state: RunState, *, error: str = "") -> None:
        if run_state.status.value == "idle" and not run_state.steps:
            self.update_idle()
            return
        self._set_idle_layout(False)

        status_map = {
            "idle": ("准备就绪", "#22c55e"),
            "running": ("正在处理", "#eab308"),
            "success": ("已完成", "#22c55e"),
            "failed": ("未完成", "#ef4444"),
        }
        label, color = status_map[run_state.status.value]
        title = {
            "paper-research": "论文研究",
            "paper-to-article": "文章生成",
            "paper-to-wechat": "公众号文章",
            "daily-digest": "研究日报",
            "publish-existing": "创建微信草稿",
        }.get(run_state.title, "研究任务")
        self.query_one("#stage-status", Static).update(
            f"[{color}]● {label}[/]  ·  {title}"
        )

        step_colors = {
            StepStatus.WAITING: "#7d8596",
            StepStatus.RUNNING: "#eab308",
            StepStatus.SUCCESS: "#22c55e",
            StepStatus.FAILED: "#ef4444",
            StepStatus.SKIPPED: "#7d8596",
        }
        if run_state.steps:
            parts = [
                f"[{step_colors[step.status]}]{step.icon} {step.label}[/]"
                for step in run_state.steps[:6]
            ]
            self.query_one("#stage-workflow", Static).update("   →   ".join(parts))

        if error:
            current = f"失败 · {error}"
        elif run_state.status.value == "running":
            current = "正在处理研究材料，请稍候…"
        elif run_state.status.value == "success":
            current = "运行完成 · 可在下方继续调整文章或发布设置"
        else:
            failed_step = next(
                (step.label for step in run_state.steps if step.status == StepStatus.FAILED),
                title,
            )
            current = f"运行未完成 · {failed_step}"
        self.query_one("#stage-current", Static).update(current)

        if run_state.artifacts:
            names = "   ·   ".join(artifact.name for artifact in run_state.artifacts[-4:])
            artifact_text = f"已保存 · {names}"
        else:
            artifact_text = "文章和研究材料会自动保存在本地"
        self.query_one("#stage-artifacts", Static).update(artifact_text)


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
            list_view.append(ListItem(Label(f" {marker} {title}"), name=s.id))

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
            list_view.append(ListItem(Label(f" {marker} {title}"), name=s.id))

    def update_artifacts_for_run(self, artifacts: list[dict[str, str]]) -> None:
        """Update the artifact section from durable Runtime records."""
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
        yield Label("QUALITY", classes="ctx-section-title")
        yield Vertical(id="ctx-quality")
        yield Label("CONNECTIONS", classes="ctx-section-title")
        yield Vertical(id="ctx-connections")
        yield Label("MODEL", classes="ctx-section-title")
        yield Vertical(id="ctx-model")

    def update_state(self, run_state: RunState) -> None:
        self._update_status(run_state)
        self._update_current_task(run_state)
        self._update_next_actions(run_state)
        self._update_workflows(run_state)
        self._update_artifacts(run_state)
        self._update_stats(run_state)
        self._update_quality(run_state)
        self._update_connections(run_state)
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
            container.mount(Label(f" Agent     {rs.active_agent}", classes="ctx-row"))
        else:
            container.mount(Label(" No model info", classes="ctx-row-muted"))
        if rs.workspace:
            container.mount(Label(f" Workspace {rs.workspace[-18:]}", classes="ctx-row"))

    def _update_quality(self, rs: RunState) -> None:
        container = self.query_one("#ctx-quality", Vertical)
        container.remove_children()
        if not rs.quality:
            container.mount(Label(" Pending", classes="ctx-row-muted"))
            return
        for label, key in (("Technical", "technical_score"), ("WeChat", "wechat_score")):
            value = rs.quality.get(key)
            container.mount(Label(f" {label:<10s} {value if value is not None else '-'}", classes="ctx-row"))

    def _update_connections(self, rs: RunState) -> None:
        container = self.query_one("#ctx-connections", Vertical)
        container.remove_children()
        if not rs.providers:
            container.mount(Label(" Not checked", classes="ctx-row-muted"))
            return
        for name, status in rs.providers.items():
            marker = "●" if status in {"connected", "configured", "available"} else "○"
            container.mount(Label(f" {marker} {name:<10s} {status}", classes="ctx-row"))


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
            f" Parsed            {s.parsed or '-'} / {rs.progress.total if rs.progress else '-'}     Tokens    {s.tokens:,}     Est. Cost  {f'${s.cost:.2f}' if s.cost is not None else 'N/A'}",
        ]
        summary_el.update("\n".join(summary_lines))


# ── Command Palette ──────────────────────────────────────────────────────

COMMANDS = [
    ("connect", "配置并测试模型连接"),
    ("model", "选择本次会话使用的模型"),
    ("reasoning", "设置模型推理强度"),
    ("session", "打开、创建或删除会话"),
    ("diagnose", "查看脱敏连接与运行诊断"),
    ("preview", "打开手机文章预览"),
    ("collect-arxiv", "搜索 arXiv 论文"),
    ("rank-papers", "为论文排序"),
    ("ingest-paper", "解析选中的论文"),
    ("generate-article", "生成公众号文章"),
    ("review-article", "检查文章质量"),
    ("improve-article", "使用模型修改文章"),
    ("create-wechat-draft", "创建微信草稿"),
    ("collect-github", "收集 GitHub 热门项目"),
    ("daily-digest", "生成研究日报"),
    ("trend-analysis", "分析研究趋势"),
    ("check-status", "检查系统状态"),
    ("agent-run", "运行完整流程"),
]


class CommandPalette(Vertical):
    """Modal command palette for quick command execution."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.visible = False
        self._selected_idx = 0
        self._filtered: list[tuple[str, str]] = list(COMMANDS)

    def compose(self) -> ComposeResult:
        yield Static("[bold #f5f5f7]你想做什么？[/]", id="palette-title")
        yield Static("输入关键词筛选，Enter 执行；Esc 关闭", id="palette-hint")
        yield Input(placeholder="搜索操作，例如：模型、会话、预览…", id="palette-input")
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


class SlashCommandPopup(Vertical):
    """Inline command completion shown as soon as the composer starts with /."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._filtered: list[tuple[str, str]] = list(COMMANDS)
        self._allow_display = False

    def compose(self) -> ComposeResult:
        yield Static("命令  ·  ↑↓ 选择  ·  Enter 插入  ·  Esc 关闭", id="slash-title")
        yield ListView(id="slash-list")

    def on_mount(self) -> None:
        self.refresh_commands("", show=False)
        # The popup is an inline completion affordance, never a welcome panel.
        # It becomes visible only after the composer contains '/'.
        self.display = False

    def refresh_commands(self, query: str, *, show: bool = True) -> None:
        needle = query.strip().lstrip("/").lower()
        self._filtered = [
            (name, desc) for name, desc in COMMANDS
            if not needle or needle in name.lower() or needle in desc.lower()
        ]
        self._allow_display = show
        self._refresh_list()

    def _refresh_list(self) -> None:
        listing = self.query_one("#slash-list", ListView)
        listing.clear()
        for name, desc in self._filtered[:8]:
            listing.append(ListItem(Label(f"/ {name:<20} {desc}"), name=name))
        self.display = self._allow_display and bool(self._filtered)

    def first_command(self) -> str | None:
        return self._filtered[0][0] if self._filtered else None


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

    async def _on_key(self, event) -> None:
        """Filter escape sequences from key events."""
        if event.key in {"shift+enter", "ctrl+enter", "alt+enter"} or (
            event.key == "enter" and (getattr(event, "shift", False) or getattr(event, "ctrl", False))
        ):
            event.stop()
            event.prevent_default()
            self.value = cast(str, getattr(self, "value", "")) + "\n"
            return
        # Let parent handle the key first
        await super()._on_key(event)
        # Then clean the value if it contains escape sequences
        value = cast(str, getattr(self, "value", ""))
        if self._clean_text(value) != value:
            self.value = self._clean_text(value)


# ── Input Bar ────────────────────────────────────────────────────────────

class InputBar(Vertical):
    """A quiet composer with one primary action and a visible cancel state."""

    def compose(self) -> ComposeResult:
        yield Horizontal(
            CleanInput(
                placeholder="输入消息，按 Enter 发送...",
                id="chat-input",
            ),
            Button("停止", id="composer-stop"),
            id="composer-row",
        )
        yield Static("模式：安全预览  ·  模型：未配置  ·  推理：默认", id="composer-meta")

    def set_run_mode(self, running: bool) -> None:
        input_widget = self.query_one("#chat-input", Input)
        self.query_one("#composer-stop", Button).display = running
        if running:
            input_widget.placeholder = "正在处理；输入要求可继续调整，按 Esc 取消..."
        else:
            input_widget.placeholder = "输入消息，按 Enter 发送..."

    def get_text(self) -> str:
        return self.query_one("#chat-input", Input).value

    def clear(self) -> None:
        self.query_one("#chat-input", Input).value = ""

    def set_meta(self, model: str, mode: str, reasoning: str = "default") -> None:
        mode_label = {"running": "正在处理", "real": "真实草稿", "dry-run": "安全预览"}.get(mode, mode)
        mode_color = {"running": "#2997ff", "real": "#eab308", "dry-run": "#30d158"}.get(mode, "#f5a623")
        reasoning_label = {"default": "默认", "low": "轻量", "medium": "标准", "high": "深度"}.get(reasoning, reasoning)
        reasoning_color = {"default": "#98989f", "low": "#60a5fa", "medium": "#a78bfa", "high": "#f59e0b"}.get(reasoning, "#98989f")
        model_label = model or "未配置"
        if len(model_label) > 30:
            model_label = model_label[:27] + "..."
        self.query_one("#composer-meta", Static).update(
            f"[#8f8f98]模式：[/][{mode_color}]{escape(mode_label)}[/]"
            f"  [#4a4a52]·[/]  [#8f8f98]模型：[/][#d7dde8]{escape(model_label)}[/]"
            f"  [#4a4a52]·[/]  [#8f8f98]推理强度：[/][{reasoning_color}]{escape(reasoning_label)}[/]"
        )
