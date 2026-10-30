from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from jarvy.core.runtime import build_runtime
from jarvy.scheduler.service import ReminderScheduler

agent, memory, jobs = build_runtime()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    worker = asyncio.create_task(ReminderScheduler(jobs).run())
    try:
        yield
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Jarvy",
    version="0.1.0",
    description="Local-first permission-aware assistant",
    lifespan=lifespan,
)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    approval: str | None = Field(default=None, max_length=1000)


class ReminderRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    run_at: str = Field(min_length=1, max_length=64)


@app.get("/", include_in_schema=False)
async def home() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "jarvy"}


@app.post("/chat")
async def chat(request: ChatRequest) -> dict[str, object]:
    result = await agent.run(request.message, approval=request.approval)
    memory.log_episode(request.message, result.response)
    return {
        "task_id": result.task_id,
        "response": result.response,
        "steps": result.steps,
        "pending_approval": result.pending_approval,
    }


@app.get("/memory")
async def list_memory(q: str = "") -> dict[str, object]:
    return {"facts": memory.recall(q)}


@app.put("/memory/{key}")
async def put_memory(key: str, body: dict[str, str]) -> dict[str, str]:
    memory.remember(key, body.get("value", ""))
    return {"status": "saved", "key": key}


@app.delete("/memory/{key}")
async def delete_memory(key: str) -> dict[str, object]:
    return {"deleted": memory.forget(key), "key": key}


@app.post("/reminders")
async def create_reminder(request: ReminderRequest) -> dict[str, object]:
    try:
        return jobs.create(request.message, request.run_at)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/reminders")
async def list_reminders(status: str = "pending", limit: int = 50) -> dict[str, object]:
    try:
        return {"reminders": jobs.list(status=status, limit=limit)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/reminders/{reminder_id}")
async def cancel_reminder(reminder_id: str) -> dict[str, object]:
    return {"cancelled": jobs.cancel(reminder_id), "id": reminder_id}


@app.get("/notifications")
async def list_notifications(unread_only: bool = True, limit: int = 50) -> dict[str, object]:
    try:
        return {"notifications": jobs.notifications(unread_only=unread_only, limit=limit)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str) -> dict[str, object]:
    return {"marked_read": jobs.mark_read(notification_id), "id": notification_id}
