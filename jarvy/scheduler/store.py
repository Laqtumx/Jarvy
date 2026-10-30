from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def _parse_run_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("run_at must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError("run_at must include a timezone, such as Z or +05:30")
    normalized = parsed.astimezone(UTC)
    now = datetime.now(UTC)
    if normalized <= now:
        raise ValueError("Reminder time must be in the future")
    if normalized > now + timedelta(days=365):
        raise ValueError("Reminder time cannot be more than 365 days away")
    return normalized


class JobStore:
    """Persistent one-shot reminder queue and local notification inbox."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    message TEXT NOT NULL,
                    run_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','fired','cancelled')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_due_idx ON jobs(status, run_at);
                CREATE TABLE IF NOT EXISTS notifications (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL UNIQUE,
                    message TEXT NOT NULL,
                    fired_at TEXT NOT NULL,
                    read_at TEXT
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

    def create(self, message: str, run_at: str) -> dict[str, Any]:
        message = message.strip()
        if not message or len(message) > 1000:
            raise ValueError("Reminder message must contain 1 to 1000 characters")
        scheduled = _parse_run_at(run_at)
        job_id = str(uuid.uuid4())
        created = datetime.now(UTC).isoformat()
        with self._connect() as db:
            db.execute(
                "INSERT INTO jobs(id,message,run_at,status,created_at) VALUES (?,?,?,'pending',?)",
                (job_id, message, scheduled.isoformat(), created),
            )
        return {
            "id": job_id,
            "message": message,
            "run_at": scheduled.isoformat(),
            "status": "pending",
        }

    def list(self, status: str = "pending", limit: int = 50) -> list[dict[str, Any]]:
        if status not in {"pending", "fired", "cancelled", "all"}:
            raise ValueError("status must be pending, fired, cancelled, or all")
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        with self._connect() as db:
            if status == "all":
                rows = db.execute(
                    "SELECT id,message,run_at,status,attempts,created_at FROM jobs "
                    "ORDER BY run_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT id,message,run_at,status,attempts,created_at FROM jobs "
                    "WHERE status=? ORDER BY run_at LIMIT ?",
                    (status, limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def cancel(self, job_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE jobs SET status='cancelled' WHERE id=? AND status='pending'", (job_id,)
            )
            return cursor.rowcount == 1

    def fire_due(self, now: datetime | None = None) -> list[dict[str, Any]]:
        current = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        fired_at = datetime.now(UTC).isoformat()
        notifications: list[dict[str, Any]] = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT id,message FROM jobs WHERE status='pending' AND run_at<=? ORDER BY run_at",
                (current,),
            ).fetchall()
            for row in rows:
                notification_id = str(uuid.uuid4())
                db.execute(
                    "UPDATE jobs SET status='fired', attempts=attempts+1 WHERE id=?", (row["id"],)
                )
                db.execute(
                    "INSERT OR IGNORE INTO notifications(id,job_id,message,fired_at) "
                    "VALUES (?,?,?,?)",
                    (notification_id, row["id"], row["message"], fired_at),
                )
                notifications.append(
                    {
                        "id": notification_id,
                        "job_id": row["id"],
                        "message": row["message"],
                        "fired_at": fired_at,
                    }
                )
        return notifications

    def notifications(self, unread_only: bool = True, limit: int = 50) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        with self._connect() as db:
            if unread_only:
                rows = db.execute(
                    "SELECT id,job_id,message,fired_at,read_at FROM notifications "
                    "WHERE read_at IS NULL ORDER BY fired_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT id,job_id,message,fired_at,read_at FROM notifications "
                    "ORDER BY fired_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def mark_read(self, notification_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE notifications SET read_at=? WHERE id=? AND read_at IS NULL",
                (datetime.now(UTC).isoformat(), notification_id),
            )
            return cursor.rowcount == 1
