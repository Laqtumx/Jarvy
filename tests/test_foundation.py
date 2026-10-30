from __future__ import annotations

import hashlib
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
from jarvy.tools.integrations.google_auth import GoogleCredentialStore
from jarvy.tools.integrations.google_workspace import (
    GoogleWorkspace,
    _aware_datetime,
    _extract_message_text,
    build_google_tools,
    build_raw_email,
)
from jarvy.tools.registry import RiskTier, Tool, ToolRegistry
from jarvy.tools.system.basic import _resolve_allowed, build_system_tools
from jarvy.tools.web.browser import (
    build_browser_tools,
    safe_download_filename,
    validate_public_url,
)
from jarvy.tools.web.monitor import (
    MonitorStore,
    build_monitor_tools,
    extract_page_text,
    validate_monitor_url,
)
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


def test_google_workspace_email_serialization_and_untrusted_read_extraction():
    raw = build_raw_email("person@example.com", "Notes", "Hello, world")
    import base64
    from email import message_from_bytes

    padded = raw + "=" * (-len(raw) % 4)
    message = message_from_bytes(base64.urlsafe_b64decode(padded))
    assert message["To"] == "person@example.com"
    assert message["Subject"] == "Notes"
    assert message.get_payload(decode=True).decode().strip() == "Hello, world"
    with pytest.raises(ValueError):
        build_raw_email("bad recipient", "Subject", "Body")
    with pytest.raises(ValueError):
        build_raw_email("person@example.com", "bad\nsubject", "Body")

    body = base64.urlsafe_b64encode(b"Email text with untrusted instructions").decode().rstrip("=")
    extracted = _extract_message_text(
        {
            "mimeType": "multipart/alternative",
            "parts": [{"mimeType": "text/plain", "body": {"data": body}}],
        }
    )
    assert extracted == "Email text with untrusted instructions"


def test_google_tool_risk_tiers_and_timezone_validation():
    tools = build_google_tools(GoogleCredentialStore())
    assert tools.get("gmail_search").risk is RiskTier.T0
    assert tools.get("gmail_draft").risk is RiskTier.T1
    assert tools.get("gmail_send").risk is RiskTier.T2
    assert tools.get("calendar_create_event").risk is RiskTier.T2
    assert tools.get("drive_search").risk is RiskTier.T0
    assert tools.get("drive_create_text").risk is RiskTier.T1
    assert _aware_datetime("2026-10-30T10:00:00+05:30", "start").tzinfo is not None
    with pytest.raises(ValueError, match="timezone"):
        _aware_datetime("2026-10-30T10:00:00", "start")


@pytest.mark.asyncio
async def test_google_drive_create_and_read_use_mocked_google_api():
    import httpx

    class FakeCredentials:
        async def access_token(self):
            return "test-access-token"

    requests = []

    def create_handler(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "file_123", "name": "Notes.txt"})

    workspace = GoogleWorkspace(FakeCredentials(), transport=httpx.MockTransport(create_handler))
    created = await workspace.drive_create_text("Notes.txt", "local file content")
    assert created["id"] == "file_123"
    assert "local file content" in requests[0].content.decode()
    assert requests[0].headers["authorization"] == "Bearer test-access-token"
    assert "multipart/related" in requests[0].headers["content-type"]

    def read_handler(request):
        if request.url.path.endswith("/files/file_123"):
            return httpx.Response(
                200,
                json={
                    "id": "file_123",
                    "name": "Notes",
                    "mimeType": "application/vnd.google-apps.document",
                },
            )
        return httpx.Response(200, content=b"Exported text")

    reader = GoogleWorkspace(FakeCredentials(), transport=httpx.MockTransport(read_handler))
    file_data = await reader.drive_read_text("file_123")
    assert file_data["text"] == "Exported text"
    with pytest.raises(ValueError, match="Invalid Google Drive file id"):
        await reader.drive_read_text("../private")


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
async def test_stream_endpoint_emits_live_agent_events_and_final_result(
    tmp_path: Path, monkeypatch
):
    import httpx

    from jarvy.interface import api

    monkeypatch.setattr(
        api,
        "agent",
        Agent(
            LocalRouter(),
            build_system_tools([tmp_path]),
            AuditLog(tmp_path / "stream-audit.jsonl"),
        ),
    )
    monkeypatch.setattr(api, "memory", MemoryStore(tmp_path / "stream-memory.db"))
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://jarvy.test") as client:
        response = await client.post("/chat/stream", json={"message": "What time is it?"})
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert {event["type"] for event in events} >= {
        "task_started",
        "planning",
        "tool_started",
        "tool_completed",
        "result",
    }
    assert events[-1]["response"].lower().startswith("the current local time")


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


def test_monitor_text_extractor_ignores_scripts_and_tracks_title():
    title, text = extract_page_text(
        "<html><title>Release Notes</title><body>Version 1.2"
        "<script>secret()</script>Stable</body></html>"
    )
    assert title == "Release Notes"
    assert text == "Release Notes Version 1.2 Stable"


def test_monitor_store_baselines_then_alerts_on_changed_page(tmp_path: Path):
    store = MonitorStore(tmp_path / "monitors.db")
    watch = store.create("https://8.8.8.8/news", interval_seconds=300)
    due = store.due()
    assert len(due) == 1
    assert store.record_success(due[0], "News", "First version") is False
    baseline = dict(due[0], content_hash=hashlib.sha256(b"First version").hexdigest(), title="News")
    assert store.record_success(baseline, "News", "Second version") is True
    alerts = store.alerts()
    assert len(alerts) == 1 and alerts[0]["url"] == watch["url"]
    assert store.mark_alert_read(alerts[0]["id"])
    assert store.alerts() == []


def test_monitor_tools_and_url_guard(tmp_path: Path):
    store = MonitorStore(tmp_path / "monitors.db")
    tools = build_monitor_tools(store)
    assert tools.get("watch_page").risk is RiskTier.T1
    assert tools.get("list_page_watches").risk is RiskTier.T0
    assert validate_monitor_url("https://8.8.8.8/news") == "https://8.8.8.8/news"
    with pytest.raises(PermissionError):
        validate_monitor_url("http://127.0.0.1/private")


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
    assert tools.get("web_fill").risk is RiskTier.T2
    assert tools.get("web_download").risk is RiskTier.T1


def test_download_filename_is_a_safe_basename():
    assert safe_download_filename("../../secret.txt") == "secret.txt"
    assert safe_download_filename("..\\\\..\\\\unsafe.exe") == "unsafe.exe"


def test_audit_redacts_user_content_and_form_values(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    audit = AuditLog(path)
    audit.record("tool", input="private prompt", arguments={"value": "secret form data"})
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["details"]["input"] == "[REDACTED]"
    assert record["details"]["arguments"]["value"] == "[REDACTED]"


def test_read_file_tool_reads_only_allowed_file(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    tool = build_system_tools([tmp_path]).get("read_text_file")
    import asyncio

    result = asyncio.run(tool.invoke({"path": str(tmp_path / "notes.txt")}))
    assert result["text"] == "hello"
