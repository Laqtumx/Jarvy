from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jarvy.brain.agent import Agent
from jarvy.brain.router import (
    FallbackRouter,
    LocalRouter,
    ModelTurn,
    _openai_messages,
    openai_tool_declarations,
)
from jarvy.memory.store import MemoryStore
from jarvy.memory.tools import build_memory_tools
from jarvy.safety.audit import AuditLog
from jarvy.safety.permissions import PermissionEngine
from jarvy.scheduler.store import JobStore
from jarvy.scheduler.tools import build_scheduler_tools
from jarvy.tools.registry import RiskTier, Tool, ToolRegistry
from jarvy.tools.system.basic import _resolve_allowed, build_system_tools
from jarvy.tools.web.browser import build_browser_tools, validate_public_url
from jarvy.tools.web.search import GeminiGroundedSearch, build_search_tool, parse_grounded_response


class ScriptedRouter:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = iter(turns)

    async def next_turn(self, user_text, history, tools):
        return next(self.turns)


def test_openai_compatible_schema_and_tool_history_conversion():
    declarations = openai_tool_declarations(
        [
            {
                "name": "list_files",
                "description": "List files",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"path": {"type": "STRING"}},
                    "required": ["path"],
                },
            }
        ]
    )
    assert declarations[0]["function"]["parameters"]["type"] == "object"
    assert declarations[0]["function"]["parameters"]["properties"]["path"]["type"] == "string"

    messages = _openai_messages(
        None,
        [
            {"role": "user", "parts": [{"text": "List files."}]},
            {"role": "model", "parts": [{"functionCall": {"name": "list_files", "args": {}}}]},
            {
                "role": "user",
                "parts": [{"functionResponse": {"name": "list_files", "response": {"items": []}}}],
            },
        ],
    )
    assert messages[1]["content"] == "List files."
    assert messages[2]["tool_calls"][0]["function"]["name"] == "list_files"
    assert messages[3]["tool_call_id"] == messages[2]["tool_calls"][0]["id"]


@pytest.mark.asyncio
async def test_fallback_router_degrades_to_local_after_provider_failure():
    class BrokenRouter:
        async def next_turn(self, user_text, history, tools):
            raise RuntimeError("provider unavailable")

    router = FallbackRouter([BrokenRouter()], LocalRouter(cloud_configured=True))
    result = await router.next_turn("What time is it?", [], [])
    assert result.tool_call == {"name": "get_current_time", "arguments": {}}


def test_tool_registry_rejects_duplicate_and_exposes_declarations():
    registry = build_system_tools([Path("/tmp")])
    assert {tool.name for tool in registry.all()} >= {"get_current_time", "read_text_file"}
    assert registry.declarations()[0]["name"] == "get_current_time"
    with pytest.raises(ValueError):
        registry.register(registry.all()[0])


def test_paths_are_confined_to_allowed_directories(tmp_path: Path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    assert _resolve_allowed(str(allowed / "a.txt"), [allowed]) == allowed / "a.txt"
    with pytest.raises(PermissionError):
        _resolve_allowed(str(outside / "secret"), [allowed])


def test_permission_policy_requires_exact_t2_approval_and_denies_t3():
    engine = PermissionEngine()
    consequential = Tool("send", "send", {}, RiskTier.T2, lambda: None)
    token = "APPROVE: send({'to': 'a@example.test'})"
    assert not engine.authorize(consequential, {"to": "a@example.test"}).allowed
    assert engine.authorize(consequential, {"to": "a@example.test"}, token).allowed
    critical = Tool("wipe", "wipe", {}, RiskTier.T3, lambda: None)
    assert not engine.authorize(critical, {}).allowed


def test_memory_upsert_search_and_delete(tmp_path: Path):
    memory = MemoryStore(tmp_path / "memory.db")
    memory.remember("favorite color", "blue")
    memory.remember("favorite color", "green")
    assert memory.recall("color")[0]["value"] == "green"
    assert memory.forget("favorite color")
    assert memory.recall() == []


@pytest.mark.asyncio
async def test_agent_tool_call_is_audited_and_returns_observed_result(tmp_path: Path):
    registry = build_system_tools([tmp_path])
    router = ScriptedRouter(
        [
            ModelTurn(tool_call={"name": "get_current_time", "arguments": {}}),
            ModelTurn(text="It is currently the time reported by the clock tool."),
        ]
    )
    agent = Agent(router, registry, AuditLog(tmp_path / "audit.jsonl"), max_steps=3)
    result = await agent.run("What time is it?")
    assert len(result.steps) == 1
    assert result.steps[0]["status"] == "success"
    assert "clock tool" in result.response
    records = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [record["event"] for record in records] == [
        "task_started",
        "tool_completed",
        "task_finished",
    ]


@pytest.mark.asyncio
async def test_memory_plugins_persist_recall_and_classify_delete_as_t2(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    tools = build_memory_tools(store)
    await tools.get("remember_fact").invoke({"key": "preferred name", "value": "Sam"})
    recalled = await tools.get("recall_memory").invoke({"query": "name"})
    assert recalled["facts"][0]["value"] == "Sam"
    assert tools.get("remember_fact").risk is RiskTier.T1
    assert tools.get("recall_memory").risk is RiskTier.T0
    assert tools.get("forget_memory").risk is RiskTier.T2


@pytest.mark.asyncio
async def test_local_router_executes_clock_demo_without_api_key(tmp_path: Path):
    agent = Agent(
        LocalRouter(),
        build_system_tools([tmp_path]),
        AuditLog(tmp_path / "audit.jsonl"),
    )
    result = await agent.run("What time is it?")
    assert len(result.steps) == 1
    assert "current local time" in result.response


@pytest.mark.asyncio
async def test_agent_requires_exact_approval_for_t2_and_denies_t3(tmp_path: Path):
    from jarvy.tools.registry import Tool

    registry = ToolRegistry()
    registry.register(Tool("write", "write", {}, RiskTier.T2, lambda: {"ok": True}))
    router = ScriptedRouter(
        [
            ModelTurn(tool_call={"name": "write", "arguments": {}}),
            ModelTurn(tool_call={"name": "write", "arguments": {}}),
            ModelTurn(text="done"),
        ]
    )
    agent = Agent(router, registry, AuditLog(tmp_path / "audit.jsonl"))
    blocked = await agent.run("write something")
    assert blocked.pending_approval == "APPROVE: write({})"
    assert not blocked.steps
    approved = await agent.run("write something", approval=blocked.pending_approval)
    assert approved.steps[0]["status"] == "success"

    critical = Tool("critical", "critical", {}, RiskTier.T3, lambda: None)
    decision = PermissionEngine().authorize(critical, {})
    assert not decision.allowed and not decision.requires_confirmation


def test_grounded_search_returns_only_http_citations():
    result = parse_grounded_response(
        {
            "candidates": [
                {
                    "content": {"parts": [{"text": "A short research summary."}]},
                    "groundingMetadata": {
                        "webSearchQueries": ["Jarvy project"],
                        "groundingChunks": [
                            {"web": {"title": "Example", "uri": "https://example.com/page"}},
                            {"web": {"title": "Bad", "uri": "javascript:alert(1)"}},
                            {"web": {"title": "Duplicate", "uri": "https://example.com/page"}},
                        ],
                    },
                }
            ]
        }
    )
    assert result["summary"] == "A short research summary."
    assert result["sources"] == [{"title": "Example", "url": "https://example.com/page"}]
    assert result["grounded"] is True


def test_search_plugin_is_read_only_and_rejects_empty_query():
    tool = build_search_tool("test-key", "test-model")
    assert tool.name == "web_search" and tool.risk is RiskTier.T0
    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(GeminiGroundedSearch("key", "model").search("  "))


def test_persistent_reminder_queue_fires_once_and_can_be_cancelled(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.db")
    run_at = datetime.now(UTC) + timedelta(minutes=5)
    reminder = store.create("Take a break", run_at.isoformat())
    assert reminder["status"] == "pending"
    assert store.list()[0]["id"] == reminder["id"]
    fired = store.fire_due(now=run_at + timedelta(seconds=1))
    assert len(fired) == 1 and fired[0]["message"] == "Take a break"
    assert store.fire_due(now=run_at + timedelta(seconds=2)) == []
    assert store.notifications()[0]["message"] == "Take a break"
    assert store.mark_read(fired[0]["id"])
    assert store.notifications() == []

    cancellable = store.create(
        "Cancel this", (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    )
    assert store.cancel(cancellable["id"])
    assert store.list(status="cancelled")[0]["id"] == cancellable["id"]


def test_reminder_tools_have_expected_risk_tiers(tmp_path: Path):
    tools = build_scheduler_tools(JobStore(tmp_path / "jobs.db"))
    assert tools.get("create_reminder").risk is RiskTier.T1
    assert tools.get("list_reminders").risk is RiskTier.T0
    assert tools.get("cancel_reminder").risk is RiskTier.T1


def test_reminder_requires_timezone_and_future_time(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.db")
    with pytest.raises(ValueError, match="timezone"):
        store.create("No timezone", "2030-01-01T12:00:00")
    with pytest.raises(ValueError, match="future"):
        store.create("Past", "2000-01-01T12:00:00Z")


def test_browser_url_guard_blocks_local_network_and_non_http_targets():
    assert validate_public_url("https://8.8.8.8/") == "https://8.8.8.8/"
    for url in (
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
        "https://user:password@example.com/",
        "http://service.local/",
    ):
        with pytest.raises((ValueError, PermissionError)):
            validate_public_url(url)


def test_browser_tools_assign_read_only_and_confirmed_risks(tmp_path: Path):
    tools = build_browser_tools(tmp_path / "profile")
    assert tools.get("web_open").risk is RiskTier.T0
    assert tools.get("web_click").risk is RiskTier.T2


def test_read_file_tool_reads_only_allowed_file(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    tool = build_system_tools([tmp_path]).get("read_text_file")
    import asyncio

    result = asyncio.run(tool.invoke({"path": str(tmp_path / "notes.txt")}))
    assert result["text"] == "hello"
