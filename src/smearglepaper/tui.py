"""OpenMuse Harness console — effect stage above a conversational composer."""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
)

from .config import DATA_DIR, runtime_settings
from .conversation import ConversationController, offline_request
from .harness import ConversationHarness, ConversationMemory, TurnEvent
from .harness_events import EventJournal, HarnessEvent, normalize_event
from .harness_projection import HarnessProjection
from .preference_memory import PreferenceMemory
from .preference_ui import PreferenceMemoryModal
from .preview_window import launch_mobile_preview_target, select_preview_html
from .run_state import (
    ArtifactInfo,
    RunMode,
    RunState,
    RunStatus,
    StepStatus,
    WorkflowStep,
    load_model_info,
)
from .runtime import AgentRuntime, RunEvent, RunRequest
from .runtime_preview import RuntimePreviewServer
from .session import Session, auto_title, delete_session, list_sessions, load_session, save_session
from .tools import execute_tool
from .trace_message import TraceMessage
from .tui_components import (
    OPENMUSE_LOGO,
    AutoWechatStage,
    CommandPalette,
    InputBar,
    SlashCommandPopup,
)
from .tui_presentation import RunPresentation, build_run_presentation, friendly_error
from .tui_theme import APP_CSS

# ── Clipboard ───────────────────────────────────────────────────────────

def copy_to_clipboard(text: str) -> bool:
    try:
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
        elif sys.platform == "linux":
            subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
        else:
            subprocess.run(["clip"], input=text.encode(), check=True)
        return True
    except Exception:
        return False




class ApprovalModal(ModalScreen[bool]):
    """Explicit confirmation for external side effects."""

    def __init__(self, approval: dict[str, object]) -> None:
        super().__init__()
        self.approval = approval

    def compose(self) -> ComposeResult:
        quality = self.approval.get("quality", {})
        artifacts = list(self.approval.get("artifact_paths", []))
        yield Vertical(
            Label("Publication approval required", classes="ctx-section-title"),
            Static(str(self.approval.get("side_effect", "External write operation"))),
            Static(f"Target: {self.approval.get('target', 'external service')}"),
            Static(
                f"Quality: technical={dict(quality).get('technical_score', '-')} "
                f"wechat={dict(quality).get('wechat_score', '-')}"
            ),
            Static(f"Artifacts: {len(artifacts)} publication inputs"),
            Static(str(self.approval.get("reversible", "Review external rollback procedures."))),
            Horizontal(
                Button("Reject", id="approval-reject", variant="error"),
                Button("Approve", id="approval-approve", variant="success"),
            ),
            id="approval-modal",
        )

    def on_mount(self) -> None:
        self.styles.align = ("center", "middle")

    @on(Button.Pressed)
    def on_button(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "approval-approve")


class ModelConnectModal(ModalScreen[dict[str, object] | None]):
    """Configure and verify an OpenAI-compatible model from inside the TUI."""

    BINDINGS = [Binding("escape", "back", "返回", priority=True)]
    PROVIDERS = {
        "opencode-zen": {
            "name": "OpenCode Zen",
            "url": "https://opencode.ai/zen/v1",
            "model": "mimo-v2.5-free",
            "requires_key": True,
            "free_only_in_opencode": True,
        },
        "opencode-go": {
            "name": "OpenCode Go",
            "url": "https://opencode.ai/zen/go/v1",
            "model": "mimo-v2.5-pro",
            "requires_key": True,
        },
        "openai": {"name": "OpenAI", "url": "https://api.openai.com/v1", "model": "", "requires_key": True},
        "custom": {"name": "自定义兼容接口", "url": "", "model": "", "requires_key": True},
    }

    def __init__(self, settings: dict[str, str | bool]) -> None:
        super().__init__()
        self.settings = settings
        self._provider = ""
        self._testing = False

    def compose(self) -> ComposeResult:
        base_url = str(self.settings.get("base_url", ""))
        model = str(self.settings.get("model", ""))
        configured = bool(self.settings.get("api_key_configured"))
        yield Vertical(
            Label("连接模型服务", classes="connect-title"),
            Static("选择服务商，下一步填写连接信息", id="connect-step-title"),
            ListView(
                ListItem(Label("OpenCode Zen"), Label("免费层仅限 OpenCode；付费模型可接入", classes="provider-description"), name="opencode-zen"),
                ListItem(Label("OpenCode Go"), Label("Go 订阅接口", classes="provider-description"), name="opencode-go"),
                ListItem(Label("OpenAI"), Label("使用 OpenAI API Key", classes="provider-description"), name="openai"),
                ListItem(Label("自定义兼容接口"), Label("DeepSeek、MiMo 或其他网关", classes="provider-description"), name="custom"),
                id="connect-providers",
            ),
            Vertical(
                Static("OpenCode Zen · OpenAI-compatible", id="connect-provider-name"),
                Label("Base URL"),
                Input(value=base_url, placeholder="https://api.example.com/v1", id="connect-base-url"),
                Label("模型"),
                Input(value=model, placeholder="例如：mimo-v2.5-pro", id="connect-model"),
                Label("API Key"),
                Input(
                    placeholder="粘贴当前服务商的 API Key" if not configured else "留空表示保留当前 Key",
                    password=True,
                    id="connect-api-key",
                ),
                Static("", id="connect-status"),
                Horizontal(
                    Button("返回", id="connect-back"),
                    Button("取消", id="connect-cancel"),
                    Button("仅保存", id="connect-save"),
                    Button("保存并测试", id="connect-test", variant="primary"),
                ),
                id="connect-config",
            ),
            Static("↑↓ 选择   Enter 继续   Esc 关闭", id="connect-hint"),
            id="model-connect-modal",
        )

    def on_mount(self) -> None:
        self.styles.align = ("center", "middle")
        self.set_class(self.size.height < 30 or self.size.width < 100, "small-terminal")
        self.query_one("#connect-config").display = False
        self.query_one("#connect-providers", ListView).focus()

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(event.size.height < 30 or event.size.width < 100, "small-terminal")

    def action_back(self) -> None:
        if self._testing:
            return
        if not self.query_one("#connect-config").display:
            self.dismiss(None)
            return
        self.query_one("#connect-config").display = False
        self.query_one("#connect-providers").display = True
        self.query_one("#connect-step-title", Static).update("选择服务商，下一步填写连接信息")
        self.query_one("#connect-hint", Static).update("↑↓ 选择   Enter 继续   Esc 关闭")
        self.query_one("#connect-providers", ListView).focus()

    @on(ListView.Selected, "#connect-providers")
    def on_provider_selected(self, event: ListView.Selected) -> None:
        provider = str(event.item.name or "")
        if provider not in self.PROVIDERS:
            return
        provider_info = self.PROVIDERS[provider]
        provider_name = str(provider_info["name"])
        base_url = str(provider_info["url"])
        requires_key = bool(provider_info["requires_key"])
        if provider != self._provider:
            self.query_one("#connect-base-url", Input).value = base_url or str(self.settings.get("base_url", ""))
            self.query_one("#connect-api-key", Input).value = ""
            self._set_status("API Key 仅保存在本机，不会显示在对话中。")
            provider_model = str(provider_info.get("model", ""))
            if provider_model:
                self.query_one("#connect-model", Input).value = provider_model
        self._provider = provider
        self.query_one("#connect-provider-name", Static).update(provider_name)
        self.query_one("#connect-step-title", Static).update("填写地址、模型和密钥")
        self.query_one("#connect-hint", Static).update("Tab 切换输入项   Esc 返回服务商")
        self.query_one("#connect-providers", ListView).display = False
        self.query_one("#connect-config").display = True
        key_input = self.query_one("#connect-api-key", Input)
        key_input.disabled = not requires_key
        key_input.placeholder = "粘贴当前服务商的 API Key" if requires_key else "此服务商无需 API Key"
        self.query_one("#connect-base-url", Input).focus()

    @on(Button.Pressed)
    def on_button(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "connect-back":
            self.action_back()
            return
        if button_id == "connect-cancel":
            self.dismiss(None)
            return
        from .config import save_openai_connection

        base_url = self.query_one("#connect-base-url", Input).value.strip()
        model = self.query_one("#connect-model", Input).value.strip()
        provider_info = self.PROVIDERS.get(self._provider, self.PROVIDERS["custom"])
        requires_key = bool(provider_info["requires_key"])
        api_key = self.query_one("#connect-api-key", Input).value.strip() or None
        if requires_key and api_key is None and not self.settings.get("api_key_configured"):
            self._set_status("请输入当前服务商的 API Key 后再测试连接。", error=True)
            self.query_one("#connect-api-key", Input).focus()
            return
        old_url = urlsplit(str(self.settings.get("base_url", "")))
        new_url = urlsplit(base_url)
        if requires_key and api_key is None and self.settings.get("api_key_configured") and (
            old_url.scheme, old_url.netloc
        ) != (new_url.scheme, new_url.netloc):
            self._set_status("更换服务地址时请填写对应 API Key，避免发送原服务的密钥。", error=True)
            self.query_one("#connect-api-key", Input).focus()
            return
        try:
            if not base_url or not model:
                raise ValueError("地址和模型不能为空。")
        except ValueError as exc:
            self._set_status(str(exc), error=True)
            return
        if button_id == "connect-save":
            try:
                save_openai_connection(base_url, model, api_key, clear_api_key=not requires_key)
            except ValueError as exc:
                self._set_status(str(exc), error=True)
                return
            self.dismiss({"saved": True, "tested": False, "model": model})
            return
        if bool(provider_info.get("free_only_in_opencode")) and model.endswith("-free"):
            self._set_status(
                "OpenCode Zen 的免费模型只能在 OpenCode 内使用。请改用 OpenCode Go，"
                "或填写 Zen 的付费模型后再测试。",
                error=True,
            )
            return
        self._set_busy(True)
        self._set_status("正在验证连接……")
        self._test_connection(base_url, api_key or "", model)

    @work(thread=True, exclusive=True)
    def _test_connection(self, base_url: str, api_key: str, model: str) -> None:
        from .llm import check_openai_connection

        try:
            result = check_openai_connection(base_url, api_key or "", model)
        except Exception as exc:  # noqa: BLE001 - surface provider failures in the modal
            result = {"ok": False, "provider": "openai", "model": model, "error": f"{type(exc).__name__}: {exc}"}
        self.app.call_from_thread(self._connection_checked, result, model)

    def _connection_checked(self, result: dict[str, object], model: str) -> None:
        from .config import save_openai_connection

        self._set_busy(False)
        if result.get("ok"):
            try:
                provider_info = self.PROVIDERS.get(self._provider, self.PROVIDERS["custom"])
                save_openai_connection(
                    self.query_one("#connect-base-url", Input).value.strip(),
                    model,
                    self.query_one("#connect-api-key", Input).value.strip() or None,
                    clear_api_key=not bool(provider_info.get("requires_key", True)),
                )
            except ValueError as exc:
                self._set_status(str(exc), error=True)
                return
            self.dismiss({"saved": True, "tested": True, "model": model, "result": result})
            return
        status_code = result.get("status_code")
        detail = str(result.get("error", "网络或接口错误"))
        prefix = f"HTTP {status_code}" if status_code else "网络错误"
        self._set_status(f"连接失败（{prefix}）：{detail}\n当前失败配置尚未覆盖已保存配置。", error=True)

    def _set_busy(self, busy: bool) -> None:
        self._testing = busy
        for button_id in ("connect-back", "connect-cancel", "connect-save", "connect-test"):
            self.query_one(f"#{button_id}", Button).disabled = busy
        for input_widget in self.query(Input):
            input_widget.disabled = busy
        if not busy:
            requires_key = bool(self.PROVIDERS.get(self._provider, {}).get("requires_key", True))
            self.query_one("#connect-api-key", Input).disabled = not requires_key

    def _set_status(self, text: str, *, error: bool = False) -> None:
        status = self.query_one("#connect-status", Static)
        status.update(text)
        status.set_class(error, "connect-error")


class ModelPickerModal(ModalScreen[dict[str, object] | None]):
    """Choose the active model without leaving the conversation."""

    BINDINGS = [Binding("escape", "close", "关闭", priority=True)]

    def action_close(self) -> None:
        self.dismiss(None)

    def __init__(self, current: str | None) -> None:
        super().__init__()
        self.current = current or "provider default"
        self._models = self._model_choices()

    def _model_choices(self) -> list[str]:
        from .config import env

        configured = env("OPENAI_MODEL")
        choices = [self.current, configured, "mimo-v2.5-pro", "mimo-v2.5", "deepseek-chat"]
        result: list[str] = []
        for model in choices:
            name = model.strip()
            if name and name not in result:
                result.append(name)
        return result

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("选择模型", classes="ctx-section-title"),
            Static("当前选择只影响本次会话；需要更换接口或 API Key 请使用 /connect。"),
            Input(placeholder="筛选模型…", id="model-filter"),
            ListView(id="model-list"),
            Static("", id="model-picker-status"),
            Horizontal(
                Button("刷新模型", id="model-refresh"),
                Button("连接设置", id="model-connect"),
                Button("取消", id="model-cancel"),
            ),
            id="model-picker-modal",
        )

    def on_mount(self) -> None:
        self._refresh_list("")
        self.query_one("#model-filter", Input).focus()

    def _refresh_list(self, query: str) -> None:
        container = self.query_one("#model-list", ListView)
        container.clear()
        needle = query.strip().lower()
        for model in self._models:
            if needle and needle not in model.lower():
                continue
            marker = "●" if model == self.current else "○"
            container.append(ListItem(Label(f" {marker} {model}"), name=model))

    @on(Input.Changed, "#model-filter")
    def on_filter_changed(self, event: Input.Changed) -> None:
        self._refresh_list(event.value)

    @on(Input.Submitted, "#model-filter")
    def on_filter_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        visible = self.query_one("#model-list", ListView)
        if visible.children:
            first = visible.children[0]
            model = str(getattr(first, "name", "") or "")
            if model:
                self.dismiss({"model": model})

    @on(ListView.Selected, "#model-list")
    def on_model_selected(self, event: ListView.Selected) -> None:
        model = str(event.item.name or "")
        if model:
            self.dismiss({"model": model})

    @on(Button.Pressed)
    def on_button(self, event: Button.Pressed) -> None:
        if event.button.id == "model-refresh":
            self._refresh_remote_models()
        elif event.button.id == "model-connect":
            self.dismiss({"connect": True})
        elif event.button.id == "model-cancel":
            self.dismiss(None)

    @work(thread=True, exclusive=True)
    def _refresh_remote_models(self) -> None:
        from .config import env
        from .llm import list_openai_models

        self.app.call_from_thread(self._set_refresh_status, "正在读取服务端模型…", False)
        try:
            models = list_openai_models(env("OPENAI_BASE_URL"), env("OPENAI_API_KEY"))
        except Exception as exc:
            self.app.call_from_thread(self._set_refresh_status, f"读取失败：{type(exc).__name__}", True)
            return
        self.app.call_from_thread(self._merge_remote_models, models)

    def _merge_remote_models(self, models: list[str]) -> None:
        for model in models:
            if model not in self._models:
                self._models.append(model)
        self._refresh_list(self.query_one("#model-filter", Input).value)
        self._set_refresh_status(f"已读取 {len(models)} 个服务端模型。", False)

    def _set_refresh_status(self, text: str, error: bool) -> None:
        status = self.query_one("#model-picker-status", Static)
        status.update(text)
        status.set_class(error, "connect-error")


class SessionPickerModal(ModalScreen[dict[str, object] | None]):
    """Open, create, or remove saved conversations."""

    BINDINGS = [Binding("escape", "close", "关闭", priority=True)]

    def action_close(self) -> None:
        self.dismiss(None)

    def __init__(self, sessions: list[Session], active_id: str) -> None:
        super().__init__()
        self.sessions = sessions
        self.active_id = active_id
        self._selected_id = ""

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("会话", classes="ctx-section-title"),
            Static("选择一个已保存的工作，或开始新的对话。"),
            ListView(id="session-picker-list"),
            Static("", id="session-picker-status"),
            Horizontal(
                Button("新建会话", id="session-new", variant="primary"),
                Button("删除选中", id="session-delete", variant="error"),
                Button("关闭", id="session-cancel"),
            ),
            id="session-picker-modal",
        )

    def on_mount(self) -> None:
        container = self.query_one("#session-picker-list", ListView)
        if not self.sessions:
            container.append(ListItem(Label("  暂无已保存会话"), name=""))
        for session in self.sessions[:20]:
            marker = "●" if session.id == self.active_id else "○"
            title = session.title.strip() or "新对话"
            if len(title) > 42:
                title = title[:39] + "..."
            container.append(ListItem(Label(f" {marker} {title}"), name=session.id))
        if self.sessions:
            container.focus()

    @on(ListView.Selected, "#session-picker-list")
    def on_session_selected(self, event: ListView.Selected) -> None:
        session_id = str(event.item.name or "")
        if session_id:
            self.dismiss({"open": session_id})

    @on(ListView.Highlighted, "#session-picker-list")
    def on_session_highlighted(self, event: ListView.Highlighted) -> None:
        self._selected_id = str(event.item.name or "")

    @on(Button.Pressed)
    def on_button(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "session-new":
            self.dismiss({"new": True})
        elif button_id == "session-delete":
            if not self._selected_id:
                self.query_one("#session-picker-status", Static).update("先在列表中选中一个会话，再删除。")
            else:
                self.dismiss({"delete": self._selected_id})
        elif button_id == "session-cancel":
            self.dismiss(None)


# ── Main Screen ──────────────────────────────────────────────────────────

class AgentConsole(Screen):
    CSS = APP_CSS
    BINDINGS = [
        Binding("ctrl+n", "new_session", "New Session"),
        Binding("ctrl+r", "demo_run", "Demo Run"),
        Binding("ctrl+s", "save_session", "Save"),
        Binding("ctrl+c", "copy_selection", "Copy"),
        Binding("ctrl+l", "clear_screen", "Clear"),
        Binding("ctrl+d", "toggle_details", "Details"),
        Binding("ctrl+o", "open_preview", "Mobile Preview"),
        Binding("ctrl+p", "toggle_palette", "Commands"),
        Binding("ctrl+b", "toggle_context", "Context"),
        Binding("escape", "dismiss_escape", "Close / Cancel", priority=True),
        Binding("alt+left", "previous_agent", "Previous Agent"),
        Binding("alt+right", "next_agent", "Next Agent"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.session = Session.create()
        self.run_state = RunState(model=load_model_info())
        self._busy = False
        self._controller_running = False
        self._recovery_notice = ""
        self._last_agent_reply = ""
        self._context_visible = True
        self._welcome_visible = True
        self._trace_messages: list[TraceMessage] = []
        self.runtime = AgentRuntime()
        self._preference_memory = PreferenceMemory(DATA_DIR / "preferences.sqlite3")
        self._active_run_id = ""
        self._active_agent = "main"
        self._agents = ["main", "scout", "reader", "writer", "technical-reviewer", "wechat-editor", "publisher"]
        self._pending_approval = ""
        self._tool_details: dict[int, str] = {}
        self._last_runtime_error = ""
        self._details_visible = False
        self._presentation: RunPresentation | None = None
        self._summary_run_id = ""
        model_info = load_model_info()
        self._selected_model = model_info.name if model_info.name and model_info.name != "unknown" else None
        self._reasoning_effort = "default"
        self._real_mode = False
        self._preview_server: RuntimePreviewServer | None = None
        self._run_started_monotonic = 0.0
        self._streaming_message: TraceMessage | None = None
        self._activity_message: TraceMessage | None = None
        self._stream_render_pending = False
        self._event_journal = EventJournal(DATA_DIR / "sessions" / f"{self.session.id}.events.jsonl")
        self._conversation_harness = ConversationHarness(
            self.session.id,
            ConversationMemory(DATA_DIR / "sessions" / f"{self.session.id}.memory.json"),
            event_journal=self._event_journal,
        )
        self._harness_projection = HarnessProjection()
        self.runtime.attach_event_journal(self.session.id, self._event_journal)

    def compose(self) -> ComposeResult:
        yield Static("", id="session-bar")
        with Vertical(id="main-container"):
            yield AutoWechatStage(id="effect-stage")
            with Vertical(id="conversation-area"):
                yield Static("对话", id="conversation-label")
                yield Static(
                    "直接告诉我你想做什么\n\n"
                    "例如：\n"
                    "  粘贴论文链接，我会提炼成中文文章\n"
                    "  输入研究主题，我会先帮你找合适的论文\n"
                    "  对当前文章说：标题更克制一点\n\n"
                    "输入 /help 查看更多操作",
                    id="conversation-empty",
                )
                yield RichLog(id="trace-area", markup=True, highlight=True, wrap=True)
                yield Button("继续未完成的对话", id="resume-turn", variant="primary")
                yield Button("有待确认的偏好 · 查看", id="memory-review")
                yield InputBar(id="input-bar")

        yield Static(self._footer_text(), id="footer-bar")
        yield SlashCommandPopup(id="slash-command-popup")
        yield CommandPalette(id="command-palette")

    def _footer_text(self) -> str:
        return (
            "[#eab308]^N[/] 新对话   "
            "[#eab308]^P[/] 命令   "
            "[#eab308]/model[/] 模型   "
            "[#eab308]/session[/] 会话   "
            "[#eab308]^S[/] 保存   "
            "[#eab308]^D[/] 详情   "
            "[#eab308]^O[/] 预览   "
            "[#eab308]^Q[/] 退出"
        )

    def on_mount(self) -> None:
        self.set_class(self.size.height < 30 or self.size.width < 100, "small-terminal")
        stage = self.query_one("#effect-stage", AutoWechatStage)
        stage.update_idle()
        if dict(runtime_settings().get("llm", {})).get("provider") == "none":
            stage.query_one("#stage-current", Static).update("尚未配置模型。请先设置 API Key 和模型地址，再开始生成文章。")
            stage.set_setup_required(True)
        self.query_one("#quick-continue", Button).disabled = not any(session.messages or session.run_ids for session in list_sessions())
        self.query_one("#resume-turn", Button).display = False
        self._refresh_memory_review()
        self.query_one("#trace-area", RichLog).display = False
        self.query_one("#conversation-empty", Static).display = True
        self.query_one("#chat-input", Input).focus()
        self._welcome_visible = True
        self._update_session_bar()
        self._update_input_placeholder()
        self.set_interval(1.0, self._refresh_liveness)
        self.set_interval(0.05, self._flush_stream_render)

    def _refresh_liveness(self) -> None:
        """Keep the composer honest during long network/model operations."""
        if not self._busy or not self._run_started_monotonic:
            return
        elapsed = int(time.monotonic() - self._run_started_monotonic)
        current = self._harness_projection.state.activity or "准备工作"
        self.query_one("#input-bar", InputBar).set_meta(
            self._selected_model or (self.run_state.model.name if self.run_state.model else "local"),
            "real" if self._real_mode else "dry-run",
            self._reasoning_effort,
        )
        self.query_one("#effect-stage", AutoWechatStage).update_liveness(current, elapsed)

    def on_resize(self, event: events.Resize) -> None:
        self.query_one("#effect-stage", AutoWechatStage).set_compact(event.size.height < 30)
        self.set_class(event.size.height < 30 or event.size.width < 100, "small-terminal")
        self._update_session_bar()

    # ── Welcome / Empty State ────────────────────────────────────────────

    def _show_welcome(self) -> None:
        self.query_one("#effect-stage").display = True
        stage = self.query_one("#effect-stage", AutoWechatStage)
        stage.update_idle()
        stage.set_setup_required(dict(runtime_settings().get("llm", {})).get("provider") == "none")
        self.query_one("#trace-area", RichLog).display = False
        self.query_one("#conversation-empty", Static).display = True
        self.query_one("#conversation-area").remove_class("has-messages")
        self._details_visible = False
        self._welcome_visible = True
        self._update_session_bar()

    def _hide_welcome(self) -> None:
        if self._welcome_visible:
            self.query_one("#trace-area", RichLog).display = True
            self.query_one("#conversation-empty", Static).display = False
            self.query_one("#conversation-area").add_class("has-messages")
            self._welcome_visible = False
        self.query_one("#effect-stage").display = bool(self._presentation or self.run_state.steps)

    def _check_llm_on_start(self) -> None:
        """Check LLM connection on startup and show status."""
        from .llm import check_llm_connection
        status = check_llm_connection()
        if status.get("ok"):
            # LLM is connected, ready for chat
            return
        else:
            # LLM not available, show hint
            pass

    def _show_run_block(self) -> None:
        self.query_one("#effect-stage", AutoWechatStage).update_state(
            self.run_state, error=self._last_runtime_error
        )

    def _hide_run_block(self) -> None:
        return

    # ── Message Display ──────────────────────────────────────────────────

    def _now(self) -> str:
        from datetime import datetime
        return datetime.now().strftime("%H:%M:%S")

    def _append_trace(self, tag: str, tag_class: str, text: str) -> None:
        log = self.query_one("#trace-area", RichLog)
        # Handle multi-line text with proper alignment
        lines = text.split("\n")
        first_line = lines[0] if lines else ""
        log.write(f"[#7d8596]{self._now()}[/] [#303642]│[/] [{tag_class}]{tag}[/] {first_line}")
        for line in lines[1:]:
            log.write(f"[#7d8596]         │[/] {line}")

    def _render_trace(self) -> None:
        log = self.query_one("#trace-area", RichLog)
        following = log.is_vertical_scroll_end
        scroll_y = log.scroll_y
        log.clear()
        for message in self._trace_messages:
            if self._details_visible or not message.detail_only:
                log.write(message.to_renderable(details=self._details_visible), scroll_end=following)
        if not following:
            log.scroll_to(y=scroll_y, animate=False, force=True)

    def _schedule_trace_render(self) -> None:
        """Batch token updates so long streamed replies do not redraw per token."""
        self._stream_render_pending = True

    def _flush_stream_render(self) -> None:
        # A repeating tick recovers after a busy frame; a skipped one-shot
        # timer could leave _stream_render_pending permanently set.
        if self._stream_render_pending:
            self._stream_render_pending = False
            self._render_trace()

    def _record_trace(self, message: TraceMessage) -> None:
        if not message.detail_only:
            self._hide_welcome()
        self._trace_messages.append(message)
        self._render_trace()

    def _append_system(self, text: str, *, detail_only: bool = False) -> None:
        self._record_trace(TraceMessage.create("system", text, detail_only=detail_only))

    def _append_user(self, text: str) -> None:
        self._record_trace(TraceMessage.create("user", text))

    def _append_agent(self, text: str, *, detail_only: bool = False) -> None:
        self._last_agent_reply = text
        display_text = text.removeprefix("OpenMuse：").removeprefix("AutoWechat：")
        self._record_trace(TraceMessage.create("agent", display_text, detail_only=detail_only))

    def _begin_streaming_agent(self) -> None:
        message = TraceMessage.create("agent", "")
        self._streaming_message = message
        self._trace_messages.append(message)
        self._render_trace()

    def _append_stream_delta(self, text: str) -> None:
        if self._streaming_message is None:
            self._begin_streaming_agent()
        assert self._streaming_message is not None
        self._streaming_message.content += text
        self._last_agent_reply = self._streaming_message.content
        self._schedule_trace_render()

    def _set_activity(self, text: str) -> None:
        """Update one compact progress line in the normal conversation view."""
        if self._activity_message is None:
            self._activity_message = TraceMessage.create("activity", text)
            self._trace_messages.append(self._activity_message)
        else:
            self._activity_message.content = text
        self._schedule_trace_render()

    def _clear_activity(self) -> None:
        if self._activity_message is not None:
            self._trace_messages = [m for m in self._trace_messages if m is not self._activity_message]
            self._activity_message = None
            self._render_trace()

    def _append_tool(self, name: str, result: str, *, detail_only: bool = False) -> None:
        self._record_trace(TraceMessage.create("tool", f"{name}\n  {result}", detail_only=detail_only))

    def _append_step(self, name: str, detail: str) -> None:
        self._record_trace(TraceMessage.create("step", f"{name}\n  {detail}", detail_only=True))

    def _append_artifact(self, path: str, producer: str = "") -> None:
        name = Path(path).name or path
        label = f"FILE {name}" + (f"  ·  {producer}" if producer else "")
        self._record_trace(TraceMessage.create("artifact", label, artifact_path=path, detail_only=True))

    def _append_error(self, text: str, *, detail_only: bool = False) -> None:
        self._record_trace(TraceMessage.create("error", text, detail_only=detail_only))

    def _append_run_summary(self) -> None:
        if not self._active_run_id or not self._presentation:
            return
        if self._presentation.status not in {"completed", "failed", "cancelled"}:
            return
        if self._summary_run_id == self._active_run_id:
            return
        self._summary_run_id = self._active_run_id
        self._append_agent(self._presentation.summary_text)

    # ── State Updates ────────────────────────────────────────────────────

    def _update_header(self) -> None:
        stage = self.query_one("#effect-stage", AutoWechatStage)
        stage.display = self._welcome_visible or bool(self._presentation or self.run_state.steps)
        self._update_session_bar()
        if self._presentation:
            stage.update_presentation(self._presentation, self.run_state)
            draft = self.query_one("#action-draft", Button)
            wechat = dict(runtime_settings().get("wechat", {}))
            if draft.display and not all(wechat.values()):
                draft.disabled = True
                draft.tooltip = "配置微信公众号后可创建真实草稿"
        else:
            stage.update_state(self.run_state, error=self._last_runtime_error)

    def _update_session_bar(self) -> None:
        """Keep the editor context visible without introducing a sidebar."""
        bar = self.query_one("#session-bar", Static)
        title = self.session.title.strip() or "新对话"
        if len(title) > 34:
            title = title[:31] + "..."
        model = self._selected_model or (self.run_state.model.name if self.run_state.model else "未配置模型")
        if len(model) > 24:
            model = model[:21] + "..."
        status, color = {
            "running": ("处理中", "#2997ff"),
            "completed": ("已完成", "#30d158"),
            "failed": ("需要处理", "#ff453a"),
            "interrupted": ("可以恢复", "#eab308"),
            "recovery_required": ("需要核对", "#eab308"),
            "waiting_input": ("等待选择", "#ff9f0a"),
            "waiting_approval": ("等待确认", "#ff9f0a"),
            "cancelling": ("正在取消", "#ff9f0a"),
            "cancelled": ("已取消", "#98989f"),
        }.get(self._harness_projection.state.status, ("就绪", "#98989f"))
        text = Text("OpenMuse  ·  ", style="bold #f5f5f7", no_wrap=True, overflow="ellipsis")
        text.append(title, style="#d1d1d6")
        if self.size.width >= 100:
            text.append(f"  ·  {model}", style="#98989f")
        text.append(f"  ·  {status}", style=color)
        bar.update(text)

    def _update_context(self) -> None:
        return

    # ── Input Handling ───────────────────────────────────────────────────

    @on(Input.Submitted, "#chat-input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        # Filter out terminal escape sequences (Kitty keyboard protocol etc.)
        text = re.sub(r'\[[\d;]*[A-Za-z]', '', text)
        text = re.sub(r'\[[\d;]*:\d+u', '', text)
        text = text.strip()
        if not text:
            return
        event.input.value = ""

        # Check if command palette is open
        palette = self.query_one("#command-palette", CommandPalette)
        if palette.visible:
            self._handle_palette_input(text)
            return

        self._handle_user_input(text)

    @on(Input.Changed, "#chat-input")
    def on_chat_input_changed(self, event: Input.Changed) -> None:
        popup = self.query_one("#slash-command-popup", SlashCommandPopup)
        value = event.value
        if value.startswith("/") and " " not in value and "\n" not in value:
            popup.refresh_commands(value)
        else:
            popup.display = False

    @on(ListView.Selected, "#slash-list")
    def on_slash_command_selected(self, event: ListView.Selected) -> None:
        command = str(event.item.name or "")
        if not command:
            return
        popup = self.query_one("#slash-command-popup", SlashCommandPopup)
        popup.display = False
        input_widget = self.query_one("#chat-input", Input)
        input_widget.value = f"/{command} "
        input_widget.focus()

    def on_key(self, event: events.Key) -> None:
        popup = self.query_one("#slash-command-popup", SlashCommandPopup)
        input_widget = self.query_one("#chat-input", Input)
        if not popup.display or not input_widget.has_focus:
            return
        if event.key == "escape":
            popup.display = False
            # Match OpenCode: Esc cancels the command hint and removes the
            # trigger character so the composer returns to a normal prompt.
            input_widget.value = ""
            input_widget.focus()
        elif event.key == "tab":
            command = popup.first_command()
            if command:
                input_widget.value = f"/{command} "
                popup.display = False
                event.stop()
        elif event.key == "down":
            popup.query_one("#slash-list", ListView).focus()
            event.stop()

    @on(Button.Pressed)
    def on_stage_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "composer-stop":
            self.action_cancel_run()
            return
        if button_id == "resume-turn":
            self.action_resume_turn()
            return
        if button_id == "memory-review":
            if not self._busy:
                self._show_memory()
            return
        input_widget = self.query_one("#chat-input", Input)
        if button_id == "quick-paper":
            input_widget.placeholder = "粘贴 arXiv 论文链接或输入论文 ID…"
            input_widget.focus()
        elif button_id == "quick-topic":
            input_widget.placeholder = "输入研究主题，例如：多智能体协作…"
            input_widget.focus()
        elif button_id == "quick-continue":
            previous = next((session for session in list_sessions() if session.messages or session.run_ids), None)
            if previous:
                self._reload_session(previous)
            else:
                self._append_system("还没有可以继续的历史任务。")
        elif button_id == "quick-connect":
            self._open_model_connect()
        elif button_id == "action-revise":
            input_widget.placeholder = "告诉我想怎么修改，例如：标题更克制、方法部分更深入…"
            input_widget.focus()
        elif button_id == "action-preview":
            self.action_open_preview()
        elif button_id == "action-open":
            self._open_current_article()
        elif button_id == "action-draft":
            wechat = dict(runtime_settings().get("wechat", {}))
            if not wechat or not all(wechat.values()):
                self._append_system("创建真实微信草稿前，需要先配置微信公众号 AppID 和 AppSecret。")
            else:
                self._create_current_draft()

    @on(ListView.Selected, "#stage-candidates")
    def on_candidate_selected(self, event: ListView.Selected) -> None:
        paper_id = str(event.item.name or "")
        interaction = self._presentation.interaction if self._presentation else None
        options = list(interaction.get("options", [])) if interaction else []
        selected = next(
            (option for option in options if isinstance(option, dict) and str(option.get("paper_id", "")) == paper_id),
            {},
        )
        self._choose_candidate(paper_id, str(selected.get("title", "")))


    def _handle_user_input(self, text: str) -> None:
        if self._busy:
            if text.strip() == "/cancel":
                self.action_cancel_run()
            else:
                self.query_one("#chat-input", Input).value = text
                self._append_system("当前任务仍在进行，已保留你的输入。可以等待完成，或先按 Esc 取消。")
            return
        if text.startswith("/"):
            self._handle_slash_command(text)
            return
        store = self._conversation_harness.turn_store
        try:
            pending = store.pending() if store else False
        except RuntimeError as exc:
            self.query_one("#chat-input", Input).value = text
            self._append_error(str(exc))
            self._refresh_recovery()
            return
        if pending:
            self.query_one("#chat-input", Input).value = text
            self._append_system("上次对话尚未完成，请先点击继续或输入 /resume；也可以新建会话。")
            self._refresh_recovery()
            return
        if dict(runtime_settings().get("llm", {})).get("provider") == "none":
            self.query_one("#chat-input", Input).value = text
            self._append_system("请先用 /connect 配置模型；也可以用 /offline 查看明确标注的离线示例。")
            return
        self._hide_welcome()
        self._append_user(text)
        self.session.messages.append({"role": "user", "content": text})
        save_session(self.session)
        # Claim the slot before scheduling: rapid input must not replace a turn.
        self._busy = True
        self._run_chat_turn(text)


    def _choose_candidate(self, paper_id: str, title: str) -> None:
        if self._busy or not self._active_run_id or not paper_id:
            return
        self._hide_welcome()
        self._append_user(f"选择：{title or paper_id}")
        self.session.messages.append({"role": "user", "content": f"选择：{title or paper_id}"})
        self._resume_candidate(paper_id)



    def _open_current_article(self) -> None:
        if not self._active_run_id:
            self._append_system("当前还没有文章可以打开。")
            return
        source = select_preview_html(self.runtime.get_run(self._active_run_id).get("artifacts", []))
        if source is None:
            self._append_system("当前运行尚未生成文章 HTML。")
            return
        if not webbrowser.open(source.as_uri(), new=1):
            self._append_error("系统浏览器无法打开文章。")


    def _create_current_draft(self) -> None:
        if self._busy:
            self._append_system("请等待当前任务完成后再创建草稿。")
            return
        self._append_user("请求创建微信草稿")
        self.session.messages.append({"role": "user", "content": "请求创建微信草稿"})
        self._busy = True
        self._run_controller_action("request_draft", {}, "请求创建微信草稿")

    def _handle_slash_command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()

        if cmd == "/clear":
            self.action_clear_screen()
        elif cmd == "/status":
            self._run_check_status()
        elif cmd == "/diagnose":
            self._show_diagnostics()
        elif cmd == "/delete":
            delete_session(self.session.id)
            self._append_system(f"会话 {self.session.id[:8]} 已删除")
            self.action_new_session()
        elif cmd == "/demo-run":
            self.action_demo_run()
        elif cmd == "/offline":
            self._busy = True
            self._run_offline_example(parts[1] if len(parts) > 1 else "离线示例")
        elif cmd == "/cancel":
            self.action_cancel_run()
        elif cmd == "/resume":
            self.action_resume_turn()
        elif cmd == "/approve" and self._pending_approval:
            self._resolve_approval(self._pending_approval, True)
        elif cmd == "/details":
            self.action_toggle_details()
        elif cmd == "/memory":
            argument = parts[1].strip().lower() if len(parts) > 1 else ""
            if argument in {"on", "off"}:
                try:
                    enabled = argument == "on"
                    self._preference_memory.set_enabled(enabled)
                    self._record_memory_action("memory.settings_changed", {"enabled": enabled})
                    self._append_system("已启用偏好记忆。" if enabled else "已暂停偏好记忆：不会检索或由模型新增提议；已有偏好保留，可在 /memory 中删除。")
                except (ValueError, OSError) as exc:
                    self._append_error(str(exc))
            else:
                self._show_memory()
        elif cmd == "/forget":
            try:
                if len(parts) > 1:
                    identifier = parts[1].strip()
                    self._preference_memory.forget(identifier)
                    self._record_memory_action("memory.forgotten", {"id": identifier})
                    self._append_system("已忘记这条偏好，之后不再从偏好库读取。聊天记录和文章仍保留。")
                elif self._conversation_harness.turn_store and self._conversation_harness.turn_store.pending():
                    self._append_system("当前有待恢复的对话。请先完成恢复或新建会话，避免旧快照重新带回已清空的上下文。")
                else:
                    self._conversation_harness.memory.clear()
                    self._append_system("已清空本会话上下文。长期偏好请在 /memory 中删除；聊天记录、任务和文章未删除。")
            except (ValueError, RuntimeError, OSError) as exc:
                self._append_error(str(exc))
        elif cmd in {"/session", "/sessions", "/seesion"}:
            argument = parts[1].strip() if len(parts) > 1 else ""
            if argument.lower() in {"new", "create"}:
                self.action_new_session()
            elif argument.lower() in {"list", "ls"} or not argument:
                self._open_session_picker()
            else:
                self._open_session_by_reference(argument)
        elif cmd in {"/connect", "/conect"}:
            self._open_model_connect()
        elif cmd == "/preview":
            self.action_open_preview()
        elif cmd == "/artifacts":
            self._show_artifacts()
        elif cmd == "/artifact":
            if len(parts) == 1:
                self._append_error("Use /artifact N to inspect one artifact.")
            else:
                self._show_artifact(parts[1])
        elif cmd == "/tool" and len(parts) > 1:
            try:
                sequence = int(parts[1])
            except ValueError:
                self._append_error("Use /tool EVENT_SEQUENCE to inspect a completed tool call.")
            else:
                detail = self._tool_details.get(sequence)
                self._append_tool(f"event {sequence}", detail or "No stored detail for this tool call.")
        elif cmd == "/model":
            if len(parts) == 1 or not parts[1].strip():
                self._open_model_picker()
            else:
                self._selected_model = parts[1].strip()
                self._append_system(f"本次会话将使用模型：{self._selected_model}")
                self._update_input_placeholder()
        elif cmd in {"/reasoning", "/think"}:
            requested = parts[1].strip().lower() if len(parts) > 1 else ""
            aliases = {"默认": "default", "轻量": "low", "标准": "medium", "深度": "high"}
            requested = aliases.get(requested, requested)
            if requested not in {"default", "low", "medium", "high"}:
                self._append_system("推理强度：默认。可选：default、low、medium、high。")
            else:
                self._reasoning_effort = requested
                labels = {"default": "默认", "low": "轻量", "medium": "标准", "high": "深度"}
                self._append_system(f"本次会话推理强度：{labels[requested]}")
                self._update_input_placeholder()
        elif cmd == "/mode":
            requested = parts[1].strip().lower() if len(parts) > 1 else ""
            if requested not in {"dry-run", "real"}:
                self._append_system(f"Current mode: {'real' if self._real_mode else 'dry-run'}. Use /mode dry-run or /mode real.")
            else:
                self._real_mode = requested == "real"
                self._append_system(
                    "Real mode selected; external writes still require explicit approval."
                    if self._real_mode
                    else "Dry-run mode selected."
                )
                self._update_input_placeholder()
        elif cmd == "/exit" or cmd == "/quit":
            self.action_quit()
        elif cmd == "/help":
            self._append_system(
                "你可以直接这样说：\n"
                "  粘贴 arXiv 链接 — 直接解读论文\n"
                "  输入研究主题 — 先选择候选论文\n"
                "  标题更克制一点 — 修改当前文章\n"
                "  重新选题 — 开始新的材料选择\n\n"
                "常用操作：\n"
                "  /preview — 打开手机预览\n"
                "  /details — 查看完整运行详情\n"
                "  /memory — 确认、编辑或删除跨会话写作偏好\n"
                "  /memory off|on — 暂停或启用偏好记忆\n"
                "  /forget ID — 忘记一条偏好；不带 ID 清空本会话上下文\n"
                "  /connect — 配置并验证模型连接\n"
                "  /model — 选择本次会话模型\n"
                "  /reasoning — 设置模型推理强度\n"
                "  /session — 打开、创建或删除会话\n"
                "  /cancel — 取消当前运行\n"
                "  /mode dry-run|real — 选择安全预览或真实草稿\n"
                "  /status — 检查配置\n"
                "  /diagnose — 查看脱敏连接与运行诊断\n"
                "  /offline — 运行离线示例（不调用模型）\n"
                "  /resume — 安全继续中断的对话或任务\n"
                "  /help — 显示此帮助\n\n"
                "快捷键：\n"
                "  Ctrl+N — 新建会话\n"
                "  Ctrl+P — 命令面板\n"
                "  Ctrl+D — 展开或收起运行详情\n"
                "  Ctrl+O — 打开手机尺寸文章预览\n"
                "  Ctrl+Q — 退出"
            )
        else:
            self._append_error(f"未知命令: {cmd}。输入 /help 查看帮助。")

    def _open_model_connect(self) -> None:
        from .config import openai_connection_settings

        self.app.push_screen(ModelConnectModal(openai_connection_settings()), self._model_connect_result)

    def _open_model_picker(self) -> None:
        self.app.push_screen(ModelPickerModal(self._selected_model), self._model_picker_result)

    def _model_picker_result(self, result: dict[str, object] | None) -> None:
        if not result:
            return
        if result.get("connect"):
            self._open_model_connect()
            return
        model = str(result.get("model") or "").strip()
        if not model:
            return
        self._selected_model = model
        self._append_system(f"本次会话将使用模型：{model}")
        self._update_input_placeholder()

    def _open_session_picker(self) -> None:
        self.app.push_screen(SessionPickerModal(list_sessions(), self.session.id), self._session_picker_result)

    def _open_session_by_reference(self, reference: str) -> None:
        sessions = list_sessions()
        match = next((item for item in sessions if item.id == reference or item.title.lower() == reference.lower()), None)
        if match is None:
            self._append_error(f"找不到会话：{reference}。输入 /session 查看会话列表。")
            return
        self._reload_session(match)

    def _session_picker_result(self, result: dict[str, object] | None) -> None:
        if not result:
            return
        if result.get("new"):
            self.action_new_session()
            return
        session_id = str(result.get("open") or "")
        if session_id:
            self._open_session_by_reference(session_id)
            return
        delete_id = str(result.get("delete") or "")
        if delete_id:
            delete_session(delete_id)
            if delete_id == self.session.id:
                self.action_new_session()
            else:
                self._append_system("会话已删除。")
                self._update_sidebar()

    def _model_connect_result(self, result: dict[str, object] | None) -> None:
        if not result:
            return
        self.run_state.model = load_model_info()
        self._selected_model = str(result.get("model") or self.run_state.model.name or "") or None
        self.query_one("#effect-stage", AutoWechatStage).set_setup_required(False)
        self._update_input_placeholder()
        if result.get("tested"):
            self._append_system(f"模型已保存并连接成功：{self._selected_model}")
        else:
            self._append_system(f"模型配置已保存：{self._selected_model}")

    def _show_memory(self) -> None:
        memory = self._conversation_harness.memory
        if not memory.turns and not memory.summary:
            self._append_system("当前没有对话记忆。")
        lines = [f"当前对话记忆：{len(memory.turns)} 条（按上下文预算压缩）"]
        if memory.summary:
            lines.append(f"摘要：{memory.summary}")
        labels = {"user": "你", "assistant": OPENMUSE_LOGO, "tool": "工具"}
        for item in memory.turns[-6:]:
            role = labels.get(str(item.get("role", "")), str(item.get("role", "")))
            content = str(item.get("content", "")).replace("\n", " ").strip()
            if len(content) > 96:
                content = content[:93] + "..."
            lines.append(f"{role}：{content}")
        self._append_system("\n".join(lines))
        self.app.push_screen(PreferenceMemoryModal(self._preference_memory, self.session.id, self._record_memory_action),
                             lambda _: self._refresh_memory_review())

    def _record_memory_action(self, kind: str, metadata: dict[str, object]) -> None:
        event = HarnessEvent.create(kind, session_id=self.session.id, turn_id="memory-management",
                                    sequence=0, source="memory", payload=metadata)
        try:
            self._event_journal.append(event)
        except (ValueError, OSError):
            self._append_error("偏好已更新，但事件记录保存失败；请检查会话日志。")
        self._refresh_memory_review()

    def _refresh_memory_review(self) -> None:
        button = self.query_one("#memory-review", Button)
        try:
            count = sum(row["status"] == "pending" and not row["expired"]
                        for row in self._preference_memory.entries(session_id=self.session.id))
        except (ValueError, OSError) as exc:
            button.display = False
            self._append_error(str(exc))
            return
        button.display = bool(count)
        button.label = f"{count} 条偏好待确认 · 查看"
        if count and self._welcome_visible:
            self._hide_welcome()

    def _show_diagnostics(self) -> None:
        """Show a copyable, secret-free snapshot for self-service debugging."""
        from urllib.parse import urlsplit

        settings = runtime_settings()
        llm = dict(settings.get("llm", {}))
        openai = dict(llm.get("openai", {}))
        endpoint = str(openai.get("base_url", ""))
        parsed = urlsplit(endpoint)
        safe_endpoint = f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.netloc else "未配置"
        provider = str(llm.get("provider", "none"))
        model = self._selected_model or str(openai.get("model", "未配置"))
        memory = self._conversation_harness.memory
        lines = [
            "OpenMuse 诊断信息（已隐藏 API Key）：",
            f"  Provider：{provider}",
            f"  Endpoint：{safe_endpoint}",
            f"  Model：{model}",
            f"  API Key：{'已配置' if openai.get('api_key_configured') else '未配置'}",
            f"  Session：{self.session.id}",
            f"  Memory：{len(memory.turns)} 条 / 上限 {memory.max_turns}",
            f"  Run：{self._active_run_id or '无'}",
            f"  状态：{'处理中' if self._busy else '就绪'}",
        ]
        if "opencode.ai/zen/go" in endpoint:
            lines.append("  OpenCode Go：已自动启用 x-opencode-session")
        lines.append("如需反馈问题，请复制以上信息；不要复制 API Key。")
        self._append_system("\n".join(lines))

    def _show_artifacts(self) -> None:
        if not self._presentation or not self._presentation.artifacts:
            self._append_system("当前运行还没有可用产物。")
            return
        lines = ["当前运行产物："]
        for index, artifact in enumerate(self._presentation.artifacts, 1):
            lines.append(f"  {index}. {artifact.name}  ·  {artifact.producer}")
        lines.append("使用 /artifact N 查看完整路径。")
        self._append_system("\n".join(lines))

    def _show_artifact(self, raw_index: str) -> None:
        try:
            index = int(raw_index)
        except ValueError:
            self._append_error("Artifact index must be a number. Use /artifacts first.")
            return
        artifacts = self._presentation.artifacts if self._presentation else ()
        if index < 1 or index > len(artifacts):
            self._append_error("Artifact index is out of range. Use /artifacts first.")
            return
        artifact = artifacts[index - 1]
        self._append_system(
            f"Artifact {index}: {artifact.name}\n"
            f"  path: {artifact.path}\n"
            f"  type: {artifact.artifact_type}\n"
            f"  producer: {artifact.producer}\n"
            f"  status: {artifact.status}"
        )

    # ── Command Palette ──────────────────────────────────────────────────

    def _handle_palette_input(self, text: str) -> None:
        palette = self.query_one("#command-palette", CommandPalette)
        if text.startswith("/"):
            text = text[1:]
        palette.filter_commands(text)

    @on(Input.Changed, "#palette-input")
    def on_palette_input_changed(self, event: Input.Changed) -> None:
        palette = self.query_one("#command-palette", CommandPalette)
        palette.filter_commands(event.value)

    @on(Input.Submitted, "#palette-input")
    def on_palette_submitted(self, event: Input.Submitted) -> None:
        palette = self.query_one("#command-palette", CommandPalette)
        command = palette.get_selected()
        palette.hide()
        if not command:
            return
        prompts = {
            "connect": "/connect",
            "model": "/model",
            "session": "/session",
            "preview": "/preview",
            "offline": "/offline",
            "resume": "/resume",
            "memory": "/memory",
            "forget": "/forget",
            "collect-arxiv": "收集最近 agents 方向的论文",
            "rank-papers": "收集并排序最近 agents 方向的论文",
            "ingest-paper": "解析当前选择的论文",
            "generate-article": "为当前论文生成深度解读文章",
            "review-article": "审查当前文章质量",
            "improve-article": "改进当前论文文章",
            "create-wechat-draft": "生成当前论文的公众号草稿",
            "collect-github": "生成最近 AI 开源项目日报",
            "daily-digest": "生成 agents 方向日报",
            "trend-analysis": "分析最近 agents 研究趋势",
            "check-status": "/status",
            "agent-run": "收集并解读最近 agents 论文，生成公众号草稿",
        }
        self._handle_user_input(prompts.get(command, command))

    # ── Session List ─────────────────────────────────────────────────────

    @on(ListView.Selected, "#session-list")
    def on_session_selected(self, event: ListView.Selected) -> None:
        session_id = str(event.item.name or "")
        if session_id:
            try:
                session = load_session(session_id)
                self._reload_session(session)
            except FileNotFoundError:
                self._append_error(f"会话不存在: {session_id}")

    def _reload_session(self, session: Session) -> None:
        if self._busy:
            self._append_system("请先停止当前任务，等待安全收尾后再切换会话。")
            return
        self.session = session
        self._event_journal = EventJournal(DATA_DIR / "sessions" / f"{session.id}.events.jsonl")
        self._conversation_harness = ConversationHarness(
            session.id,
            ConversationMemory(DATA_DIR / "sessions" / f"{session.id}.memory.json"),
            event_journal=self._event_journal,
        )
        self._harness_projection.reset()
        self.runtime.attach_event_journal(session.id, self._event_journal)
        self._recovery_notice = ""
        journal_error = ""
        journal_events = []
        try:
            journal_events = self._event_journal.events()
            self._harness_projection.replay(journal_events)
        except (ValueError, OSError) as exc:
            journal_error = str(exc)
        self.run_state = RunState(model=load_model_info())
        self._last_agent_reply = ""
        self._welcome_visible = True
        self._active_run_id = ""
        self._active_agent = "main"
        self._last_runtime_error = ""
        self._details_visible = False
        self._presentation = None
        self._summary_run_id = ""
        self._trace_messages = []
        self._activity_message = None

        log = self.query_one("#trace-area", RichLog)
        log.clear()
        if journal_error:
            self._append_error("会话事件记录无法读取，已保留原文件。请检查记录或新建会话。")

        if session.messages:
            self._hide_welcome()
            for msg in session.messages:
                role = msg.get("role", "")
                content = str(msg.get("content", ""))
                if role == "user":
                    self._append_user(content)
                elif role == "assistant":
                    self._append_agent(content)

        if session.run_ids:
            self._active_run_id = session.run_ids[-1]
            try:
                if journal_events:
                    self._harness_projection.replay(journal_events)
                else:
                    self._harness_projection.replay(self.runtime.events(self._active_run_id))
                for event in self.runtime.events(self._active_run_id):
                    self._render_replayed_event(event, apply_projection=False)
                self._sync_runtime_state()
                has_persisted_summary = any(
                    msg.get("role") == "assistant"
                    and (
                        str(msg.get("content", "")).startswith("OpenMuse：")
                        or str(msg.get("content", "")).startswith("AutoWechat：")
                    )
                    for msg in session.messages
                )
                if has_persisted_summary:
                    self._summary_run_id = self._active_run_id
                else:
                    self._append_run_summary()
            except FileNotFoundError:
                self._active_run_id = ""

        self._update_header()
        self._update_session_bar()
        self._update_context()
        self._update_sidebar()

        # A turn receipt can have committed just before the session transcript
        # was saved. Reconcile that one answer without invoking the model again.
        store = self._conversation_harness.turn_store
        try:
            saved = store.load() if store else {}
        except RuntimeError:
            self._refresh_recovery()
            return
        if saved.get("status") == "completed" and saved.get("reply") and not any(
            m.get("turn_id") == saved.get("turn_id") for m in self.session.messages
        ):
            self.session.messages.append({"role": "assistant", "content": saved["reply"], "turn_id": saved["turn_id"]})
            self._append_agent(str(saved["reply"]))
            save_session(self.session)
        elif saved.get("status") not in {None, "completed", "abandoned"}:
            partial = ""
            for event in journal_events:
                if event.turn_id != saved.get("turn_id"):
                    continue
                if event.type in {"turn.started", "model.started"}:
                    partial = ""
                elif event.type == "model.delta":
                    partial += str(event.payload.get("text", ""))
            if partial:
                self._append_agent("[上次回答未完成，以下仅为已收到的片段；继续后会重新生成]\n\n" + partial)
        self._refresh_recovery()
        self._refresh_memory_review()

    # ── Actions ──────────────────────────────────────────────────────────

    def action_new_session(self) -> None:
        if self._busy:
            self._append_system("请先停止当前任务，等待安全收尾后再创建会话。")
            return
        self._stop_preview_server()
        self.query_one("#resume-turn", Button).display = False
        self._recovery_notice = ""
        self.session = Session.create()
        self._event_journal = EventJournal(DATA_DIR / "sessions" / f"{self.session.id}.events.jsonl")
        self._conversation_harness = ConversationHarness(
            self.session.id,
            ConversationMemory(DATA_DIR / "sessions" / f"{self.session.id}.memory.json"),
            event_journal=self._event_journal,
        )
        self._harness_projection.reset()
        self.runtime.attach_event_journal(self.session.id, self._event_journal)
        self.run_state = RunState(model=load_model_info())
        self._last_agent_reply = ""
        self._welcome_visible = True
        self._active_run_id = ""
        self._last_runtime_error = ""
        self._details_visible = False
        self._presentation = None
        self._summary_run_id = ""
        self._trace_messages = []
        self._activity_message = None

        log = self.query_one("#trace-area", RichLog)
        log.clear()
        self._show_welcome()
        self._update_session_bar()
        self._update_header()
        self._update_context()
        self._update_sidebar()

        self._refresh_memory_review()

    def action_save_session(self) -> None:
        if not self.session.messages:
            self._append_system("没有对话内容可保存。")
            return
        self.session.title = auto_title(self.session.messages)
        save_session(self.session)
        self._update_sidebar()
        self._update_header()
        self._append_system(f"✅ 会话已保存: {self.session.id[:8]}")

    def action_quit(self) -> None:
        if self.session.messages:
            self.session.title = auto_title(self.session.messages)
            save_session(self.session)
        self.app.exit(message=self._exit_message())

    @staticmethod
    def _exit_message() -> str:
        return f"\n{OPENMUSE_LOGO}\n\n本次工作已自动保存。下次见。\n"

    def action_copy_selection(self) -> None:
        """Copy selected text or last agent reply to clipboard."""
        # Try to copy from RichLog selection
        log = self.query_one("#trace-area", RichLog)
        if log.has_focus and hasattr(log, 'selected_text'):
            selected = log.selected_text
            if selected:
                if copy_to_clipboard(selected):
                    self._append_system("📋 已复制选中文本")
                else:
                    self._append_error("复制失败")
                return
        # Fallback: copy last agent reply
        if self._last_agent_reply:
            if copy_to_clipboard(self._last_agent_reply):
                self._append_system("📋 已复制最后回复")
            else:
                self._append_error("复制失败")

    def action_clear_screen(self) -> None:
        log = self.query_one("#trace-area", RichLog)
        log.clear()
        self._show_welcome()

    def action_toggle_palette(self) -> None:
        palette = self.query_one("#command-palette", CommandPalette)
        if palette.visible:
            palette.hide()
        else:
            palette.show()

    def action_toggle_context(self) -> None:
        self._context_visible = not self._context_visible
        self.query_one("#stage-artifacts", Static).display = self._context_visible

    def action_toggle_details(self) -> None:
        self._details_visible = not self._details_visible
        self._render_trace()
        self._append_system("已展开运行详情" if self._details_visible else "已收起运行详情")

    def action_open_preview(self) -> bool:
        if not self._active_run_id:
            self._append_system("当前还没有可预览的文章。先生成一篇公众号文章。")
            return False
        try:
            manifest = self.runtime.get_run(self._active_run_id)
            if manifest.get("workflow") == "publish-existing":
                source_run = manifest.get("request", {}).get("inputs", {}).get("source_run_id")
                if source_run:
                    source_manifest = self.runtime.get_run(str(source_run))
                    if source_manifest.get("session_id") == self.session.id:
                        manifest = source_manifest
        except FileNotFoundError:
            self._append_error("当前运行记录不存在，无法打开预览。")
            return False
        source = select_preview_html(manifest.get("artifacts", []))
        if source is None:
            self._append_system("当前运行尚未生成文章 HTML。完成文章生成后再打开手机预览。")
            return False
        try:
            preview_run_id = str(manifest["run_id"])
            if self._preview_server is None or self._preview_server.run_id != preview_run_id:
                self._stop_preview_server()
                self._preview_server = RuntimePreviewServer(self.runtime, preview_run_id)
            url = self._preview_server.start()
            launched = launch_mobile_preview_target(url)
        except (OSError, RuntimeError) as exc:
            self._append_error(f"无法打开手机预览：{exc}")
            return False
        mode = "独立手机窗口" if launched.mode == "app" else "默认浏览器"
        self._append_system(f"已在{mode}中打开手机预览。文章修改完成后会自动刷新。")
        return True

    def _stop_preview_server(self) -> None:
        if self._preview_server is not None:
            self._preview_server.stop()
            self._preview_server = None

    def on_unmount(self) -> None:
        self._stop_preview_server()

    def action_demo_run(self) -> None:
        """Trigger agent with demo message."""
        self._handle_user_input("帮我看看最近 agents 方向有哪些论文")

    def action_cancel_run(self) -> None:
        if not self._busy:
            return
        self._conversation_harness.cancel()
        if self._active_run_id:
            self.runtime.cancel(self._active_run_id)
        self._append_system("已请求取消当前任务。")

    def action_dismiss_escape(self) -> None:
        """Close transient UI first; otherwise preserve Escape-to-cancel."""
        popup = self.query_one("#slash-command-popup", SlashCommandPopup)
        if popup.display:
            popup.display = False
            input_widget = self.query_one("#chat-input", Input)
            input_widget.value = ""
            input_widget.focus()
            return
        self.action_cancel_run()

    def action_next_agent(self) -> None:
        index = (self._agents.index(self._active_agent) + 1) % len(self._agents)
        self._active_agent = self._agents[index]
        self._details_visible = True
        self._replay_agent_view()

    def action_previous_agent(self) -> None:
        index = (self._agents.index(self._active_agent) - 1) % len(self._agents)
        self._active_agent = self._agents[index]
        self._details_visible = True
        self._replay_agent_view()

    def _replay_agent_view(self) -> None:
        self._trace_messages = [message for message in self._trace_messages if not message.detail_only]
        self._append_system(f"Agent view: {self._active_agent}")
        self._update_input_placeholder()
        if self._active_run_id:
            self._harness_projection.replay(self._event_journal.events())
            for event in self.runtime.events(self._active_run_id):
                if self._active_agent == "main" or event.agent_id == self._active_agent:
                    self._render_replayed_event(event, apply_projection=False)
            self._sync_runtime_state()

    def _update_all(self) -> None:
        """Update all panels from RunState."""
        self._update_header()
        self._update_context()
        self._update_sidebar()
        self._update_run_block()
        self._update_input_placeholder()

    def _update_run_block(self) -> None:
        return

    def _update_input_placeholder(self) -> None:
        input_widget = self.query_one("#chat-input", Input)
        self.query_one("#input-bar", InputBar).set_run_mode(self._busy)
        if self._presentation and self._presentation.status == "waiting_input":
            input_widget.placeholder = "选择第 1、2 或 3 篇论文，方向键和 Enter 也可以…"
        elif self.run_state.status == RunStatus.RUNNING:
            input_widget.placeholder = "正在处理；输入要求可继续调整，按 Esc 取消..."
        else:
            input_widget.placeholder = "输入消息，按 Enter 发送..."
        model = self._selected_model or (self.run_state.model.name if self.run_state.model else "local")
        self.query_one("#input-bar", InputBar).set_meta(
            model or "local",
            "real" if self._real_mode else "dry-run",
            self._reasoning_effort,
        )
        self._update_session_bar()

    def _set_composer_running(self, running: bool) -> None:
        self.query_one("#input-bar", InputBar).set_run_mode(running)
        self._update_session_bar()

    def _update_sidebar(self) -> None:
        """Legacy hook retained for session persistence compatibility."""
        return

    # ── Background Tasks ─────────────────────────────────────────────────

    @work(exclusive=True, thread=True)
    def _run_check_status(self) -> None:
        self._busy = True
        try:
            result = execute_tool("check_status", {})
            data = json.loads(result)
            self.app.call_from_thread(
                self._append_agent,
                f"```\n{json.dumps(data, indent=2, ensure_ascii=False)}\n```"
            )
        except Exception as exc:
            self.app.call_from_thread(self._append_error, str(exc))
        finally:
            self._busy = False


    def _make_controller(self) -> ConversationController:
        return ConversationController(
            self.runtime, self._conversation_harness,
            active_run_id=self._active_run_id, model=self._selected_model,
            on_run_changed=lambda run_id: self.app.call_from_thread(self._adopt_controller_run, run_id),
            on_runtime_event=self._runtime_event_from_worker,
            preview=lambda: self.app.call_from_thread(self.action_open_preview),
            preference_memory=self._preference_memory,
        )

    def _adopt_controller_run(self, run_id: str) -> None:
        if run_id != self._active_run_id:
            self._stop_preview_server()
        self._active_run_id = run_id
        self.session.run_ids = [item for item in self.session.run_ids if item != run_id] + [run_id]
        self._summary_run_id = ""
        self._presentation = None
        self._pending_approval = ""
        self._show_run_block()
        save_session(self.session)

    def _finish_controller_turn(self) -> None:
        """Finish UI cleanup atomically before accepting another submission."""
        self._controller_running = False
        self._run_started_monotonic = 0.0
        try:
            self._sync_runtime_state()
        finally:
            self._set_composer_running(False)
            self._busy = False
            self._update_input_placeholder()
        self._present_pending_approval()
        self._refresh_recovery()
        self._refresh_memory_review()

    def _refresh_recovery(self) -> None:
        button = self.query_one("#resume-turn", Button)
        try:
            info = self._make_controller().recovery_info()
        except (ValueError, RuntimeError, OSError) as exc:
            button.display = True
            button.disabled = True
            self._append_error(str(exc))
            return
        button.display = bool(info["available"])
        button.disabled = info.get("status") == "recovery_required"
        if not info["available"]:
            self._recovery_notice = ""
        if info["available"] and str(info["message"]) != self._recovery_notice:
            self._recovery_notice = str(info["message"])
            self._append_system(self._recovery_notice)
            event = HarnessEvent.create("session.interrupted", session_id=self.session.id,
                turn_id="recovery", sequence=0, source="session", payload={"status": info.get("status")})
            try:
                self._event_journal.append(event)
            except (ValueError, OSError):
                button.disabled = True
                self._append_error("无法保存恢复状态，已保留原事件记录；请检查记录或新建会话。")
            self._apply_harness_projection(event)

    def action_resume_turn(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.query_one("#resume-turn", Button).display = False
        self._run_chat_turn("", resume=True)

    @work(exclusive=True, thread=True)
    def _run_chat_turn(self, user_message: str, *, resume: bool = False) -> None:
        self._busy = True
        self._controller_running = True
        self.app.call_from_thread(self._set_composer_running, True)
        self._run_started_monotonic = time.monotonic()
        self._streaming_message = None
        self.app.call_from_thread(self._begin_streaming_agent)
        try:
            controller = self._make_controller()
            store = self._conversation_harness.turn_store
            saved = store.load() if store else {}
            if resume and saved.get("status") in {None, "completed", "abandoned"}:
                controller.resume_runtime()
                self.app.call_from_thread(self._sync_runtime_state)
                if self._presentation:
                    self.app.call_from_thread(self._append_agent, self._presentation.summary_text)
                return
            reply = controller.run(
                user_message,
                on_event=lambda event: self.app.call_from_thread(self._handle_conversation_event, event),
                reasoning_effort=self._reasoning_effort,
                resume=resume,
            )
            if reply and self._harness_projection.state.terminal_reason == "completed":
                self.session.messages.append({"role": "assistant", "content": reply, "turn_id": self._conversation_harness.last_turn_id})
                self.session.title = auto_title(self.session.messages)
                save_session(self.session)
        except Exception as exc:
            self.app.call_from_thread(self._append_error, friendly_error(str(exc)))
            self.app.call_from_thread(self._append_error, str(exc), detail_only=True)
        finally:
            self.app.call_from_thread(self._finish_controller_turn)

    def _handle_conversation_event(self, event: TurnEvent) -> None:
        self._apply_harness_projection(event)
        if event.type == "model.delta":
            self._append_stream_delta(str(event.payload.get("text", "")))
        elif event.type == "model.first_token":
            self._append_system(f"首 token 延迟 {event.payload.get('latency_ms', 0)} ms", detail_only=True)
        elif event.type in {"model.stalled", "tool.stalled"}:
            self._append_system("当前操作响应较慢；可以继续等待或按 Esc 取消。", detail_only=True)
        elif event.type == "model.completed":
            self._append_system(f"模型输出完成 · usage={event.payload.get('usage', {})}", detail_only=True)
        elif event.type == "memory.retrieved":
            self._append_system(f"本轮参考 {event.payload.get('count', 0)} 条已确认偏好。", detail_only=True)
        elif event.type == "memory.proposed":
            self._append_system("已提出一条偏好，需你在记忆面板确认后才会跨会话使用。")
        elif event.type in {"tool.started", "tool.completed", "tool.failed", "tool.blocked"}:
            self._append_system(f"{event.payload.get('name', '工具')} · {event.type}", detail_only=True)
        elif event.type == "provider.retrying":
            self._append_system(self._harness_projection.state.activity, detail_only=True)
        elif event.type == "provider.capability":
            self._append_system("当前模型接口不支持推理强度设置，使用服务商默认值。")
        elif event.type == "context.compacted":
            self._append_system("较长历史或工具输出已压缩；当前任务和本次要求保留。", detail_only=True)
        elif event.type == "approval.required":
            self._append_system(f"工具 {event.payload.get('name', '操作')} 需要你确认；请在结果页确认外部发布。")


    @work(exclusive=True, thread=True)
    def _run_controller_action(self, name: str, arguments: dict[str, object], message: str) -> None:
        self._busy = True
        self._controller_running = True
        self.app.call_from_thread(self._set_composer_running, True)
        self._conversation_harness.memory.append("user", message)
        try:
            context = self._make_controller().perform(name, arguments)
            self.app.call_from_thread(self._sync_runtime_state)
            summary = self._presentation.summary_text if self._presentation else str(context.get("summary", ""))
            self._conversation_harness.memory.append("assistant", summary)
            self.session.messages.append({"role": "assistant", "content": summary})
            self.app.call_from_thread(self._append_agent, summary)
            save_session(self.session)
        except Exception as exc:
            self.app.call_from_thread(self._append_error, friendly_error(str(exc)))
        finally:
            self.app.call_from_thread(self._finish_controller_turn)

    def _resume_candidate(self, paper_id: str) -> None:
        self._busy = True
        self._run_controller_action("select_candidate", {"paper_id": paper_id}, f"选择论文 {paper_id}")

    @work(exclusive=True, thread=True)
    def _run_offline_example(self, message: str) -> None:
        self._busy = True
        self.app.call_from_thread(self._append_system, "离线示例模式：不使用模型理解意图，不代表真实模型生成质量。")
        try:
            workflow, inputs = offline_request(message)
            created = self.runtime.create_run(
                RunRequest(workflow, inputs, dry_run=True), session_id=self.session.id,
            )
            self.app.call_from_thread(self._adopt_controller_run, created.run_id)
            unsubscribe = self.runtime.subscribe(created.run_id, self._runtime_event_from_worker)
            try:
                self.runtime.execute(created.run_id)
            finally:
                unsubscribe()
            self.app.call_from_thread(self._sync_runtime_state)
            save_session(self.session)
        except Exception as exc:
            self.app.call_from_thread(self._append_error, friendly_error(str(exc)))
        finally:
            self.app.call_from_thread(self._finish_controller_turn)


    def _runtime_event_from_worker(self, event: RunEvent) -> None:
        self.app.call_from_thread(self._handle_runtime_event, normalize_event(event))

    def _render_replayed_event(self, event: RunEvent, *, apply_projection: bool = True) -> None:
        if apply_projection:
            self._apply_harness_projection(event)
        self._render_runtime_details(event)

    def _render_runtime_details(self, event: RunEvent) -> None:
        """One detail renderer for live and replay; activity belongs to Projection."""
        payload = event.payload
        if event.type in {"step.started", "step.completed"}:
            self._append_step(event.agent_id, f"{event.type}: {payload.get('step')}")
        elif event.type == "tool.started":
            self._append_tool(str(payload.get("tool", "tool")), "Started", detail_only=True)
        elif event.type == "tool.completed":
            self._append_collapsed_tool(event)
        elif event.type == "artifact.created":
            self._append_artifact(str(payload.get("path", "")), str(payload.get("producer", "")))
        elif event.type in {"tool.failed", "step.failed", "run.failed"}:
            error = str(payload.get("error", "任务未完成"))
            if event.type == "run.failed":
                self._append_error(friendly_error(error))
            self._append_error(error, detail_only=True)
        elif event.type == "agent.started":
            self._append_agent(f"{event.agent_id} started", detail_only=True)
        elif event.type == "approval.required":
            self._pending_approval = str(payload.get("id", ""))
        elif event.type == "interaction.required":
            self._append_agent("候选论文已经准备好，请选择一篇继续。")

    def _handle_runtime_event(self, event: RunEvent) -> None:
        self._apply_harness_projection(event)
        if self._active_agent == "main" or event.agent_id == self._active_agent:
            self._render_runtime_details(event)
        if event.type == "approval.required":
            self._pending_approval = str(event.payload.get("id", ""))
            if not self._controller_running:
                self.app.push_screen(ApprovalModal(event.payload), self._approval_decision)
        self._sync_runtime_state()
        if event.type in {"run.completed", "run.failed", "run.cancelled"} and not self._controller_running:
            self._append_run_summary()

    def _apply_harness_projection(self, event: object) -> None:
        """Reduce both live event types through the same UI projection."""
        normalized = normalize_event(event)
        if normalized.source == "runtime":
            self._event_journal.append(normalized)
        state = self._harness_projection.apply(normalized)
        if state.activity:
            self._set_activity(state.activity)
        if state.status == "failed" and state.last_error:
            self._last_runtime_error = state.last_error
        elif state.status in {"running", "completed", "cancelled"}:
            self._last_runtime_error = ""
        self._update_session_bar()


    def _append_collapsed_tool(self, event: RunEvent) -> None:
        payload = event.payload
        detail = str(payload.get("output_preview", ""))
        if detail:
            self._tool_details[event.sequence] = detail
        suffix = f"  [collapsed: /tool {event.sequence}]" if detail else ""
        duration = payload.get("duration_seconds")
        timing = f"  ·  {duration}s" if duration is not None else ""
        self._append_tool(
            str(payload.get("tool", "tool")),
            f"{payload.get('message', 'Completed')}{timing}{suffix}",
            detail_only=True,
        )

    def _present_pending_approval(self) -> None:
        # Do not start an exclusive approval worker while a model turn is
        # still summarizing the request: that would cancel its live worker.
        if not self._pending_approval or not self._active_run_id:
            return
        manifest = self.runtime.get_run(self._active_run_id)
        approval = manifest.get("approvals", {}).get(self._pending_approval, {})
        if manifest["status"] == "waiting_approval" and approval.get("status") == "pending":
            self.app.push_screen(ApprovalModal(approval), self._approval_decision)

    def _approval_decision(self, approved: bool | None) -> None:
        if self._pending_approval:
            self._busy = True
            self._resolve_approval(self._pending_approval, bool(approved))


    @work(exclusive=True, thread=True)
    def _resolve_approval(self, approval_id: str, approved: bool) -> None:
        self._busy = True
        self._controller_running = True
        self.app.call_from_thread(self._set_composer_running, True)
        try:
            self._make_controller().resolve_approval(approval_id, approved)
            self._pending_approval = ""
            self.app.call_from_thread(self._sync_runtime_state)
            summary = self._presentation.summary_text if self._presentation else "已处理审批。"
            self._conversation_harness.memory.append("user", "确认创建微信草稿" if approved else "拒绝创建微信草稿")
            self._conversation_harness.memory.append("assistant", summary)
            self.session.messages.append({"role": "assistant", "content": summary})
            self.app.call_from_thread(self._append_agent, summary)
            save_session(self.session)
        except Exception as exc:
            self.app.call_from_thread(self._append_error, friendly_error(str(exc)))
        finally:
            self.app.call_from_thread(self._finish_controller_turn)

    def _sync_runtime_state(self) -> None:
        if not self._active_run_id:
            return
        manifest = self.runtime.get_run(self._active_run_id)
        checkpoints = {
            str(item.get("id")): self.runtime.store.load_checkpoint(
                self._active_run_id, str(item.get("id"))
            )
            for item in manifest.get("steps", [])
        }
        self._presentation = build_run_presentation(manifest, checkpoints)
        if manifest.get("status") == "completed":
            self._last_runtime_error = ""
        status_map = {
            "pending": RunStatus.IDLE,
            "running": RunStatus.RUNNING,
            "waiting_approval": RunStatus.RUNNING,
            "waiting_input": RunStatus.RUNNING,
            "cancelling": RunStatus.RUNNING,
            "cancelled": RunStatus.FAILED,
            "recovery_required": RunStatus.FAILED,
            "failed": RunStatus.FAILED,
            "completed": RunStatus.SUCCESS,
        }
        step_status_map = {
            "pending": StepStatus.WAITING,
            "running": StepStatus.RUNNING,
            "retrying": StepStatus.RUNNING,
            "waiting_approval": StepStatus.RUNNING,
            "waiting_input": StepStatus.RUNNING,
            "skipped": StepStatus.SKIPPED,
            "failed": StepStatus.FAILED,
            "completed": StepStatus.SUCCESS,
        }
        self.run_state.id = self._active_run_id
        self.run_state.title = str(manifest.get("workflow", ""))
        self.run_state.mode = RunMode.RUN if manifest["status"] in {"running", "waiting_input", "waiting_approval", "cancelling"} else RunMode.IDLE
        self.run_state.status = status_map[str(manifest["status"])]
        self.run_state.steps = [
            WorkflowStep(str(item["id"]), str(item["tool"]), step_status_map[str(item["status"])])
            for item in manifest.get("steps", [])
        ]
        active = next((item for item in manifest.get("steps", []) if item["status"] in {"running", "retrying", "waiting_approval"}), None)
        self.run_state.current_step = str(active["id"]) if active else ""
        self.run_state.current_task = str(active["tool"]) if active else ""
        self.run_state.artifacts = [
            ArtifactInfo(Path(str(item["path"])).name, str(item["path"]), "file", status=str(item.get("status", "created")))
            for item in manifest.get("artifacts", [])
            if item.get("status") != "stale"
        ]
        usage = manifest.get("usage", {})
        self.run_state.stats.tokens = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
        self.run_state.stats.cost = float(usage["cost"]) if usage.get("cost") is not None else None
        self.run_state.quality = dict(manifest.get("quality", {}))
        settings = runtime_settings()
        llm = dict(settings.get("llm", {}))
        self.run_state.providers = {
            "LLM": "configured" if llm.get("provider") != "none" else "not configured",
            "WeChat": "connected" if all(dict(settings.get("wechat", {})).values()) else "dry-run",
            "MCP": "available" if importlib.util.find_spec("mcp") else "not installed",
        }
        self.run_state.workspace = str(self.runtime.workspace)
        self.run_state.active_agent = self._active_agent
        self._update_all()


# ── App ─────────────────────────────────────────────────────────────────

class SmearglePaperApp(App):
    """OpenMuse Harness application (SmearglePaper compatibility module)."""

    TITLE = OPENMUSE_LOGO
    CSS = APP_CSS
    # Disable mouse tracking and Kitty keyboard protocol
    MOUSE_TRACKING = False

    def __init__(self) -> None:
        super().__init__()

    def on_mount(self) -> None:
        self.push_screen(AgentConsole())


def run_tui() -> None:
    import os
    # Disable Kitty keyboard protocol BEFORE importing Textual
    os.environ['TEXTUAL_DISABLE_KITTY_KEY'] = '1'
    os.environ['KITTY_KEYBOARD_PROTOCOL'] = '0'
    if 'TERM' not in os.environ:
        os.environ['TERM'] = 'xterm-256color'

    app = SmearglePaperApp()
    app.run()


if __name__ == "__main__":
    run_tui()
