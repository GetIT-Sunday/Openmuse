"""Local, consent-gated preferences, separate from transcripts and task state."""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .context_budget import estimate_tokens

KINDS = {"audience": "目标读者", "tone": "表达风格", "depth": "解读深度", "avoid": "避免事项", "topics": "关注主题"}
PACK_SCOPE = "pack:autowechat/research-to-wechat"
_SENSITIVE = re.compile(
    r"\b(?:sk-|tp-|ghp_|github_pat_)[\w-]{12,}|bearer\s+\S+|-----BEGIN .*PRIVATE KEY|"
    r"(?:api[_ -]?key|token|password|secret|密码|密钥)\s*[:=：]\s*\S+", re.I)


def _terms(text: str) -> set[str]:
    result = set(re.findall(r"[a-z0-9_-]{2,}", text.lower()))
    for part in re.findall(r"[\u4e00-\u9fff]+", text):
        result.update(part[i:i + 2] for i in range(len(part) - 1))
    return result


class PreferenceMemory:
    """One DB per workspace; profile and Pack are explicit logical namespaces.

    Only ``confirm`` activates a proposal. These mutators are UI APIs, never
    model tools. Rejected/deleted proposals retain only empty idempotency receipts.
    """

    def __init__(self, path: Path, *, profile: str = "default",
                 clock: Callable[[], datetime] | None = None) -> None:
        if not re.fullmatch(r"[\w-]{1,64}", profile):
            raise ValueError("记忆档案标识无效。")
        self.path, self.profile = path, profile
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        connection = sqlite3.connect(self.path, timeout=3)
        connection.row_factory = sqlite3.Row
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in {0, 1}:
                raise ValueError("偏好数据库版本不受支持；已保留原文件。")
            connection.execute("PRAGMA secure_delete=ON")
            connection.execute("""CREATE TABLE IF NOT EXISTS preferences (
                id TEXT PRIMARY KEY, profile TEXT NOT NULL, scope TEXT NOT NULL,
                kind TEXT NOT NULL, value TEXT NOT NULL, status TEXT NOT NULL,
                version INTEGER NOT NULL, base_version INTEGER NOT NULL,
                source_session TEXT NOT NULL, source_turn TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                expires_at TEXT, operation_id TEXT NOT NULL,
                UNIQUE(profile, operation_id))""")
            connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS active_preference
                ON preferences(profile, scope, kind) WHERE status='active'""")
            connection.execute("CREATE TABLE IF NOT EXISTS preference_settings (profile TEXT PRIMARY KEY, enabled INTEGER NOT NULL)")
            if version == 0:
                connection.execute("PRAGMA user_version=1")
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
        except sqlite3.DatabaseError as exc:
            raise ValueError("偏好存储无法读取或写入；已保留原文件，请检查后重试。") from exc
        finally:
            connection.close()

    @staticmethod
    def _validate(kind: str, value: str, scope: str, days: int) -> str:
        value = value.strip()
        if kind not in KINDS or not (scope == "workspace" or re.fullmatch(r"pack:[a-z0-9/_-]{1,100}", scope)):
            raise ValueError("请选择有效的偏好类别与适用范围。")
        if not value or len(value) > 300 or len(value.encode()) > 900:
            raise ValueError("请用 1–300 字描述一条写作偏好。")
        if _SENSITIVE.search(value):
            raise ValueError("疑似凭据不能保存为偏好，请在 /connect 中配置。")
        if type(days) is not int or not 0 <= days <= 3650:
            raise ValueError("有效期为 1–3650 天，0 表示长期保留。")
        return value

    def enabled(self) -> bool:
        if not self.path.exists():
            return True
        with self._db() as db:
            row = db.execute("SELECT enabled FROM preference_settings WHERE profile=?", (self.profile,)).fetchone()
        return bool(row[0]) if row else True

    def set_enabled(self, enabled: bool) -> None:
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO preference_settings VALUES (?, ?)", (self.profile, int(enabled)))

    def entries(self, *, session_id: str | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self._db() as db:
            rows = db.execute("""SELECT * FROM preferences WHERE profile=? AND status IN ('active','pending')
                ORDER BY updated_at DESC, id""", (self.profile,)).fetchall()
        result = [dict(row) for row in rows]
        if session_id is not None:
            result = [r for r in result if r["status"] == "active" or r["source_session"] == session_id]
        now = self.clock().isoformat()
        for row in result:
            row["expired"] = bool(row["expires_at"] and row["expires_at"] <= now)
        return result

    def propose(self, kind: str, value: str, *, scope: str = "workspace", days: int = 365,
                session_id: str = "", turn_id: str = "", operation_id: str = "",
                expected_version: int | None = None) -> dict[str, Any]:
        value = self._validate(kind, value, scope, days)
        operation_id = operation_id or uuid.uuid4().hex
        identifier = hashlib.sha256(f"{self.profile}:{operation_id}".encode()).hexdigest()[:16]
        now = self.clock()
        with self._db() as db:
            existing = db.execute("SELECT * FROM preferences WHERE profile=? AND operation_id=?",
                                  (self.profile, operation_id)).fetchone()
            if existing:
                return dict(existing)
            count = db.execute("SELECT COUNT(*) FROM preferences WHERE profile=? AND status IN ('active','pending')", (self.profile,)).fetchone()[0]
            if count >= 100:
                raise ValueError("偏好记录已达 100 条，请先整理或删除不再需要的记录。")
            previous = db.execute("SELECT version FROM preferences WHERE profile=? AND scope=? AND kind=? AND status='active'",
                                  (self.profile, scope, kind)).fetchone()
            base = int(previous[0]) if previous else 0
            if expected_version is not None and expected_version != base:
                raise ValueError("偏好已在其他窗口更新，请刷新后重试。")
            db.execute("INSERT INTO preferences VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                identifier, self.profile, scope, kind, value, "pending", base + 1, base,
                session_id, turn_id, now.isoformat(), now.isoformat(),
                (now + timedelta(days=days)).isoformat() if days else None, operation_id))
            return dict(db.execute("SELECT * FROM preferences WHERE id=?", (identifier,)).fetchone())

    def confirm(self, identifier: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM preferences WHERE id=? AND profile=?", (identifier, self.profile)).fetchone()
            if row is None or row["status"] != "pending":
                raise ValueError("此偏好已处理或不存在，请刷新列表。")
            if row["expires_at"] and row["expires_at"] <= self.clock().isoformat():
                raise ValueError("此偏好提议已过期，请重新添加。")
            previous = db.execute("SELECT version FROM preferences WHERE profile=? AND scope=? AND kind=? AND status='active'",
                                  (self.profile, row["scope"], row["kind"])).fetchone()
            if (int(previous[0]) if previous else 0) != row["base_version"]:
                raise ValueError("同类偏好已被修改；请刷新后重新编辑，避免覆盖新内容。")
            db.execute("UPDATE preferences SET status='superseded', value='' WHERE profile=? AND scope=? AND kind=? AND status='active'",
                       (self.profile, row["scope"], row["kind"]))
            db.execute("UPDATE preferences SET status='active', updated_at=? WHERE id=?", (self.clock().isoformat(), identifier))
            return dict(db.execute("SELECT * FROM preferences WHERE id=?", (identifier,)).fetchone())

    def reject(self, identifier: str) -> None:
        with self._db() as db:
            db.execute("UPDATE preferences SET status='rejected', value='' WHERE id=? AND profile=? AND status='pending'",
                       (identifier, self.profile))

    def forget(self, identifier: str) -> None:
        with self._db() as db:
            row = db.execute("SELECT * FROM preferences WHERE id=? AND profile=? AND status IN ('active','pending')",
                             (identifier, self.profile)).fetchone()
            if row is None:
                raise ValueError("偏好不存在，请刷新列表。")
            # Redact pending replacements too, so a stale confirmation cannot resurrect it.
            db.execute("UPDATE preferences SET status='forgotten', value='' WHERE profile=? AND scope=? AND kind=?",
                       (self.profile, row["scope"], row["kind"]))

    def retrieve(self, query: str, *, scope: str = "workspace", writing: bool = False,
                 budget: int = 1800) -> list[dict[str, Any]]:
        if not self.enabled():
            return []
        tokens = _terms(query)
        writing = writing or bool(tokens & _terms("写作文章公众号标题读者文风偏好研究论文修改 writing article paper preferences"))
        ranked = []
        for row in self.entries():
            if row["status"] != "active" or row["expired"] or row["scope"] not in {"workspace", scope}:
                continue
            overlap = len(tokens & _terms(row["value"]))
            if not overlap and not (writing and row["kind"] != "topics"):
                continue
            ranked.append((overlap + (2 if row["scope"] == scope else 0), row))
        ranked.sort(key=lambda item: (item[1]["scope"] == scope, item[0], item[1]["updated_at"], item[1]["id"]), reverse=True)
        selected: list[dict[str, Any]] = []
        for score, row in ranked:
            if any(item["kind"] == row["kind"] for item in selected):
                continue
            item = {k: row[k] for k in ("id", "kind", "value", "scope", "source_session", "source_turn", "updated_at", "expires_at")}
            item["relevance"] = score
            if estimate_tokens([*selected, item]) <= budget:
                selected.append(item)
        return selected
