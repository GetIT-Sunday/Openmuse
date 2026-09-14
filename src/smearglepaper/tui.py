"""AutoWechat Agent Console — effect stage above a conversational composer."""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Input,
    Label,
    ListView,
    RichLog,
    Static,
)

from .config import DATA_DIR, runtime_settings
from .harness import ConversationHarness, ConversationMemory, TurnEvent
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
from .tools import SYSTEM_PROMPT, execute_tool, installed_capabilities
from .trace_message import TraceMessage
from .tui_components import (
    AUTOWECHAT_LOGO,
    AutoWechatStage,
    CommandPalette,
    InputBar,
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


def _select_article_markdown(artifacts: object) -> Path | None:
    if not isinstance(artifacts, list):
        return None
    candidates: list[Path] = []
    for item in artifacts:
        if not isinstance(item, dict) or item.get("status") == "stale" or item.get("producer") != "write":
            continue
        path = Path(str(item.get("path", "")))
        if path.suffix.lower() == ".md" and path.is_file() and "publish_package" not in path.name:
            candidates.append(path)
    return min(candidates, key=lambda path: (0 if "article" in path.name.lower() else 1, path.name)) if candidates else None


def _select_article_json(artifacts: object) -> Path | None:
    if not isinstance(artifacts, list):
        return None
    candidates: list[Path] = []
    for item in artifacts:
        if not isinstance(item, dict) or item.get("status") == "stale" or item.get("producer") != "write":
            continue
        path = Path(str(item.get("path", "")))
        if path.suffix.lower() == ".json" and path.is_file() and "review" not in path.name:
            candidates.append(path)
    return min(candidates, key=lambda path: (0 if "article" in path.name.lower() else 1, path.name)) if candidates else None


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

    @on(Button.Pressed)
    def on_button(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "approval-approve")


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
        Binding("escape", "cancel_run", "Cancel Run", priority=True),
        Binding("alt+left", "previous_agent", "Previous Agent"),
        Binding("alt+right", "next_agent", "Next Agent"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.session = Session.create()
        self.run_state = RunState(model=load_model_info())
        self._busy = False
        self._last_agent_reply = ""
        self._context_visible = True
        self._welcome_visible = True
        self._trace_messages: list[TraceMessage] = []
        self.runtime = AgentRuntime()
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
        self._real_mode = False
        self._preview_server: RuntimePreviewServer | None = None
        self._run_started_monotonic = 0.0
        self._streaming_message: TraceMessage | None = None
        self._activity_message: TraceMessage | None = None
        self._stream_render_pending = False
        self._conversation_harness = ConversationHarness(
            self.session.id,
            ConversationMemory(DATA_DIR / "sessions" / f"{self.session.id}.memory.json"),
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="main-container"):
            yield AutoWechatStage(id="effect-stage")
            with Vertical(id="conversation-area"):
                yield Static("对话", id="conversation-label")
                yield RichLog(id="trace-area", markup=True, highlight=True, wrap=True)
                yield InputBar(id="input-bar")

        yield Static(self._footer_text(), id="footer-bar")
        yield CommandPalette(id="command-palette")

    def _footer_text(self) -> str:
        return (
            "[#eab308]^N[/] 新对话   "
            "[#eab308]^P[/] 命令   "
            "[#eab308]^S[/] 保存   "
            "[#eab308]^D[/] 详情   "
            "[#eab308]^O[/] 手机预览   "
            "[#eab308]Esc[/] 取消   "
            "[#eab308]^Q[/] 退出"
        )

    def on_mount(self) -> None:
        stage = self.query_one("#effect-stage", AutoWechatStage)
        stage.update_idle()
        if dict(runtime_settings().get("llm", {})).get("provider") == "none":
            stage.query_one("#stage-current", Static).update("尚未配置模型。请先设置 API Key 和模型地址，再开始生成文章。")
        self.query_one("#quick-continue", Button).disabled = not any(session.run_ids for session in list_sessions())
        self.query_one("#trace-area", RichLog).display = False
        self.query_one("#chat-input", Input).focus()
        self._welcome_visible = True
        self._update_input_placeholder()
        self.set_interval(1.0, self._refresh_liveness)

    def _refresh_liveness(self) -> None:
        """Keep the composer honest during long network/model operations."""
        if not self._busy or not self._run_started_monotonic:
            return
        elapsed = int(time.monotonic() - self._run_started_monotonic)
        current = self._presentation.phase if self._presentation else "准备工作"
        self.query_one("#input-bar", InputBar).set_meta(
            self._active_agent,
            self._selected_model or (self.run_state.model.name if self.run_state.model else "local"),
            f"处理中 · {elapsed}s · {current}",
        )

    def on_resize(self, event: events.Resize) -> None:
        self.query_one("#effect-stage", AutoWechatStage).set_compact(event.size.height < 30)

    # ── Welcome / Empty State ────────────────────────────────────────────

    def _show_welcome(self) -> None:
        self.query_one("#effect-stage", AutoWechatStage).update_idle()
        self.query_one("#trace-area", RichLog).display = False
        self.query_one("#conversation-area").remove_class("has-messages")
        self._details_visible = False
        self._welcome_visible = True

    def _hide_welcome(self) -> None:
        if self._welcome_visible:
            self.query_one("#trace-area", RichLog).display = True
            self.query_one("#conversation-area").add_class("has-messages")
            self._welcome_visible = False

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
        log.clear()
        for message in self._trace_messages:
            if self._details_visible or not message.detail_only:
                log.write(message.to_rich_text())

    def _schedule_trace_render(self) -> None:
        """Batch token updates so long streamed replies do not redraw per token."""
        if self._stream_render_pending:
            return
        self._stream_render_pending = True

        def flush() -> None:
            self._stream_render_pending = False
            self._render_trace()

        self.set_timer(0.05, flush)

    def _record_trace(self, message: TraceMessage) -> None:
        self._trace_messages.append(message)
        self._render_trace()

    def _append_system(self, text: str, *, detail_only: bool = False) -> None:
        self._record_trace(TraceMessage.create("system", text, detail_only=detail_only))

    def _append_user(self, text: str) -> None:
        self._record_trace(TraceMessage.create("user", text))

    def _append_agent(self, text: str, *, detail_only: bool = False) -> None:
        self._last_agent_reply = text
        display_text = text.removeprefix("AutoWechat：")
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
        if self._presentation:
            stage.update_presentation(self._presentation, self.run_state)
            draft = self.query_one("#action-draft", Button)
            wechat = dict(runtime_settings().get("wechat", {}))
            if draft.display and not all(wechat.values()):
                draft.disabled = True
                draft.tooltip = "配置微信公众号后可创建真实草稿"
        else:
            stage.update_state(self.run_state, error=self._last_runtime_error)

    def _update_context(self) -> None:
        return

    # ── Input Handling ───────────────────────────────────────────────────

    @on(Input.Submitted, "#chat-input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        # Filter out terminal escape sequences (Kitty keyboard protocol etc.)
        import re
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

    @on(Button.Pressed)
    def on_stage_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "composer-stop":
            self.action_cancel_run()
            return
        input_widget = self.query_one("#chat-input", Input)
        if button_id == "quick-paper":
            input_widget.placeholder = "粘贴 arXiv 论文链接或输入论文 ID…"
            input_widget.focus()
        elif button_id == "quick-topic":
            input_widget.placeholder = "输入研究主题，例如：多智能体协作…"
            input_widget.focus()
        elif button_id == "quick-continue":
            previous = next((session for session in list_sessions() if session.run_ids), None)
            if previous:
                self._reload_session(previous)
            else:
                self._append_system("还没有可以继续的历史任务。")
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
            self._apply_followup(text)
            return

        if text.startswith("/"):
            self._handle_slash_command(text)
            return

        if self._presentation and self._presentation.status == "waiting_input":
            self._handle_candidate_input(text)
            return

        if self._is_revision_context(text):
            self._revise_current_article(text)
            return

        self._hide_welcome()
        self._append_user(text)
        self.session.messages.append({"role": "user", "content": text})
        if self._is_conversation_turn(text):
            self._run_chat_turn(text)
        else:
            self._run_agent(text)

    def _is_conversation_turn(self, text: str) -> bool:
        lower = text.lower()
        if re.search(r"https?://|(?<!\d)\d{4}\.\d{4,5}(?:v\d+)?(?!\d)", text):
            return False
        workflow_words = (
            "论文", "选题", "文章", "解读", "日报", "周报", "公众号", "微信", "草稿",
            "收集", "搜索", "解析", "预览", "发布", "写一篇", "改写", "重新选题",
        )
        return not any(word in lower for word in workflow_words)

    def _is_revision_context(self, text: str) -> bool:
        if not self._presentation or self._presentation.status != "completed":
            return False
        if self._presentation.workflow not in {"paper-to-article", "paper-to-wechat"}:
            return False
        lower = text.lower()
        if re.search(r"https?://", text) or re.search(r"(?<!\d)\d{4}\.\d{4,5}(?:v\d+)?(?!\d)", text):
            return False
        return not any(word in lower for word in ("新任务", "重新选题", "换一篇论文", "日报", "周报"))

    def _revise_current_article(self, instruction: str) -> None:
        if not self._active_run_id:
            return
        manifest = self.runtime.get_run(self._active_run_id)
        markdown = _select_article_markdown(manifest.get("artifacts", []))
        if markdown is None:
            self._append_system("没有找到当前文章正文，无法开始修订。")
            return
        request = dict(manifest.get("request", {}))
        inputs = dict(request.get("inputs", {}))
        revision_number = int(inputs.get("revision_number", 1)) + 1
        self._hide_welcome()
        self._append_user(instruction)
        self.session.messages.append({"role": "user", "content": instruction})
        self._summary_run_id = ""
        self.runtime.update_inputs(
            self._active_run_id,
            {
                "input_article": str(markdown),
                "revision_instruction": instruction,
                "revision_number": revision_number,
            },
            restart_step="write",
        )
        self._run_revision(revision_number, instruction)

    def _handle_candidate_input(self, text: str) -> None:
        interaction = self._presentation.interaction if self._presentation else None
        options = list(interaction.get("options", [])) if interaction else []
        normalized = text.strip().lower()
        if not options or "扩大" in text or "修改主题" in text or "更换主题" in text:
            self._restart_discovery(text)
            return
        number_words = {"一": 1, "二": 2, "三": 3}
        match = re.search(r"(?:第|选|选择)?\s*([123一二三])", normalized)
        token = match.group(1) if match else ""
        index = int(token) if token.isdigit() else number_words.get(token, 0)
        selected: dict[str, object] | None = None
        if 1 <= index <= len(options) and isinstance(options[index - 1], dict):
            selected = options[index - 1]
        if selected is None:
            selected = next(
                (
                    option
                    for option in options
                    if isinstance(option, dict)
                    and (normalized == str(option.get("paper_id", "")).lower() or normalized in str(option.get("title", "")).lower())
                ),
                None,
            )
        if selected is None:
            self._append_system("请选择候选列表中的第 1、2 或 3 篇论文。")
            return
        self._choose_candidate(str(selected.get("paper_id", "")), str(selected.get("title", "")))

    def _choose_candidate(self, paper_id: str, title: str) -> None:
        if not self._active_run_id or not paper_id:
            return
        self._hide_welcome()
        self._append_user(f"选择：{title or paper_id}")
        self.session.messages.append({"role": "user", "content": f"选择：{title or paper_id}"})
        self._resume_candidate(paper_id)

    def _restart_discovery(self, text: str) -> None:
        if not self._active_run_id:
            return
        updates: dict[str, object]
        if "扩大" in text or "90" in text:
            updates = {"days": 90}
            message = "已将搜索范围扩大到 90 天。"
        else:
            topic = re.sub(r"^(修改|更换)?(?:研究)?主题[：:]?", "", text).strip() or text.strip()
            updates = {"query": topic, "request": topic}
            message = f"正在按新主题重新寻找：{topic}"
        self._hide_welcome()
        self._append_user(text)
        self.session.messages.append({"role": "user", "content": text})
        self.runtime.update_inputs(self._active_run_id, updates, restart_step="collect")
        self._rerun_discovery(message)

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
        if not self._active_run_id or not self._presentation:
            return
        manifest = self.runtime.get_run(self._active_run_id)
        article_json = _select_article_json(manifest.get("artifacts", []))
        if article_json is None:
            self._append_system("没有找到已审阅文章，无法创建草稿。")
            return
        if not self._presentation.quality.get("publish_ready"):
            self._append_system("文章尚未通过发布质量检查，请先根据建议继续修改。")
            return
        self._start_draft_creation(str(article_json), dict(self._presentation.quality))

    def _apply_followup(self, text: str) -> None:
        if not self._active_run_id:
            self._append_system("No active run is available for this update.")
            return
        updates: dict[str, object] = {}
        if any(word in text for word in ("严谨", "学术")):
            updates["style_mode"] = "rigorous"
        elif any(word in text for word in ("通俗", "大众")):
            updates["style_mode"] = "popular"
        elif "面试" in text:
            updates["style_mode"] = "interview"
        audience = re.search(r"面向(.+?)(?:的|读者|来写|$)", text)
        if audience:
            updates["target_audience"] = audience.group(1).strip()
        score = re.search(r"(?:评分|分数|门槛).*?(\d{2,3})", text)
        if score:
            updates["target_score"] = int(score.group(1))
        if not updates:
            self._append_system("The run is active. Use Esc to cancel, or specify a new audience, style, or quality score.")
            return
        self.runtime.update_inputs(self._active_run_id, updates, restart_step="write")
        self._append_system(f"Updated downstream writing inputs: {json.dumps(updates, ensure_ascii=False)}")

    def _handle_slash_command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()

        if cmd == "/clear":
            self.action_clear_screen()
        elif cmd == "/status":
            self._run_check_status()
        elif cmd == "/delete":
            delete_session(self.session.id)
            self._append_system(f"会话 {self.session.id[:8]} 已删除")
            self.action_new_session()
        elif cmd == "/demo-run":
            self.action_demo_run()
        elif cmd == "/cancel":
            self.action_cancel_run()
        elif cmd == "/approve" and self._pending_approval:
            self._resolve_approval(self._pending_approval, True)
        elif cmd == "/details":
            self.action_toggle_details()
        elif cmd == "/memory":
            self._show_memory()
        elif cmd == "/forget":
            self._conversation_harness.memory.clear()
            self._append_system("已清空对话记忆。当前文章、运行记录和已保存产物不会受影响。")
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
                self._append_system(f"Current model: {self._selected_model or 'provider default'}")
            else:
                self._selected_model = parts[1].strip()
                self._append_system(f"Model selected: {self._selected_model}")
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
                "  /memory — 查看当前对话记忆\n"
                "  /forget — 清空对话记忆（不删除文章）\n"
                "  /cancel — 取消当前运行\n"
                "  /mode dry-run|real — 选择安全预览或真实草稿\n"
                "  /status — 检查配置\n"
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

    def _show_memory(self) -> None:
        memory = self._conversation_harness.memory
        if not memory.turns and not memory.summary:
            self._append_system("当前没有对话记忆。")
            return
        lines = [f"当前对话记忆：{len(memory.turns)} 条（最多保留 {memory.max_turns} 条）"]
        if memory.summary:
            lines.append(f"摘要：{memory.summary}")
        labels = {"user": "你", "assistant": "AutoWechat", "tool": "工具"}
        for item in memory.turns[-6:]:
            role = labels.get(str(item.get("role", "")), str(item.get("role", "")))
            content = str(item.get("content", "")).replace("\n", " ").strip()
            if len(content) > 96:
                content = content[:93] + "..."
            lines.append(f"{role}：{content}")
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
            "preview": "/preview",
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
        self.session = session
        self._conversation_harness = ConversationHarness(
            session.id,
            ConversationMemory(DATA_DIR / "sessions" / f"{session.id}.memory.json"),
        )
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
                for event in self.runtime.events(self._active_run_id):
                    self._render_replayed_event(event)
                self._sync_runtime_state()
                has_persisted_summary = any(
                    msg.get("role") == "assistant"
                    and str(msg.get("content", "")).startswith("AutoWechat：")
                    for msg in session.messages
                )
                if has_persisted_summary:
                    self._summary_run_id = self._active_run_id
                else:
                    self._append_run_summary()
            except FileNotFoundError:
                self._active_run_id = ""

        self._update_header()
        self._update_context()
        self._update_sidebar()

    # ── Actions ──────────────────────────────────────────────────────────

    def action_new_session(self) -> None:
        self._stop_preview_server()
        self.session = Session.create()
        self._conversation_harness = ConversationHarness(
            self.session.id,
            ConversationMemory(DATA_DIR / "sessions" / f"{self.session.id}.memory.json"),
        )
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
        self._update_header()
        self._update_context()
        self._update_sidebar()

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
        return f"\n{AUTOWECHAT_LOGO}\n\n本次工作已自动保存。下次见。\n"

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

    def action_open_preview(self) -> None:
        if not self._active_run_id:
            self._append_system("当前还没有可预览的文章。先生成一篇公众号文章。")
            return
        try:
            manifest = self.runtime.get_run(self._active_run_id)
        except FileNotFoundError:
            self._append_error("当前运行记录不存在，无法打开预览。")
            return
        source = select_preview_html(manifest.get("artifacts", []))
        if source is None:
            self._append_system("当前运行尚未生成文章 HTML。完成文章生成后再打开手机预览。")
            return
        try:
            if self._preview_server is None or self._preview_server.run_id != self._active_run_id:
                self._stop_preview_server()
                self._preview_server = RuntimePreviewServer(self.runtime, self._active_run_id)
            url = self._preview_server.start()
            launched = launch_mobile_preview_target(url)
        except (OSError, RuntimeError) as exc:
            self._append_error(f"无法打开手机预览：{exc}")
            return
        mode = "独立手机窗口" if launched.mode == "app" else "默认浏览器"
        self._append_system(f"已在{mode}中打开手机预览。文章修改完成后会自动刷新。")

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
        self._set_activity("正在取消 · 已停止继续生成，等待当前请求收尾")
        self._append_system("已请求取消当前任务。")

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
            for event in self.runtime.events(self._active_run_id):
                if self._active_agent == "main" or event.agent_id == self._active_agent:
                    self._render_replayed_event(event)
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
            self._active_agent,
            model or "local",
            "running" if self._busy else "real" if self._real_mode else "dry-run",
        )

    def _set_composer_running(self, running: bool) -> None:
        self.query_one("#input-bar", InputBar).set_run_mode(running)

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

    @work(exclusive=True, thread=True)
    def _run_chat_turn(self, user_message: str) -> None:
        self._busy = True
        self.app.call_from_thread(self._set_composer_running, True)
        self._run_started_monotonic = time.monotonic()
        self._streaming_message = None
        self.app.call_from_thread(self._begin_streaming_agent)

        def on_event(event: TurnEvent) -> None:
            self.app.call_from_thread(self._handle_conversation_event, event)

        try:
            active_tools, skill_catalog = installed_capabilities()
            reply = self._conversation_harness.run(
                user_message,
                system_prompt=SYSTEM_PROMPT + "\n你是 AIGC Harness 的对话控制器。先理解意图，必要时调用已安装 Skill；涉及真实外部发布必须先请求用户确认。\n\n" + skill_catalog,
                on_event=on_event,
                model=self._selected_model,
                tools=active_tools,
                execute_tool=execute_tool,
                allow_tool=self._allow_conversation_tool,
            )
            if self._streaming_message is not None:
                self.session.messages.append({"role": "assistant", "content": reply})
                self.session.title = auto_title(self.session.messages)
                save_session(self.session)
        except Exception as exc:
            self.app.call_from_thread(self._append_error, friendly_error(str(exc)))
            self.app.call_from_thread(self._append_error, str(exc), detail_only=True)
        finally:
            self._busy = False
            self._run_started_monotonic = 0.0
            self.app.call_from_thread(self._set_composer_running, False)
            self.app.call_from_thread(self._update_input_placeholder)

    def _handle_conversation_event(self, event: TurnEvent) -> None:
        if event.type == "model.delta":
            self._set_activity("正在回答 · 内容持续生成中")
            self._append_stream_delta(str(event.payload.get("text", "")))
        elif event.type == "model.started":
            self._set_activity("正在思考 · 已连接模型")
            self._append_system(f"正在连接模型 {event.payload.get('model', 'provider default')}…", detail_only=True)
        elif event.type == "heartbeat":
            self._set_activity(f"仍在思考 · 已等待 {event.payload.get('elapsed_seconds', 0)} 秒")
            self._append_system(
                f"模型仍在响应（已等待 {event.payload.get('elapsed_seconds', 0)} 秒）",
                detail_only=True,
            )
        elif event.type == "model.completed":
            self._set_activity("正在整理回答 · 即将完成")
            self._append_system("模型输出完成", detail_only=True)
        elif event.type == "tool.started":
            name = str(event.payload.get("name", ""))
            labels = {
                "collect_papers": "正在搜索近期论文",
                "rank_papers": "正在筛选候选论文",
                "ingest_paper": "正在读取论文和图表",
                "generate_article": "正在生成文章",
                "run_writing_agent": "正在撰写并审阅文章",
                "review_article": "正在检查文章质量",
                "improve_article": "正在修改文章",
                "create_wechat_draft": "正在准备微信草稿",
            }
            self._set_activity(labels.get(name, f"正在处理 · {name or '工作'}"))
            self._append_system(f"正在执行 {event.payload.get('name', '工具')}…", detail_only=True)
        elif event.type == "tool.completed":
            name = str(event.payload.get("name", "工具"))
            self._set_activity(f"已完成 · {name}")
            self._append_system(f"已完成 {event.payload.get('name', '工具')}", detail_only=True)
        elif event.type == "approval.required":
            self._append_system(
                f"工具 {event.payload.get('name', '操作')} 需要你确认；当前对话不会执行真实外部发布。"
            )
        elif event.type == "turn.completed":
            self._set_activity("已完成 · 可以继续提问或修改文章")
        elif event.type == "turn.cancelled":
            self._set_activity("已取消 · 可以重新发送消息")
        elif event.type == "turn.failed":
            self._set_activity("未完成 · 可以重试当前请求")

    @staticmethod
    def _allow_conversation_tool(name: str, arguments: dict[str, object]) -> tuple[bool, str]:
        """Keep model-driven turns side-effect free until the user approves in the UI."""
        if name == "create_wechat_draft" and not bool(arguments.get("dry_run", True)):
            return False, "创建真实微信草稿必须由用户在结果页显式确认。"
        if name == "agent_run":
            arguments["dry_run"] = True
        return True, ""

    @work(exclusive=True, thread=True)
    def _resume_candidate(self, paper_id: str) -> None:
        if not self._active_run_id:
            return
        self._busy = True
        try:
            self.runtime.resolve_interaction(self._active_run_id, paper_id)
            self.app.call_from_thread(self._append_agent, "已确认论文，正在阅读证据并撰写文章……")
            result = self.runtime.resume(self._active_run_id)
            self.app.call_from_thread(self._sync_runtime_state)
            if self._presentation and result.status in {"completed", "failed", "cancelled"}:
                summary = self._presentation.summary_text
                self.session.messages.append({"role": "assistant", "content": summary})
                self.app.call_from_thread(self._append_run_summary)
            if self.session.messages:
                self.session.title = auto_title(self.session.messages)
                save_session(self.session)
        except Exception as exc:
            self._last_runtime_error = f"{type(exc).__name__}: {exc}"
            self.app.call_from_thread(self._append_error, self._last_runtime_error)
            self.app.call_from_thread(self._sync_runtime_state)
        finally:
            self._busy = False
            self.app.call_from_thread(self._update_input_placeholder)

    @work(exclusive=True, thread=True)
    def _rerun_discovery(self, message: str) -> None:
        if not self._active_run_id:
            return
        self._busy = True
        self.app.call_from_thread(self._append_agent, message)
        try:
            result = self.runtime.execute(self._active_run_id)
            self.app.call_from_thread(self._sync_runtime_state)
            if result.status == "waiting_input" and self._presentation:
                count = len(list((self._presentation.interaction or {}).get("options", [])))
                self.app.call_from_thread(self._append_agent, f"重新找到 {count} 篇候选论文，请选择一篇继续。")
            save_session(self.session)
        except Exception as exc:
            self._last_runtime_error = f"{type(exc).__name__}: {exc}"
            self.app.call_from_thread(self._append_error, "重新寻找论文时遇到问题，请修改主题后重试。")
            self.app.call_from_thread(self._sync_runtime_state)
        finally:
            self._busy = False
            self.app.call_from_thread(self._update_input_placeholder)

    @work(exclusive=True, thread=True)
    def _run_revision(self, revision_number: int, instruction: str) -> None:
        if not self._active_run_id:
            return
        self._busy = True
        self.app.call_from_thread(self._append_agent, f"正在修改第 {revision_number} 版：{instruction}")
        try:
            result = self.runtime.execute(self._active_run_id)
            self.app.call_from_thread(self._sync_runtime_state)
            manifest = self.runtime.get_run(self._active_run_id)
            if result.status == "completed" and self._presentation:
                summary = f"第 {revision_number} 版已完成。\n{self._presentation.summary_text}"
                self.session.messages.append({"role": "assistant", "content": summary})
                self.app.call_from_thread(self._append_agent, summary)
            elif manifest.get("fallback_revision_active"):
                self.app.call_from_thread(self._append_error, "本次修改没有完成，上一版文章仍然可用。")
            save_session(self.session)
        except Exception as exc:
            self._last_runtime_error = f"{type(exc).__name__}: {exc}"
            self.app.call_from_thread(self._append_error, "本次修改没有完成，上一版文章仍然可用。")
            self.app.call_from_thread(self._sync_runtime_state)
        finally:
            self._busy = False
            self.app.call_from_thread(self._update_input_placeholder)

    @work(exclusive=True, thread=True)
    def _start_draft_creation(self, article_json: str, quality: dict[str, object]) -> None:
        self._busy = True
        try:
            created = self.runtime.create_run(
                RunRequest("publish-existing", {"article_json": article_json, "quality": quality}, dry_run=False),
                session_id=self.session.id,
            )
            self._active_run_id = created.run_id
            self.session.run_ids.append(created.run_id)
            self._presentation = None
            self._summary_run_id = ""
            self.runtime.subscribe(created.run_id, self._runtime_event_from_worker)
            self.runtime.execute(created.run_id)
            self.app.call_from_thread(self._sync_runtime_state)
            save_session(self.session)
        except Exception as exc:
            self._last_runtime_error = f"{type(exc).__name__}: {exc}"
            self.app.call_from_thread(self._append_error, friendly_error(self._last_runtime_error))
            self.app.call_from_thread(self._append_error, self._last_runtime_error, detail_only=True)
        finally:
            self._busy = False
            self.app.call_from_thread(self._update_input_placeholder)

    @work(exclusive=True, thread=True)
    def _run_agent(self, user_message: str) -> None:
        self._busy = True
        self._run_started_monotonic = time.monotonic()
        self._last_runtime_error = ""
        try:
            workflow_id, inputs = self._runtime_request(user_message)
            real_publish = workflow_id == "paper-to-wechat" and (
                self._real_mode or any(word in user_message for word in ("真实", "正式发布", "真实草稿"))
            )
            created = self.runtime.create_run(
                RunRequest(workflow_id, inputs, dry_run=not real_publish, model=self._selected_model),
                session_id=self.session.id,
            )
            self.app.call_from_thread(self._stop_preview_server)
            self._active_run_id = created.run_id
            if created.run_id not in self.session.run_ids:
                self.session.run_ids.append(created.run_id)
            self._summary_run_id = ""
            self._presentation = None
            self.run_state.start(workflow_id)
            self.app.call_from_thread(self._append_agent, "AutoWechat：正在处理你的研究任务……")
            self.app.call_from_thread(self._show_run_block)
            self.runtime.subscribe(created.run_id, self._runtime_event_from_worker)
            result = self.runtime.execute(created.run_id)
            manifest = self.runtime.get_run(created.run_id)
            if result.status == "cancelled" and manifest.get("restart_requested"):
                manifest["restart_requested"] = False
                self.runtime.store.save(created.run_id, manifest)
                result = self.runtime.resume(created.run_id)
            self.app.call_from_thread(self._sync_runtime_state)
            if self._presentation:
                if result.status == "waiting_input":
                    count = len(list((self._presentation.interaction or {}).get("options", [])))
                    summary = f"AutoWechat：找到 {count} 篇候选论文，请选择一篇继续。"
                    self.session.messages.append({"role": "assistant", "content": summary})
                else:
                    summary = self._presentation.summary_text
                    self.session.messages.append({"role": "assistant", "content": summary})
                    self.app.call_from_thread(self._append_run_summary)

            # Auto-save
            if self.session.messages:
                self.session.title = auto_title(self.session.messages)
                save_session(self.session)
                self.app.call_from_thread(self._update_sidebar)

        except RuntimeError as exc:
            self.run_state.status = RunStatus.FAILED
            self._last_runtime_error = str(exc)
            self.app.call_from_thread(self._append_error, friendly_error(str(exc)))
            self.app.call_from_thread(self._append_error, str(exc), detail_only=True)
            self.app.call_from_thread(self._update_all)
        except Exception as exc:
            self.run_state.status = RunStatus.FAILED
            self._last_runtime_error = f"{type(exc).__name__}: {exc}"
            self.app.call_from_thread(self._append_error, friendly_error(self._last_runtime_error))
            self.app.call_from_thread(self._append_error, self._last_runtime_error, detail_only=True)
            self.app.call_from_thread(self._update_all)
        finally:
            self._busy = False
            self._run_started_monotonic = 0.0
            self.app.call_from_thread(self._update_input_placeholder)

    def _runtime_request(self, message: str) -> tuple[str, dict[str, object]]:
        lower = message.lower()
        if any(word in lower for word in ("微信", "公众号", "wechat", "发布", "草稿")):
            workflow_id = "paper-to-wechat"
        elif any(word in lower for word in ("写", "解读", "article", "深度")):
            workflow_id = "paper-to-article"
        elif any(word in lower for word in ("日报", "周报", "digest", "趋势")):
            workflow_id = "daily-digest"
        elif any(word in lower for word in ("只研究", "只解析", "提取证据", "paper research")):
            workflow_id = "paper-research"
        else:
            workflow_id = "paper-to-article"
        url = re.search(r"https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/[^\s]+", message)
        paper_id_match = re.search(r"(?<!\d)(\d{4}\.\d{4,5}(?:v\d+)?)(?!\d)", message)
        topic = "agents"
        for candidate in ("multimodal", "reasoning", "nlp", "vision", "agents"):
            if candidate in lower:
                topic = candidate
                break
        inputs: dict[str, object] = {
            "request": message,
            "topic": topic,
            "query": message if not url and not paper_id_match else None,
            "days": 30,
            "top_k": 10,
            "candidate_count": 3,
            "ranking_profile": "balanced",
        }
        if "离线示例" in message or "offline example" in lower:
            inputs["offline_example"] = True
        if url:
            inputs["paper_url"] = url.group(0).rstrip(".,，。")
        elif paper_id_match:
            inputs["paper_id"] = paper_id_match.group(1)
        return workflow_id, inputs

    def _runtime_event_from_worker(self, event: RunEvent) -> None:
        self.app.call_from_thread(self._handle_runtime_event, event)

    def _render_replayed_event(self, event: RunEvent) -> None:
        payload = event.payload
        if event.type == "step.started":
            phase = {
                "collect": "正在寻找研究材料",
                "rank": "正在筛选候选论文",
                "select": "正在确认论文",
                "ingest": "正在读取论文和图表",
                "write": "正在撰写文章",
                "review": "正在检查文章质量",
                "publish": "正在准备发布包",
            }
            step_name = str(payload.get("step", ""))
            self._set_activity(phase.get(step_name, f"正在处理 · {step_name or '当前阶段'}"))
            self._append_step(event.agent_id, f"Running {payload.get('step')}")
        elif event.type == "step.completed":
            self._set_activity(f"已完成 · {payload.get('step', '当前阶段')}")
            self._append_step(event.agent_id, f"Completed {payload.get('step')}")
        elif event.type == "tool.started":
            self._append_tool(str(payload.get("tool", "tool")), "Started", detail_only=True)
        elif event.type == "tool.completed":
            self._append_collapsed_tool(event)
        elif event.type == "artifact.created":
            self._append_artifact(str(payload.get("path", "")), str(payload.get("producer", "")))
        elif event.type in {"tool.failed", "step.failed"}:
            self._last_runtime_error = str(payload.get("error", "Runtime failed"))
            self._append_error(self._last_runtime_error, detail_only=True)
        elif event.type == "run.failed":
            self._last_runtime_error = str(payload.get("error", "Runtime failed"))
            self._append_error(friendly_error(self._last_runtime_error))
            self._append_error(self._last_runtime_error, detail_only=True)
            self._set_activity("未完成 · 已保留当前产物，可以检查失败原因后重试")
        elif event.type == "approval.required":
            self._pending_approval = str(payload.get("id", ""))
        elif event.type == "interaction.required":
            self._append_agent("候选论文已经准备好，请选择一篇继续。")

    def _handle_runtime_event(self, event: RunEvent) -> None:
        if self._active_agent != "main" and event.agent_id != self._active_agent:
            self._sync_runtime_state()
            return
        payload = event.payload
        if event.type == "step.started":
            self._append_step(event.agent_id, f"Running {payload.get('step')}")
        elif event.type == "step.completed":
            self._append_step(event.agent_id, f"Completed {payload.get('step')}")
        elif event.type == "tool.started":
            self._append_tool(str(payload.get("tool", "tool")), "Started", detail_only=True)
        elif event.type == "tool.completed":
            self._append_collapsed_tool(event)
        elif event.type == "artifact.created":
            self._append_artifact(str(payload.get("path", "")), str(payload.get("producer", "")))
        elif event.type in {"tool.failed", "step.failed"}:
            self._last_runtime_error = str(payload.get("error", "Runtime failed"))
            self._append_error(self._last_runtime_error, detail_only=True)
        elif event.type == "run.failed":
            self._last_runtime_error = str(payload.get("error", "Runtime failed"))
            self._append_error(friendly_error(self._last_runtime_error))
            self._append_error(self._last_runtime_error, detail_only=True)
        elif event.type == "agent.started":
            self._append_agent(f"{event.agent_id} started", detail_only=True)
        elif event.type == "approval.required":
            self._pending_approval = str(payload.get("id", ""))
            self.app.push_screen(ApprovalModal(payload), self._approval_decision)
        elif event.type == "interaction.required":
            self._set_activity("等待选择 · 请在上方候选中确认一篇论文")
            self._append_agent("候选论文已经准备好，请选择一篇继续。")
        elif event.type == "run.completed":
            self._last_runtime_error = ""
            self._set_activity("已完成 · 文章已自动保存，可以继续修改或预览")
        self._sync_runtime_state()
        if event.type in {"run.completed", "run.failed"}:
            self._append_run_summary()

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

    def _approval_decision(self, approved: bool | None) -> None:
        if self._pending_approval:
            self._resolve_approval(self._pending_approval, bool(approved))

    @work(exclusive=True, thread=True)
    def _resolve_approval(self, approval_id: str, approved: bool) -> None:
        if not self._active_run_id:
            return
        self.runtime.resolve_approval(self._active_run_id, approval_id, approved)
        self._pending_approval = ""
        if approved:
            result = self.runtime.execute(self._active_run_id)
            self.app.call_from_thread(self._sync_runtime_state)
            if result.status == "completed" and self._presentation:
                summary = self._presentation.summary_text
                self.session.messages.append({"role": "assistant", "content": summary})
                self.app.call_from_thread(self._append_agent, summary)
                save_session(self.session)
        else:
            self.app.call_from_thread(self._sync_runtime_state)

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
    """SmearglePaper Agent Console."""

    TITLE = "AutoWechat"
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
