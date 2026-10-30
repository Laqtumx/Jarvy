from __future__ import annotations

import asyncio
import hashlib
import html
import ipaddress
import re
import socket
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from jarvy.tools.registry import RiskTier, Tool, ToolRegistry


def validate_monitor_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute http:// and https:// URLs can be monitored")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials cannot be monitored")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise PermissionError("Local and internal hostnames cannot be monitored")
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            records = socket.getaddrinfo(
                host, parsed.port or (443 if parsed.scheme == "https" else 80)
            )
        except OSError as exc:
            raise PermissionError("Could not safely resolve the monitor host") from exc
        addresses = {ipaddress.ip_address(record[4][0]) for record in records}
    if not addresses or any(not address.is_global for address in addresses):
        raise PermissionError("Private, reserved, and non-public network addresses are blocked")
    return parsed.geturl()


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.parts: list[str] = []
        self.title = ""
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data
        if not self.skip_depth:
            normalized = " ".join(data.split())
            if normalized:
                self.parts.append(normalized)


def extract_page_text(body: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(body)
    text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()[:200_000]
    title = html.unescape(" ".join(parser.title.split()))[:300]
    return title, text


async def fetch_public_page(url: str, timeout_seconds: float = 20.0) -> tuple[str, str]:
    current = await asyncio.to_thread(validate_monitor_url, url)
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
        for redirect_count in range(6):
            current = await asyncio.to_thread(validate_monitor_url, current)
            async with client.stream(
                "GET", current, headers={"User-Agent": "JarvyMonitor/0.1"}
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location or redirect_count == 5:
                        raise RuntimeError("Monitor redirect limit exceeded")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if not any(
                    kind in content_type
                    for kind in ("text/html", "text/plain", "application/xhtml")
                ):
                    raise ValueError("Page monitor accepts HTML or plain-text responses only")
                declared_length = response.headers.get("content-length")
                if declared_length and int(declared_length) > 2_000_000:
                    raise ValueError("Monitored page exceeds the 2 MiB response limit")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes(32 * 1024):
                    size += len(chunk)
                    if size > 2_000_000:
                        raise ValueError("Monitored page exceeds the 2 MiB response limit")
                    chunks.append(chunk)
                body = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                title, text = extract_page_text(body)
                return title, text
    raise RuntimeError("Too many page monitor redirects")


class MonitorStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS watches (
                    id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    interval_seconds INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','paused')),
                    next_check TEXT NOT NULL,
                    content_hash TEXT,
                    title TEXT NOT NULL DEFAULT '',
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    checked_at TEXT
                );
                CREATE INDEX IF NOT EXISTS watches_due_idx ON watches(status,next_check);
                CREATE TABLE IF NOT EXISTS alerts (
                    id TEXT PRIMARY KEY,
                    watch_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL,
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

    def create(self, url: str, interval_seconds: int = 900) -> dict[str, Any]:
        safe_url = validate_monitor_url(url)
        if not 300 <= interval_seconds <= 86_400:
            raise ValueError("Monitor interval must be between 300 seconds and 24 hours")
        with self._connect() as db:
            count = db.execute("SELECT COUNT(*) FROM watches WHERE status='active'").fetchone()[0]
            if count >= 25:
                raise ValueError("At most 25 page monitors may be active")
            watch_id = str(uuid.uuid4())
            now = datetime.now(UTC)
            db.execute(
                "INSERT INTO watches(id,url,interval_seconds,status,next_check,created_at) "
                "VALUES(?,?,?,'active',?,?)",
                (watch_id, safe_url, interval_seconds, now.isoformat(), now.isoformat()),
            )
        return {
            "id": watch_id,
            "url": safe_url,
            "interval_seconds": interval_seconds,
            "status": "active",
        }

    def list(self, include_paused: bool = False) -> list[dict[str, Any]]:
        with self._connect() as db:
            if include_paused:
                rows = db.execute(
                    "SELECT id,url,interval_seconds,status,next_check,title,last_error,created_at,"
                    "checked_at FROM watches ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT id,url,interval_seconds,status,next_check,title,last_error,created_at,"
                    "checked_at FROM watches WHERE status='active' ORDER BY created_at DESC"
                ).fetchall()
        return [dict(row) for row in rows]

    def pause(self, watch_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE watches SET status='paused' WHERE id=? AND status='active'", (watch_id,)
            )
            return cursor.rowcount == 1

    def due(self, now: datetime | None = None) -> list[dict[str, Any]]:
        current = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM watches WHERE status='active' AND next_check<=? "
                "ORDER BY next_check LIMIT 10",
                (current,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_success(self, watch: dict[str, Any], title: str, text: str) -> bool:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        now = datetime.now(UTC)
        next_check = (now + timedelta(seconds=watch["interval_seconds"])).isoformat()
        changed = bool(watch["content_hash"] and watch["content_hash"] != digest)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if changed:
                before = watch.get("title") or watch["url"]
                after = title or watch["url"]
                summary = f"Content changed on {after} (was {before})."
                db.execute(
                    "INSERT INTO alerts(id,watch_id,url,title,summary,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (str(uuid.uuid4()), watch["id"], watch["url"], after, summary, now.isoformat()),
                )
            db.execute(
                "UPDATE watches SET content_hash=?,title=?,last_error=NULL,checked_at=?,"
                "next_check=? "
                "WHERE id=? AND status='active'",
                (digest, title[:300], now.isoformat(), next_check, watch["id"]),
            )
        return changed

    def record_error(self, watch: dict[str, Any], error: str) -> None:
        now = datetime.now(UTC)
        delay = min(max(300, watch["interval_seconds"]), 3600)
        next_check = (now + timedelta(seconds=delay)).isoformat()
        with self._connect() as db:
            db.execute(
                "UPDATE watches SET last_error=?,checked_at=?,next_check=? "
                "WHERE id=? AND status='active'",
                (error[:500], now.isoformat(), next_check, watch["id"]),
            )

    def alerts(self, unread_only: bool = True, limit: int = 50) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        clause = "WHERE read_at IS NULL" if unread_only else ""
        with self._connect() as db:
            rows = db.execute(
                f"SELECT id,watch_id,url,title,summary,created_at,read_at FROM alerts {clause} "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_alert_read(self, alert_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE alerts SET read_at=? WHERE id=? AND read_at IS NULL",
                (datetime.now(UTC).isoformat(), alert_id),
            )
            return cursor.rowcount == 1


class PageMonitorService:
    def __init__(self, store: MonitorStore, poll_interval: float = 10.0) -> None:
        self.store = store
        self.poll_interval = poll_interval

    async def run(self) -> None:
        while True:
            due = await asyncio.to_thread(self.store.due)
            for watch in due:
                try:
                    title, text = await fetch_public_page(watch["url"])
                    await asyncio.to_thread(self.store.record_success, watch, title, text)
                except Exception as exc:
                    await asyncio.to_thread(self.store.record_error, watch, str(exc))
            await asyncio.sleep(self.poll_interval)


def build_monitor_tools(store: MonitorStore) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="watch_page",
            description=(
                "Monitor a public web page for text changes every 5 minutes to 24 hours. "
                "Alerts appear in Jarvy's local notification inbox."
            ),
            parameters={
                "type": "OBJECT",
                "properties": {
                    "url": {"type": "STRING"},
                    "interval_seconds": {
                        "type": "INTEGER",
                        "description": "300 to 86400 seconds; defaults to 900.",
                    },
                },
                "required": ["url"],
            },
            risk=RiskTier.T1,
            handler=store.create,
        )
    )
    registry.register(
        Tool(
            name="list_page_watches",
            description="List active public-page monitors and their last check state.",
            parameters={"type": "OBJECT", "properties": {}, "required": []},
            risk=RiskTier.T0,
            handler=lambda: {"watches": store.list()},
        )
    )
    registry.register(
        Tool(
            name="pause_page_watch",
            description="Pause a recurring page monitor by its id.",
            parameters={
                "type": "OBJECT",
                "properties": {"watch_id": {"type": "STRING"}},
                "required": ["watch_id"],
            },
            risk=RiskTier.T1,
            handler=lambda watch_id: {"paused": store.pause(watch_id), "id": watch_id},
        )
    )
    return registry
