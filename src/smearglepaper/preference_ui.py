"""Small user-owned preference editor; no model can confirm these actions."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Select, Static

from .preference_memory import KINDS, PACK_SCOPE, PreferenceMemory


class PreferenceMemoryModal(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "关闭", priority=True)]
    DEFAULT_CSS = """
    PreferenceMemoryModal { align: center middle; background: #000000 65%; }
    #preference-panel { width: 76; max-width: 94%; height: auto; max-height: 100%;
        padding: 1 2; background: #151518; overflow-y: auto; }
    #preference-list { height: 5; min-height: 3; margin: 1 0; background: #1f1f23; }
    #preference-list ListItem { height: 1; }
    #preference-details { height: 3; color: #98989f; }
    #preference-form { height: 3; }
    #preference-form Select { width: 1fr; }
    #preference-actions { height: 3; }
    #preference-actions Button { width: 1fr; min-width: 0; padding: 0; }
    #preference-status { height: auto; max-height: 2; color: #eab308; }
    PreferenceMemoryModal.compact #preference-panel { padding: 0 1; }
    PreferenceMemoryModal.compact #preference-list { height: 3; margin: 0; }
    PreferenceMemoryModal.compact #preference-status { height: 1; }
    """

    def __init__(self, memory: PreferenceMemory, session_id: str,
                 on_change: Callable[[str, dict[str, object]], None]) -> None:
        super().__init__()
        self.memory, self.session_id, self.on_change = memory, session_id, on_change
        self.rows: list[dict[str, Any]] = []
        self.selected: dict[str, Any] | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="preference-panel"):
            yield Label("偏好记忆 · 由你决定记住什么")
            yield Static("确认后用于本项目的新会话；当前要求优先，不授权任何发布。")
            yield ListView(id="preference-list")
            yield Static("尚无偏好。可直接添加，或在对话中说“记住我的写作偏好”。", id="preference-details", markup=False)
            with Horizontal(id="preference-form"):
                yield Select([(v, k) for k, v in KINDS.items()], value="audience", allow_blank=False, id="preference-kind")
                yield Select([("此项目", "workspace"), ("论文 Pack", PACK_SCOPE)], value="workspace", allow_blank=False, id="preference-scope")
                yield Select([("90 天", 90), ("一年", 365), ("长期", 0)], value=365, allow_blank=False, id="preference-days")
            yield Input(placeholder="例如：面向工程师，避免标题党（最多 300 字）", max_length=300, id="preference-value")
            yield Static("不保存凭据；忘记偏好不会删除聊天记录或文章。", id="preference-status", markup=False)
            with Horizontal(id="preference-actions"):
                yield Button("新增", id="preference-new")
                yield Button("确认保存", id="preference-save", variant="primary")
                yield Button("不保存", id="preference-reject")
                yield Button("忘记", id="preference-forget")
                yield Button("关闭", id="preference-close")

    async def on_mount(self) -> None:
        # The application can exit while an asynchronously pushed modal mounts.
        if self.query("#preference-list"):
            self.set_class(self.size.height < 30, "compact")
            await self.refresh_rows()
            try:
                if not self.memory.enabled():
                    self.query_one("#preference-status", Static).update("偏好使用已暂停；可管理记录，通过 /memory on 恢复。")
            except (ValueError, OSError) as exc:
                self.query_one("#preference-status", Static).update(str(exc))

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(event.size.height < 30, "compact")

    async def refresh_rows(self) -> None:
        self.selected = None
        try:
            self.rows = self.memory.entries()
        except (ValueError, OSError) as exc:
            self.query_one("#preference-status", Static).update(str(exc))
            self.rows = []
        self.rows.sort(key=lambda r: (r["status"] != "pending", r["kind"], r["scope"]))
        additional = sorted({r["scope"] for r in self.rows} - {"workspace", PACK_SCOPE})
        self.query_one("#preference-scope", Select).set_options(
            [("此项目", "workspace"), ("论文 Pack", PACK_SCOPE), *[(s, s) for s in additional]])
        self.query_one("#preference-scope", Select).value = "workspace"
        listing = self.query_one("#preference-list", ListView)
        await listing.clear()
        for row in self.rows:
            status = "已过期" if row["expired"] else "待确认" if row["status"] == "pending" else "已保存"
            value = row["value"].replace("\n", " ")[:26]
            await listing.append(ListItem(Label(f"{status} · {KINDS[row['kind']]} · {value}", markup=False), name=row["id"]))
        self.new_entry()

    def new_entry(self) -> None:
        self.selected = None
        self.query_one("#preference-kind", Select).disabled = False
        self.query_one("#preference-scope", Select).disabled = False
        self.query_one("#preference-value", Input).value = ""
        self.query_one("#preference-details", Static).update("新增偏好：确认保存后生效。相同范围和类别会替换旧值。")
        self.query_one("#preference-reject", Button).disabled = True
        self.query_one("#preference-forget", Button).disabled = True

    @on(ListView.Selected, "#preference-list")
    def select_entry(self, event: ListView.Selected) -> None:
        self.selected = next((r for r in self.rows if r["id"] == event.item.name), None)
        if self.selected is None:
            return
        row = self.selected
        self.query_one("#preference-kind", Select).value = row["kind"]
        self.query_one("#preference-kind", Select).disabled = True
        self.query_one("#preference-scope", Select).value = row["scope"]
        self.query_one("#preference-scope", Select).disabled = True
        self.query_one("#preference-value", Input).value = row["value"]
        self._selected_days = 365 if row["expires_at"] else 0
        self.query_one("#preference-days", Select).value = self._selected_days
        previous = next((r["value"] for r in self.rows if r["kind"] == row["kind"] and r["scope"] == row["scope"] and r["status"] == "active"), "")
        replacement = f"将替换：{previous[:24]}" if row["status"] == "pending" and previous else ""
        self.query_one("#preference-details", Static).update(
            f"来源会话 {row['source_session'][:12] or '手动添加'} · 轮次 {row['source_turn'][:12] or '手动'}\n"
            f"更新 {row['updated_at'][:10]} · 到期 {(row['expires_at'] or '长期')[:10]}\n"
            f"ID {row['id']} · {replacement or ('待确认' if row['status'] == 'pending' else '已保存')}")
        self.query_one("#preference-reject", Button).disabled = row["status"] != "pending"
        self.query_one("#preference-forget", Button).disabled = False

    @on(Button.Pressed)
    async def handle_action(self, event: Button.Pressed) -> None:
        event.stop()
        action = event.button.id
        if action == "preference-close":
            self.dismiss(None)
            return
        if action == "preference-new":
            self.new_entry()
            self.query_one("#preference-value", Input).focus()
            return
        try:
            row = self.selected
            if action == "preference-save":
                value = self.query_one("#preference-value", Input).value
                if (row and row["status"] == "pending" and value.strip() == row["value"]
                        and self.query_one("#preference-days", Select).value == self._selected_days):
                    proposed = row
                else:
                    proposed = self.memory.propose(str(self.query_one("#preference-kind", Select).value), value,
                        scope=str(self.query_one("#preference-scope", Select).value),
                        days=int(str(self.query_one("#preference-days", Select).value)), session_id=self.session_id,
                        expected_version=(row["version"] if row["status"] == "active" else row["base_version"]) if row else None)
                saved = self.memory.confirm(str(proposed["id"]))
                if row and row["status"] == "pending" and row["id"] != saved["id"]:
                    self.memory.reject(row["id"])
                self.on_change("memory.confirmed", {"id": saved["id"], "kind": saved["kind"], "scope": saved["scope"]})
                message = "已保存。此后相关对话会参考这条偏好；当前要求仍优先。"
            elif action == "preference-reject" and row:
                self.memory.reject(row["id"])
                self.on_change("memory.rejected", {"id": row["id"]})
                message = "未保存这条提议。"
            elif action == "preference-forget" and row:
                self.memory.forget(row["id"])
                self.on_change("memory.forgotten", {"id": row["id"]})
                message = "已忘记。后续不再从偏好库读取；聊天记录和文章未删除。"
            else:
                return
            await self.refresh_rows()
            self.query_one("#preference-status", Static).update(message)
        except (ValueError, OSError) as exc:
            self.query_one("#preference-status", Static).update(str(exc))

    def action_close(self) -> None:
        self.dismiss(None)
