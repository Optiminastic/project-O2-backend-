"""Ask O2 plugins (remote MCP servers) and saved commands. A fake MCP server stands in for real plugins."""

import json

import httpx
import pytest

from app.assistant import mcp_client, plugins, runner, secrets
from app.assistant.llm import LLMReply
from app.assistant.mcp_client import Connection, PluginError
from app.models import AssistantAction, AssistantCommand, AssistantConversation, AssistantPlugin
from tests.conftest import auth_header

PLUGIN_URL = "https://mcp.example.com/mcp"
KEY = "sk-test-secret"
TOOLS = [
    {"name": "list_payments", "description": "List payments", "inputSchema": {"type": "object", "properties": {"count": {"type": "integer"}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "create.refund", "description": "Refund a payment", "inputSchema": {"$schema": "x", "type": "object", "properties": {"payment_id": {"type": "string"}}}},
]


class FakeServer:
    """A Streamable HTTP MCP server: initialize, tools/list and tools/call."""

    def __init__(self, sse=False, require_key=True):
        self.sse, self.require_key = sse, require_key
        self.calls: list[tuple[str, dict]] = []
        self.session_headers: list[str | None] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.require_key and request.headers.get("authorization") != f"Bearer {KEY}":
            return httpx.Response(401)
        body = json.loads(request.content)
        self.session_headers.append(request.headers.get("mcp-session-id"))
        if "id" not in body:
            return httpx.Response(202)
        method = body["method"]
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake"}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        else:
            self.calls.append((body["params"]["name"], body["params"]["arguments"]))
            result = {"content": [{"type": "text", "text": f"ran {body['params']['name']}"}]}
        reply = {"jsonrpc": "2.0", "id": body["id"], "result": result}
        headers = {"mcp-session-id": "s-1"}
        if self.sse:
            text = 'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
            text += f"event: message\ndata: {json.dumps(reply)}\n\n"
            return httpx.Response(200, text=text, headers={**headers, "content-type": "text/event-stream"})
        return httpx.Response(200, json=reply, headers=headers)


@pytest.fixture
def server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr(mcp_client, "_transport", httpx.MockTransport(fake))
    monkeypatch.setattr(plugins, "_resolve", lambda host: ["93.184.216.34"])
    return fake


@pytest.fixture
def plugin(db, server):
    row = AssistantPlugin(name="Payments")
    plugins.connect(row, PLUGIN_URL, {"Authorization": f"Bearer {KEY}"})
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def conv(db, users):
    row = AssistantConversation(user_id=users["ceo"].id, title="New chat")
    db.add(row)
    db.commit()
    return row


class FakeModel:
    def __init__(self, *replies):
        self.replies, self.seen, self.tools = list(replies), [], []

    def __call__(self, messages, tools):
        self.seen.append(messages)
        self.tools.append(tools)
        return self.replies.pop(0)


def call(name, args):
    return LLMReply(text="", tool_calls=[{"id": "c1", "name": name, "arguments": json.dumps(args)}])


# ---------- secrets ----------


def test_connection_details_are_encrypted_and_tied_to_the_secret_key(monkeypatch):
    token = secrets.seal({"url": PLUGIN_URL, "headers": {"Authorization": KEY}})
    assert KEY not in token and "example.com" not in token
    assert secrets.unseal(token)["headers"]["Authorization"] == KEY
    monkeypatch.setattr(secrets.settings, "secret_key", "a-different-key")
    with pytest.raises(secrets.SecretUnreadable):
        secrets.unseal(token)


# ---------- URL safety ----------


@pytest.mark.parametrize("url", ["http://mcp.example.com/mcp", "ftp://x", "https://"])
def test_only_https_urls_are_accepted(url):
    with pytest.raises(PluginError):
        plugins.check_url(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.2", "::1"])
def test_private_addresses_are_refused(monkeypatch, address):
    monkeypatch.setattr(plugins, "_resolve", lambda host: [address])
    with pytest.raises(PluginError, match="public internet"):
        plugins.check_url("https://internal.example.com/mcp")


def test_a_public_address_is_accepted(monkeypatch):
    monkeypatch.setattr(plugins, "_resolve", lambda host: ["93.184.216.34"])
    assert plugins.check_url(PLUGIN_URL) == "mcp.example.com"


def test_auth_headers_are_built_per_type():
    assert plugins.auth_headers({"type": "bearer", "token": "t"}) == {"Authorization": "Bearer t"}
    assert plugins.auth_headers({"type": "basic", "username": "id", "password": "sec"}) == {"Authorization": "Basic aWQ6c2Vj"}
    assert plugins.auth_headers({"type": "header", "header_name": "X-Key", "header_value": "v"}) == {"X-Key": "v"}
    assert plugins.auth_headers({"type": "url"}) == {}


# ---------- MCP client ----------


@pytest.mark.parametrize("sse", [False, True])
def test_tools_are_listed_over_json_and_event_streams(monkeypatch, sse):
    fake = FakeServer(sse=sse)
    monkeypatch.setattr(mcp_client, "_transport", httpx.MockTransport(fake))
    tools = mcp_client.list_tools(Connection(PLUGIN_URL, {"Authorization": f"Bearer {KEY}"}))
    assert [t.name for t in tools] == ["list_payments", "create.refund"]
    assert tools[0].read_only and not tools[1].read_only
    # The session id from initialize is sent on every later request.
    assert fake.session_headers[0] is None and set(fake.session_headers[1:]) == {"s-1"}


def test_a_refused_key_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(mcp_client, "_transport", httpx.MockTransport(FakeServer()))
    with pytest.raises(PluginError, match="refused the key"):
        mcp_client.list_tools(Connection(PLUGIN_URL, {"Authorization": "Bearer wrong"}))


def test_an_unreachable_server_is_a_clear_error(monkeypatch):
    def down(request):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(mcp_client, "_transport", httpx.MockTransport(down))
    with pytest.raises(PluginError, match="Could not reach"):
        mcp_client.list_tools(Connection(PLUGIN_URL))


# ---------- the agent ----------


def test_plugin_tools_join_the_agents_tools_with_safe_names(db, plugin):
    schemas = {s["function"]["name"]: s["function"] for s in plugins.tool_schemas(db)}
    refund = schemas[f"plugin{plugin.id}__create_refund"]
    assert refund["description"].startswith("[Payments plugin]")
    assert "$schema" not in refund["parameters"] and refund["parameters"]["type"] == "object"
    plugin.enabled = False
    db.commit()
    assert plugins.tool_schemas(db) == []


def test_a_read_only_plugin_tool_runs_at_once(db, users, conv, plugin, server, monkeypatch):
    fake = FakeModel(call(f"plugin{plugin.id}__list_payments", {"count": 2}), LLMReply(text="Two payments."))
    monkeypatch.setattr(runner.llm, "chat", fake)
    runner.handle_message(db, users["ceo"], conv, "Show payments", [])
    assert server.calls == [("list_payments", {"count": 2})]
    assert "ran list_payments" in fake.seen[1][-1]["content"]
    assert any(t["function"]["name"].startswith("plugin") for t in fake.tools[0])
    assert db.query(AssistantAction).count() == 0


def test_a_plugin_change_waits_for_confirm_and_runs_once(db, users, conv, plugin, server, monkeypatch):
    fake = FakeModel(call(f"plugin{plugin.id}__create_refund", {"payment_id": "pay_1"}), LLMReply(text="Refunded."))
    monkeypatch.setattr(runner.llm, "chat", fake)
    runner.handle_message(db, users["ceo"], conv, "Refund pay_1", [])
    action = db.query(AssistantAction).one()
    assert action.status == "pending" and server.calls == []
    assert action.summary == "Create.refund (Payments)"
    assert {"label": "Payment id", "value": "pay_1"} in action.fields
    runner.confirm_action(db, users["ceo"], action.id)
    db.refresh(action)
    assert action.status == "done" and server.calls == [("create.refund", {"payment_id": "pay_1"})]


def test_a_change_for_a_removed_plugin_fails_safely(db, users, conv, plugin, server, monkeypatch):
    fake = FakeModel(call(f"plugin{plugin.id}__create_refund", {"payment_id": "pay_1"}), LLMReply(text="It failed."))
    monkeypatch.setattr(runner.llm, "chat", fake)
    runner.handle_message(db, users["ceo"], conv, "Refund pay_1", [])
    action = db.query(AssistantAction).one()
    db.delete(db.get(AssistantPlugin, plugin.id))
    db.commit()
    runner.confirm_action(db, users["ceo"], action.id)
    db.refresh(action)
    assert action.status == "failed" and "removed" in action.error and server.calls == []


# ---------- API ----------


def _add(client, user, **auth):
    body = {"name": "Payments", "url": PLUGIN_URL, "auth": auth or {"type": "bearer", "token": KEY}}
    return client.post("/api/assistant/plugins", json=body, headers=auth_header(user))


def test_the_ceo_adds_a_plugin_and_its_key_is_never_returned(client, users, server, db):
    res = _add(client, users["ceo"])
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["host"] == "mcp.example.com" and len(body["tools"]) == 2
    listed = client.get("/api/assistant/plugins", headers=auth_header(users["cfo"]))
    text = listed.text
    assert listed.status_code == 200 and KEY not in text and PLUGIN_URL not in text
    assert {d["key"] for d in listed.json()["directory"]} >= {"razorpay", "stripe", "zapier"}
    assert KEY not in db.query(AssistantPlugin).one().connection


def test_only_the_ceo_manages_plugins(client, users, server, plugin):
    assert _add(client, users["cfo"]).status_code == 403
    for method, path in (("patch", f"/{plugin.id}"), ("delete", f"/{plugin.id}"), ("post", f"/{plugin.id}/refresh")):
        kwargs = {"json": {"enabled": False}} if method == "patch" else {}
        res = getattr(client, method)(f"/api/assistant/plugins{path}", headers=auth_header(users["cfo"]), **kwargs)
        assert res.status_code == 403
    assert client.get("/api/assistant/plugins", headers=auth_header(users["manager"])).status_code == 403


def test_a_bad_key_is_reported_and_nothing_is_saved(client, users, server, db):
    res = _add(client, users["ceo"], type="bearer", token="wrong")
    assert res.status_code == 422 and "refused the key" in res.json()["detail"]
    assert db.query(AssistantPlugin).count() == 0


def test_a_missing_key_is_rejected_before_connecting(client, users, server):
    assert _add(client, users["ceo"], type="basic", username="id").status_code == 422


def test_turning_off_and_removing_a_plugin(client, users, plugin, db):
    res = client.patch(f"/api/assistant/plugins/{plugin.id}", json={"enabled": False}, headers=auth_header(users["ceo"]))
    assert res.status_code == 200 and res.json()["enabled"] is False
    assert client.delete(f"/api/assistant/plugins/{plugin.id}", headers=auth_header(users["ceo"])).status_code == 204
    assert db.query(AssistantPlugin).count() == 0


# ---------- saved commands ----------


def test_saved_commands(client, users, db):
    ceo, cfo = auth_header(users["ceo"]), auth_header(users["cfo"])
    body = {"name": "/Weekly", "description": "Weekly review", "prompt": "Summarise this week's money in and out."}
    res = client.post("/api/assistant/commands", json=body, headers=ceo)
    assert res.status_code == 201 and res.json()["name"] == "weekly"
    assert client.post("/api/assistant/commands", json=body, headers=ceo).status_code == 409
    assert client.post("/api/assistant/commands", json={**body, "name": "gst"}, headers=ceo).status_code == 422
    assert client.post("/api/assistant/commands", json={**body, "name": "two words"}, headers=ceo).status_code == 422
    assert client.post("/api/assistant/commands", json={**body, "name": "other"}, headers=cfo).status_code == 403
    listed = client.get("/api/assistant/commands", headers=cfo)
    assert [c["name"] for c in listed.json()] == ["weekly"]
    command_id = res.json()["id"]
    edited = client.put(f"/api/assistant/commands/{command_id}", json={**body, "prompt": "New."}, headers=ceo)
    assert edited.json()["prompt"] == "New."
    assert client.delete(f"/api/assistant/commands/{command_id}", headers=ceo).status_code == 204
    assert db.query(AssistantCommand).count() == 0
