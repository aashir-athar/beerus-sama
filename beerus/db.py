"""SQLite storage: warnings, member history, settings. Thread-safe, async-friendly.

Privacy: message text is NEVER stored. Only IDs, category, reason label and score.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    severity INTEGER NOT NULL DEFAULT 1,
    reason TEXT NOT NULL,
    score INTEGER NOT NULL DEFAULT 0,
    channel_id INTEGER,
    issued_by INTEGER,            -- NULL = automatic, else moderator ID
    created_at REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_warn_user ON warnings(guild_id, user_id, created_at);

CREATE TABLE IF NOT EXISTS members (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    first_join REAL NOT NULL,
    last_join REAL NOT NULL,
    last_leave REAL,
    join_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._conn: sqlite3.Connection | None = None

    # ── lifecycle ────────────────────────────────────────────
    def exists(self) -> bool:
        return self.path.exists() and self.path.stat().st_size > 0

    def open(self) -> None:
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self._conn.close()
            self._conn = None

    # ── helpers ──────────────────────────────────────────────
    async def _run(self, fn, *args):
        async with self._lock:
            return await asyncio.to_thread(fn, *args)

    def _exec(self, sql: str, params: tuple = ()) -> int:
        assert self._conn
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        return cur.lastrowid or 0

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        assert self._conn
        return self._conn.execute(sql, params).fetchall()

    # ── warnings ─────────────────────────────────────────────
    async def add_warning(self, guild_id: int, user_id: int, category: str, severity: int,
                          reason: str, score: int, channel_id: int | None,
                          issued_by: int | None = None) -> int:
        return await self._run(
            self._exec,
            "INSERT INTO warnings (guild_id,user_id,category,severity,reason,score,channel_id,issued_by,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (guild_id, user_id, category, severity, reason, score, channel_id, issued_by, time.time()),
        )

    async def active_warning_count(self, guild_id: int, user_id: int, expiry_days: float) -> int:
        since = time.time() - expiry_days * 86400
        rows = await self._run(
            self._query,
            "SELECT COUNT(*) c FROM warnings WHERE guild_id=? AND user_id=? AND active=1 AND created_at>=?",
            (guild_id, user_id, since),
        )
        return int(rows[0]["c"])

    async def total_warning_count(self, guild_id: int, user_id: int) -> int:
        rows = await self._run(
            self._query, "SELECT COUNT(*) c FROM warnings WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        return int(rows[0]["c"])

    async def recent_warnings(self, guild_id: int, user_id: int, limit: int = 10) -> list[dict]:
        rows = await self._run(
            self._query,
            "SELECT * FROM warnings WHERE guild_id=? AND user_id=? ORDER BY created_at DESC LIMIT ?",
            (guild_id, user_id, limit),
        )
        return [dict(r) for r in rows]

    async def clear_warnings(self, guild_id: int, user_id: int) -> None:
        await self._run(
            self._exec, "UPDATE warnings SET active=0 WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )

    async def top_offenders(self, guild_id: int, expiry_days: float, limit: int = 10) -> list[dict]:
        since = time.time() - expiry_days * 86400
        rows = await self._run(
            self._query,
            "SELECT user_id, COUNT(*) c FROM warnings WHERE guild_id=? AND active=1 AND created_at>=?"
            " GROUP BY user_id ORDER BY c DESC LIMIT ?",
            (guild_id, since, limit),
        )
        return [dict(r) for r in rows]

    # ── members ──────────────────────────────────────────────
    async def record_join(self, guild_id: int, user_id: int) -> dict:
        """Returns {'returning': bool, 'join_count': int, 'last_leave': float|None, 'first_join': float}."""
        def work() -> dict:
            now = time.time()
            rows = self._query("SELECT * FROM members WHERE guild_id=? AND user_id=?", (guild_id, user_id))
            if rows:
                r = rows[0]
                self._exec(
                    "UPDATE members SET last_join=?, join_count=join_count+1 WHERE guild_id=? AND user_id=?",
                    (now, guild_id, user_id),
                )
                return {"returning": True, "join_count": r["join_count"] + 1,
                        "last_leave": r["last_leave"], "first_join": r["first_join"]}
            self._exec(
                "INSERT INTO members (guild_id,user_id,first_join,last_join) VALUES (?,?,?,?)",
                (guild_id, user_id, now, now),
            )
            return {"returning": False, "join_count": 1, "last_leave": None, "first_join": now}
        return await self._run(work)

    async def record_leave(self, guild_id: int, user_id: int) -> dict | None:
        def work() -> dict | None:
            rows = self._query("SELECT * FROM members WHERE guild_id=? AND user_id=?", (guild_id, user_id))
            self._exec("UPDATE members SET last_leave=? WHERE guild_id=? AND user_id=?",
                       (time.time(), guild_id, user_id))
            return dict(rows[0]) if rows else None
        return await self._run(work)

    # ── settings ─────────────────────────────────────────────
    async def get_setting(self, key: str, default: Any = None) -> Any:
        rows = await self._run(self._query, "SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    async def set_setting(self, key: str, value: Any) -> None:
        await self._run(
            self._exec, "INSERT INTO settings (key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)),
        )

    async def delete_setting(self, key: str) -> None:
        await self._run(self._exec, "DELETE FROM settings WHERE key=?", (key,))

    async def all_settings(self, prefix: str = "") -> dict[str, Any]:
        rows = await self._run(self._query, "SELECT key,value FROM settings WHERE key LIKE ?", (prefix + "%",))
        return {r["key"]: json.loads(r["value"]) for r in rows}

    # ── backup ───────────────────────────────────────────────
    def _backup_to(self, dest: str) -> None:
        assert self._conn
        dst = sqlite3.connect(dest)
        try:
            self._conn.backup(dst)
        finally:
            dst.close()

    async def backup_to(self, dest: Path) -> None:
        await self._run(self._backup_to, str(dest))
