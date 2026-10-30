from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SENSITIVE_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "authorization",
    "cookie",
    "value",
    "text",
    "content",
    "message",
    "input",
    "body",
    "subject",
    "snippet",
    "description",
    "summary",
}
_EXACT_SENSITIVE_KEYS = {"to", "from"}


def _redact(value: Any, key: str = "") -> Any:
    normalized_key = key.lower()
    if normalized_key in _EXACT_SENSITIVE_KEYS or any(
        sensitive in normalized_key for sensitive in _SENSITIVE_KEYS
    ):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): _redact(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


class AuditLog:
    """Append-only JSONL audit trail with restrictive file permissions and secret redaction."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        os.close(descriptor)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def record(self, event: str, **details: Any) -> None:
        item = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            "details": _redact(details),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
