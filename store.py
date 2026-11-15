"""本地数据存储：记忆上次输入的用户 + 吧务操作日志（SQLite）。"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS targets (
    key          TEXT PRIMARY KEY,
    user_id      INTEGER,
    user_name    TEXT,
    nick_name    TEXT,
    portrait     TEXT,
    avatar       TEXT,
    forum_level  INTEGER,
    last_used_at INTEGER,
    use_count    INTEGER NOT NULL DEFAULT 1,
    last_day     INTEGER,
    last_reason  TEXT
);

CREATE TABLE IF NOT EXISTS logs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        INTEGER NOT NULL,
    action    TEXT NOT NULL,
    fname     TEXT,
    user_id   INTEGER,
    user_name TEXT,
    nick_name TEXT,
    avatar    TEXT,
    day       INTEGER,
    reason    TEXT,
    ok        INTEGER,
    message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts DESC);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._write(_SCHEMA)

    # ---------- 基础 ----------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _write(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(sql) if sql.lstrip().upper().startswith("CREATE") else conn.execute(sql, params)
                conn.commit()
            finally:
                conn.close()

    def _fetch(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(sql, params).fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()

    # ---------- 记忆目标 ----------

    def remember_target(
        self,
        user_id: int,
        user_name: str = "",
        nick_name: str = "",
        portrait: str = "",
        avatar: str = "",
        forum_level: int | None = None,
        day: int | None = None,
        reason: str | None = None,
    ) -> None:
        key = str(user_id or user_name)
        if not key:
            return
        ts = now_ms()
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT use_count FROM targets WHERE key = ?", (key,)
                ).fetchone()
                if row:
                    conn.execute(
                        """UPDATE targets SET
                             user_id = COALESCE(?, user_id),
                             user_name = ?,
                             nick_name = ?,
                             portrait = ?,
                             avatar = COALESCE(?, avatar),
                             forum_level = COALESCE(?, forum_level),
                             last_used_at = ?,
                             use_count = use_count + 1,
                             last_day = COALESCE(?, last_day),
                             last_reason = COALESCE(?, last_reason)
                           WHERE key = ?""",
                        (
                            user_id,
                            user_name or "",
                            nick_name or "",
                            portrait or "",
                            avatar or None,
                            forum_level,
                            ts,
                            day,
                            reason,
                            key,
                        ),
                    )
                else:
                    conn.execute(
                        """INSERT INTO targets
                             (key, user_id, user_name, nick_name, portrait, avatar,
                              forum_level, last_used_at, use_count, last_day, last_reason)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                        (
                            key,
                            user_id,
                            user_name or "",
                            nick_name or "",
                            portrait or "",
                            avatar or "",
                            forum_level or 0,
                            ts,
                            day,
                            reason,
                        ),
                    )
                conn.commit()
            finally:
                conn.close()

    def list_targets(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._fetch(
            "SELECT * FROM targets ORDER BY last_used_at DESC LIMIT ?", (limit,)
        )

    def last_target(self) -> dict[str, Any] | None:
        rows = self._fetch("SELECT * FROM targets ORDER BY last_used_at DESC LIMIT 1")
        return rows[0] if rows else None

    def delete_target(self, key: str) -> None:
        self._write("DELETE FROM targets WHERE key = ?", (key,))

    # ---------- 日志 ----------

    def add_log(
        self,
        action: str,
        fname: str,
        user_id: int,
        user_name: str,
        nick_name: str,
        avatar: str,
        ok: bool,
        message: str,
        day: int | None = None,
        reason: str = "",
    ) -> None:
        self._write(
            """INSERT INTO logs
                 (ts, action, fname, user_id, user_name, nick_name, avatar, day, reason, ok, message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                now_ms(),
                action,
                fname,
                user_id,
                user_name or "",
                nick_name or "",
                avatar or "",
                day,
                reason or "",
                1 if ok else 0,
                message or "",
            ),
        )

    def list_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._fetch(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)
        )

    def clear_logs(self) -> None:
        self._write("DELETE FROM logs")
