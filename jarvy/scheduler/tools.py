from __future__ import annotations

from typing import Any

from jarvy.scheduler.store import JobStore
from jarvy.tools.registry import RiskTier, Tool, ToolRegistry


def build_scheduler_tools(store: JobStore) -> ToolRegistry:
    registry = ToolRegistry()

    def create_reminder(message: str, run_at: str) -> dict[str, Any]:
        """Create a local reminder with a timezone-aware ISO-8601 run time."""
        return store.create(message, run_at)

    def list_reminders(status: str = "pending") -> dict[str, Any]:
        return {"reminders": store.list(status=status)}

    def cancel_reminder(reminder_id: str) -> dict[str, Any]:
        return {"cancelled": store.cancel(reminder_id), "id": reminder_id}

    registry.register(
        Tool(
            name="create_reminder",
            description=(
                "Schedule a local reminder. run_at must be a timezone-aware ISO-8601 timestamp "
                "(for example 2026-11-01T09:30:00+05:30)."
            ),
            parameters={
                "type": "OBJECT",
                "properties": {
                    "message": {"type": "STRING", "description": "Reminder text."},
                    "run_at": {
                        "type": "STRING",
                        "description": "Timezone-aware ISO-8601 date/time.",
                    },
                },
                "required": ["message", "run_at"],
            },
            risk=RiskTier.T1,
            handler=create_reminder,
        )
    )
    registry.register(
        Tool(
            name="list_reminders",
            description="List local reminders by status: pending, fired, cancelled, or all.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "status": {"type": "STRING", "description": "Status filter (default pending)."}
                },
                "required": [],
            },
            risk=RiskTier.T0,
            handler=list_reminders,
        )
    )
    registry.register(
        Tool(
            name="cancel_reminder",
            description="Cancel a pending local reminder by its id.",
            parameters={
                "type": "OBJECT",
                "properties": {"reminder_id": {"type": "STRING"}},
                "required": ["reminder_id"],
            },
            risk=RiskTier.T1,
            handler=cancel_reminder,
        )
    )
    return registry
