from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from jarvy.core.runtime import build_runtime
from jarvy.scheduler.service import ReminderScheduler
from jarvy.tools.web.monitor import PageMonitorService

agent, memory, jobs, monitors = build_runtime()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    workers = [
        asyncio.create_task(ReminderScheduler(jobs).run()),
        asyncio.create_task(PageMonitorService(monitors).run()),
    ]
    try:
        yield
    finally:
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)


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


class PageWatchRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    interval_seconds: int = Field(default=900, ge=300, le=86400)


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


@app.post("/chat/stream")
async def stream_chat(request: ChatRequest) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()

        def publish(event: dict[str, object]) -> None:
            queue.put_nowait(event)

        task = asyncio.create_task(
            agent.run(request.message, approval=request.approval, event_sink=publish)
        )
        try:
            while not task.done() or not queue.empty():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=12)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
            result = await task
            memory.log_episode(request.message, result.response)
            payload = {
                "type": "result",
                "task_id": result.task_id,
                "response": result.response,
                "steps": result.steps,
                "pending_approval": result.pending_approval,
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
        except asyncio.CancelledError:
            task.cancel()
            raise

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


@app.post("/page-watches")
async def create_page_watch(request: PageWatchRequest) -> dict[str, object]:
    try:
        return monitors.create(request.url, request.interval_seconds)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/page-watches")
async def list_page_watches(include_paused: bool = False) -> dict[str, object]:
    return {"watches": monitors.list(include_paused=include_paused)}


@app.delete("/page-watches/{watch_id}")
async def pause_page_watch(watch_id: str) -> dict[str, object]:
    return {"paused": monitors.pause(watch_id), "id": watch_id}


@app.get("/page-alerts")
async def list_page_alerts(unread_only: bool = True, limit: int = 50) -> dict[str, object]:
    try:
        return {"alerts": monitors.alerts(unread_only, limit)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/page-alerts/{alert_id}/read")
async def mark_page_alert_read(alert_id: str) -> dict[str, object]:
    return {"marked_read": monitors.mark_alert_read(alert_id), "id": alert_id}


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
