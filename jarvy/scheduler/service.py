from __future__ import annotations

import asyncio
import logging

from jarvy.scheduler.store import JobStore

logger = logging.getLogger(__name__)


class ReminderScheduler:
    def __init__(self, store: JobStore, poll_interval: float = 1.0) -> None:
        self.store = store
        self.poll_interval = poll_interval

    async def run(self) -> None:
        while True:
            try:
                fired = await asyncio.to_thread(self.store.fire_due)
                for reminder in fired:
                    logger.info("Reminder fired", extra={"reminder_id": reminder["id"]})
            except Exception:
                logger.exception("Reminder scheduler iteration failed")
            await asyncio.sleep(self.poll_interval)
