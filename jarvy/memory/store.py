from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class MemoryStore:
    """Local SQLite profile and episodic memory; semantic embeddings are a later adapter."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY,
                    key TEXT NOT NULL UNIQUE,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS episodes (
                    id INTEGER PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    request TEXT NOT NULL,
                    outcome TEXT NOT NULL
                );
            """)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def remember(self, key: str, value: str) -> None:
        key, value = key.strip(), value.strip()
        if not key or not value:
            raise ValueError("Memory key and value must not be empty")
        if len(key) > 200 or len(value) > 4000:
            raise ValueError("Memory entry is too long")
        with self._connect() as db:
            db.execute(
                "INSERT INTO facts(key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (key, value, datetime.now(UTC).isoformat()),
            )

    def recall(self, query: str = "") -> list[dict[str, Any]]:
        with self._connect() as db:
            if query.strip():
                pattern = f"%{query.strip()}%"
                rows = db.execute(
                    "SELECT key, value, updated_at FROM facts WHERE key LIKE ? OR value LIKE ? "
                    "ORDER BY updated_at DESC",
                    (pattern, pattern),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT key, value, updated_at FROM facts ORDER BY updated_at DESC"
                ).fetchall()
        return [dict(row) for row in rows]

    def forget(self, key: str) -> bool:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM facts WHERE key = ?", (key,))
            return cursor.rowcount > 0

    def log_episode(self, request: str, outcome: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO episodes(occurred_at, request, outcome) VALUES (?, ?, ?)",
                (datetime.now(UTC).isoformat(), request, outcome),
            )

    def recent_episodes(self, limit: int = 20) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        with self._connect() as db:
            rows = db.execute(
                "SELECT occurred_at, request, outcome FROM episodes ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
