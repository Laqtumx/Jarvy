from __future__ import annotations

from jarvy.brain.agent import Agent
from jarvy.brain.router import (
    FallbackRouter,
    GeminiRouter,
    LocalRouter,
    OpenAICompatibleRouter,
)
from jarvy.core.config import Settings, get_settings
from jarvy.memory.store import MemoryStore
from jarvy.memory.tools import build_memory_tools
from jarvy.safety.audit import AuditLog
from jarvy.scheduler.store import JobStore
from jarvy.scheduler.tools import build_scheduler_tools
from jarvy.tools.system.basic import build_system_tools
from jarvy.tools.web.browser import build_browser_tools, playwright_available
from jarvy.tools.web.monitor import MonitorStore, build_monitor_tools
from jarvy.tools.web.search import build_search_tool


def build_runtime(
    settings: Settings | None = None,
) -> tuple[Agent, MemoryStore, JobStore, MonitorStore]:
    config = settings or get_settings()
    config.prepare()
    cloud_routers = []
    if config.gemini_api_key:
        cloud_routers.append(
            GeminiRouter(config.gemini_api_key, config.gemini_model, config.request_timeout_seconds)
        )
    if config.groq_api_key:
        cloud_routers.append(
            OpenAICompatibleRouter(
                config.groq_api_key,
                config.groq_model,
                config.groq_base_url,
                config.request_timeout_seconds,
            )
        )
    router = (
        FallbackRouter(cloud_routers, LocalRouter(cloud_configured=True))
        if cloud_routers
        else LocalRouter()
    )
    memory = MemoryStore(config.data_dir / "memory.sqlite3")
    jobs = JobStore(config.data_dir / "scheduler.sqlite3")
    monitors = MonitorStore(config.data_dir / "monitors.sqlite3")
    tools = build_system_tools(config.allowed_dirs)
    for tool in build_memory_tools(memory).all():
        tools.register(tool)
    for tool in build_scheduler_tools(jobs).all():
        tools.register(tool)
    for tool in build_monitor_tools(monitors).all():
        tools.register(tool)
    if playwright_available():
        for tool in build_browser_tools(
            config.data_dir / "browser-profile", config.download_dir
        ).all():
            tools.register(tool)
    if config.gemini_api_key:
        tools.register(
            build_search_tool(
                config.gemini_api_key,
                config.gemini_model,
                config.request_timeout_seconds,
            )
        )
    agent = Agent(
        router=router,
        tools=tools,
        audit=AuditLog(config.data_dir / "audit.jsonl"),
        max_steps=config.max_steps,
        dry_run=config.dry_run,
    )
    return agent, memory, jobs, monitors
