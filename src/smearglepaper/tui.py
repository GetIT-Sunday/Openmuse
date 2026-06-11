"""SmearglePaper Agent Console — OpenCode-style TUI.

Three-panel layout: Sidebar | Conversation Trace | Context Panel
with Command Palette, Welcome Screen, and unified RunState.
"""
from __future__ import annotations

import json
import subprocess
import sys

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Footer,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
)

from .config import DATA_DIR
from .llm import agentic_loop
from .run_state import (
    ArtifactInfo,
    RunMode,
    RunState,
    RunStatus,
    StepStatus,
    WorkflowStep,
    default_workflow_steps,
    demo_run_state,
    load_model_info,
    scan_artifacts,
)
from .session import Session, auto_title, delete_session, list_sessions, load_session, save_session
from .storage import read_json
from .trace_message import TraceMessage
from .tui_components import (
    CommandPalette,
    ContextPanel,
    HeaderBar,
    InputBar,
    RecentActivityWidget,
    RunBlockWidget,
    Sidebar,
    WelcomeWidget,
    COMMANDS,
)
from .tui_theme import APP_CSS
from .tools import SYSTEM_PROMPT, TOOLS, execute_tool


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


# ── Main Screen ──────────────────────────────────────────────────────────

class AgentConsole(Screen):
    CSS = APP_CSS
    BINDINGS = [
        Binding("ctrl+n", "new_session", "New Session"),
        Binding("ctrl+r", "demo_run", "Demo Run"),
        Binding("ctrl+s", "save_session", "Save"),
        Binding("ctrl+c", "copy_selection", "Copy"),
        Binding("ctrl+l", "clear_screen", "Clear"),
        Binding("ctrl+p", "toggle_palette", "Commands"),
        Binding("ctrl+b", "toggle_context", "Context"),
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

    def compose(self) -> ComposeResult:
        sessions = list_sessions()
        if self.session not in sessions:
            sessions.insert(0, self.session)

        yield HeaderBar(id="header-bar")

        with Horizontal(id="main-container"):
            yield Sidebar(sessions=sessions, id="sidebar")

            with Vertical(id="main-area"):
                # Welcome state: card + recent activity
                yield Vertical(
                    WelcomeWidget(id="welcome"),
                    RecentActivityWidget(id="recent-activity"),
                    id="welcome-area",
                )
                # Run state: trace + run block
                yield RunBlockWidget(id="run-block")
                yield RichLog(id="trace-area", markup=True, highlight=True, wrap=True)
                yield InputBar(id="input-bar")

            yield ContextPanel(id="context-panel")

        yield Static(self._footer_text(), id="footer-bar")
        yield CommandPalette(id="command-palette")

    def _footer_text(self) -> str:
        return (
            "[#eab308]^N[/] New  "
            "[#eab308]^R[/] Run  "
            "[#eab308]^S[/] Save  "
            "[#eab308]^P[/] Commands  "
            "[#eab308]^L[/] Clear  "
            "[#eab308]^B[/] Context  "
            "[#eab308]^?[/] Help  "
            "[#eab308]^Q[/] Quit"
        )

    def on_mount(self) -> None:
        self.query_one("#header-bar", HeaderBar).update_state(self.run_state, self.session.id)
        self._update_context()
        # Show welcome area, hide trace + run block
        self.query_one("#welcome-area").display = True
        self.query_one("#trace-area", RichLog).display = False
        self.query_one("#run-block", RunBlockWidget).display = False
        self._welcome_visible = True

    def on_rich_log_selection_changed(self, event: RichLog.SelectionChanged) -> None:
        """Auto-copy selected text to clipboard."""
        if event.selected_text:
            copy_to_clipboard(event.selected_text)

    # ── Welcome / Empty State ────────────────────────────────────────────

    def _show_welcome(self) -> None:
        self.query_one("#welcome-area").display = True
        self.query_one("#trace-area", RichLog).display = False
        self.query_one("#run-block", RunBlockWidget).display = False
        self._welcome_visible = True

    def _hide_welcome(self) -> None:
        if self._welcome_visible:
            self.query_one("#welcome-area").display = False
            self.query_one("#trace-area", RichLog).display = True
            self._welcome_visible = False

    def _check_llm_on_start(self) -> None:
        """Check LLM connection on startup and show status."""
        from .llm import check_llm_connection
        status = check_llm_connection()
        if status.get("ok"):
            model = status.get("model", "unknown")
            # LLM is connected, ready for chat
        else:
            # LLM not available, show hint
            pass

    def _show_run_block(self) -> None:
        rb = self.query_one("#run-block", RunBlockWidget)
        rb.display = True
        rb.update_state(self.run_state)

    def _hide_run_block(self) -> None:
        self.query_one("#run-block", RunBlockWidget).display = False

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

    def _append_system(self, text: str) -> None:
        msg = TraceMessage.create("system", text)
        self._trace_messages.append(msg)
        self.query_one("#trace-area", RichLog).write(msg.to_rich_text())

    def _append_user(self, text: str) -> None:
        msg = TraceMessage.create("user", text)
        self._trace_messages.append(msg)
        self.query_one("#trace-area", RichLog).write(msg.to_rich_text())

    def _append_agent(self, text: str) -> None:
        msg = TraceMessage.create("agent", text)
        self._trace_messages.append(msg)
        self._last_agent_reply = text
        self.query_one("#trace-area", RichLog).write(msg.to_rich_text())

    def _append_tool(self, name: str, result: str) -> None:
        preview = result[:200] + "..." if len(result) > 200 else result
        msg = TraceMessage.create("tool", f"{name}\n  {preview}")
        self._trace_messages.append(msg)
        self.query_one("#trace-area", RichLog).write(msg.to_rich_text())

    def _append_step(self, name: str, detail: str) -> None:
        msg = TraceMessage.create("step", f"{name}\n  {detail}")
        self._trace_messages.append(msg)
        self.query_one("#trace-area", RichLog).write(msg.to_rich_text())

    def _append_artifact(self, path: str) -> None:
        msg = TraceMessage.create("artifact", f"📄 {path}")
        self._trace_messages.append(msg)
        self.query_one("#trace-area", RichLog).write(msg.to_rich_text())

    def _append_error(self, text: str) -> None:
        msg = TraceMessage.create("error", text)
        self._trace_messages.append(msg)
        self.query_one("#trace-area", RichLog).write(msg.to_rich_text())

    # ── State Updates ────────────────────────────────────────────────────

    def _update_header(self) -> None:
        self.query_one("#header-bar", HeaderBar).update_state(self.run_state, self.session.id)

    def _update_context(self) -> None:
        self.query_one("#context-panel", ContextPanel).update_state(self.run_state)

    def _update_sidebar(self) -> None:
        sessions = list_sessions()
        if self.session not in sessions:
            sessions.insert(0, self.session)
        self.query_one("#sidebar", Sidebar).refresh_sessions(sessions, self.session.id)

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

    def _handle_user_input(self, text: str) -> None:
        if self._busy:
            self._append_system("⏳ 正在处理中，请稍候...")
            return

        if text.startswith("/"):
            self._handle_slash_command(text)
            return

        self._hide_welcome()
        self._append_user(text)
        self.session.messages.append({"role": "user", "content": text})
        self._run_agent(text)

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
        elif cmd == "/exit" or cmd == "/quit":
            self.app.exit()
        elif cmd == "/help":
            self._append_system(
                "可用命令:\n"
                "  /clear — 清空屏幕\n"
                "  /status — 检查系统状态\n"
                "  /demo-run — 进入 Demo Run Mode\n"
                "  /delete — 删除当前会话\n"
                "  /exit — 退出 TUI\n"
                "  /help — 显示此帮助\n\n"
                "快捷键:\n"
                "  Ctrl+R — Demo Run\n"
                "  Ctrl+N — 新建会话\n"
                "  Ctrl+P — 命令面板\n"
                "  Ctrl+Q — 退出"
            )
        else:
            self._append_error(f"未知命令: {cmd}。输入 /help 查看帮助。")

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

    # ── Session List ─────────────────────────────────────────────────────

    @on(ListView.Selected, "#session-list")
    def on_session_selected(self, event: ListView.Selected) -> None:
        item_id = str(event.item.id or "")
        if item_id.startswith("session-"):
            session_id = item_id.replace("session-", "")
            try:
                session = load_session(session_id)
                self._reload_session(session)
            except FileNotFoundError:
                self._append_error(f"会话不存在: {session_id}")

    def _reload_session(self, session: Session) -> None:
        self.session = session
        self.run_state = RunState(model=load_model_info())
        self._last_agent_reply = ""
        self._welcome_visible = True

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

        self._update_header()
        self._update_context()
        self._update_sidebar()

    # ── Actions ──────────────────────────────────────────────────────────

    def action_new_session(self) -> None:
        self.session = Session.create()
        self.run_state = RunState(model=load_model_info())
        self._last_agent_reply = ""
        self._welcome_visible = True

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
        ctx = self.query_one("#context-panel", ContextPanel)
        self._context_visible = not self._context_visible
        ctx.display = self._context_visible

    def action_demo_run(self) -> None:
        """Trigger agent with demo message."""
        self._handle_user_input("帮我看看最近 agents 方向有哪些论文")

    def _update_all(self) -> None:
        """Update all panels from RunState."""
        self._update_header()
        self._update_context()
        self._update_sidebar()
        self._update_run_block()
        self._update_input_placeholder()

    def _update_run_block(self) -> None:
        rb = self.query_one("#run-block", RunBlockWidget)
        rb.update_state(self.run_state)

    def _update_input_placeholder(self) -> None:
        input_widget = self.query_one("#chat-input", Input)
        if self.run_state.status == RunStatus.RUNNING:
            input_widget.placeholder = "> Agent is running..."
        else:
            input_widget.placeholder = "> Ask SmearglePaper or type a command..."

    def _append_trace_msg(self, msg_type: str, title: str, content: str) -> None:
        """Append a trace message to the main area."""
        msg = TraceMessage.create(msg_type, content, title=title)
        self._trace_messages.append(msg)
        log = self.query_one("#trace-area", RichLog)
        log.write(msg.to_rich_text())

    def _update_sidebar(self) -> None:
        """Update sidebar with current state."""
        sessions = list_sessions()
        if self.session not in sessions:
            sessions.insert(0, self.session)
        self.query_one("#sidebar", Sidebar).refresh_sessions(sessions, self.session.id)
        # Update artifacts from RunState
        if self.run_state.artifacts:
            sidebar = self.query_one("#sidebar", Sidebar)
            mock_artifacts = [
                {"name": a.name, "size": a.size or "", "icon": a.icon}
                for a in self.run_state.artifacts
            ]
            sidebar.update_artifacts_for_run(mock_artifacts)

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
    def _run_agent(self, user_message: str) -> None:
        self._busy = True
        try:
            # Initialize run state for this conversation
            self.run_state = RunState(
                id=self.session.id,
                title=user_message[:30],
                mode=RunMode.RUN,
                status=RunStatus.RUNNING,
                current_task="Thinking...",
                steps=default_workflow_steps(),
                model=load_model_info(),
            )
            self.app.call_from_thread(self._update_all)
            self.app.call_from_thread(self._show_run_block)

            # Tool name → step name mapping
            tool_step_map = {
                "collect_papers": "collect-papers",
                "collect_blogs": "collect-papers",
                "collect_github": "collect-papers",
                "rank_papers": "rank-papers",
                "ingest_paper": "ingest-paper",
                "generate_article": "generate-article",
                "review_article": "review-article",
                "create_wechat_draft": "create-draft",
            }

            def tool_executor(name: str, arguments: dict) -> str:
                step_name = tool_step_map.get(name, "")

                # Mark step as running
                if step_name:
                    self.run_state.current_step = step_name
                    self.run_state.current_task = f"Running {step_name}"
                    self.run_state.set_step(step_name, StepStatus.RUNNING)
                    self.app.call_from_thread(self._update_all)
                    self.app.call_from_thread(self._append_trace_msg, "step", step_name, f"⟳ 正在执行 {step_name}...")

                # Execute the actual tool
                result = execute_tool(name, arguments)

                # Parse result and update state
                try:
                    data = json.loads(result)
                    success = "error" not in data
                except Exception:
                    data = {}
                    success = True

                if step_name:
                    status = StepStatus.SUCCESS if success else StepStatus.FAILED
                    self.run_state.set_step(step_name, status)

                    # Update stats based on tool
                    if name == "collect_papers" and success:
                        count = data.get("count", 0)
                        self.run_state.stats.papers_collected = count
                        today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
                        self.run_state.artifacts.append(ArtifactInfo(
                            name=f"arxiv-{today}.json",
                            path=f"data/papers/arxiv-{today}.json",
                            artifact_type="file",
                            size=f"{count} papers",
                            status="created",
                        ))
                    elif name == "rank_papers" and success:
                        count = data.get("count", 0)
                        self.run_state.stats.papers_selected = count
                        self.run_state.artifacts.append(ArtifactInfo(
                            name="ranked/latest.json",
                            path="data/ranked/latest.json",
                            artifact_type="file",
                            size="ranked",
                            status="created",
                        ))

                    self.app.call_from_thread(self._update_all)

                # Append tool trace
                preview = result[:200] + "..." if len(result) > 200 else result
                self.app.call_from_thread(self._append_trace_msg, "tool", name, f"✓ {preview}")

                return result

            # Run the LLM agentic loop
            self.app.call_from_thread(self._append_trace_msg, "agent", "Thinking", "正在分析你的请求...")

            messages, reply = agentic_loop(
                user_message=user_message,
                tools=TOOLS,
                system_prompt=SYSTEM_PROMPT,
                execute_fn=tool_executor,
                history=self.session.messages,
                max_rounds=10,
            )
            self.session.messages = messages

            if reply:
                self.app.call_from_thread(self._append_trace_msg, "agent", "Reply", reply)

            # Finish
            self.run_state.status = RunStatus.SUCCESS
            self.run_state.current_step = ""
            self.run_state.current_task = ""
            self.app.call_from_thread(self._update_all)

            # Auto-save
            if self.session.messages:
                self.session.title = auto_title(self.session.messages)
                save_session(self.session)
                self.app.call_from_thread(self._update_sidebar)

        except RuntimeError as exc:
            self.run_state.status = RunStatus.FAILED
            self.app.call_from_thread(self._append_trace_msg, "error", "Error", str(exc))
            self.app.call_from_thread(self._update_all)
        except Exception as exc:
            self.run_state.status = RunStatus.FAILED
            self.app.call_from_thread(self._append_trace_msg, "error", "Error", f"{type(exc).__name__}: {exc}")
            self.app.call_from_thread(self._update_all)
        finally:
            self._busy = False


# ── App ─────────────────────────────────────────────────────────────────

class SmearglePaperApp(App):
    """SmearglePaper Agent Console."""

    TITLE = "SmearglePaper Agent"
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
