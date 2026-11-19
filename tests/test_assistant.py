"""Ask O2: the CEO's and CFO's assistant. A scripted fake stands in for the AI model."""

import json
from datetime import timedelta

import httpx
import pytest

from app.assistant import llm, runner
from app.assistant.describe import describe
from app.assistant.executor import run_tool
from app.assistant.llm import LLMReply
from app.assistant.tools import TOOLS, tool_by_name, tool_schemas
from app.main import app
from app.models import AssistantAction, AssistantConversation, Client
from tests.conftest import auth_header, client_body


class FakeModel:
    """Plays back scripted replies and remembers what it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []

    def __call__(self, messages, tools):
        self.seen.append(messages)
        return self.replies.pop(0)


def call(name, args, call_id="c1"):
    return LLMReply(text="", tool_calls=[{"id": call_id, "name": name, "arguments": json.dumps(args)}])


@pytest.fixture
def conv(db, users):
    row = AssistantConversation(user_id=users["ceo"].id, title="New chat")
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(llm.settings, "openrouter_api_key", "test-key")


# ---------- tables ----------


def test_assistant_tables_are_in_the_erase_registry():
    from app.services.wipe import DELETE_ORDER, assert_covers_schema

    assert_covers_schema()
    for table in ("assistant_actions", "assistant_files", "assistant_messages", "assistant_conversations"):
        assert table in DELETE_ORDER


def test_conversation_and_action_round_trip(db, users, conv):
    db.add(AssistantAction(conversation_id=conv.id, user_id=users["ceo"].id, tool="create_client",
                           tool_call_id="call_1", arguments={"body": {"business_name": "X"}},
                           summary="Create client X", fields=[], kind="write", status="pending"))
    db.commit()
    assert db.query(AssistantAction).one().arguments["body"]["business_name"] == "X"


# ---------- OpenRouter client ----------


def test_chat_without_a_key_is_not_configured(monkeypatch):
    monkeypatch.setattr(llm.settings, "openrouter_api_key", "")
    assert llm.is_configured() is False
    with pytest.raises(llm.AssistantNotConfigured):
        llm.chat([{"role": "user", "content": "hi"}], [])


def test_chat_parses_text_and_tool_calls(monkeypatch, configured):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "Looking",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "list_clients", "arguments": "{}"}}],
        }}]})

    monkeypatch.setattr(llm, "_transport", httpx.MockTransport(handler))
    reply = llm.chat([{"role": "user", "content": "clients?"}], [])
    assert reply.text == "Looking"
    assert reply.tool_calls == [{"id": "c1", "name": "list_clients", "arguments": "{}"}]


def test_chat_failure_is_unavailable(monkeypatch, configured):
    monkeypatch.setattr(llm, "_transport", httpx.MockTransport(lambda r: httpx.Response(502, json={})))
    with pytest.raises(llm.AssistantUnavailable):
        llm.chat([{"role": "user", "content": "hi"}], [])


# ---------- tools ----------


def test_every_tool_maps_to_a_real_endpoint_and_none_is_excluded():
    paths = app.openapi()["paths"]
    for tool in TOOLS:
        full = "/api" + tool.path
        assert full in paths and tool.method.lower() in paths[full], tool.name
        assert not tool.path.startswith(("/auth", "/public", "/admin")), tool.name
        if tool.kind == "write":
            assert tool.summary, tool.name


def test_schemas_inline_the_request_body():
    schemas = {s["function"]["name"]: s for s in tool_schemas(app.openapi())}
    create = schemas["create_client"]["function"]["parameters"]
    assert "business_name" in create["properties"]["body"]["properties"]
    assert "$ref" not in json.dumps(create)
    assert schemas["get_project"]["function"]["parameters"]["required"] == ["project_id"]
    assert "file_id" in schemas["attach_vendor_bill"]["function"]["parameters"]["properties"]


# ---------- confirm card and executor ----------


def test_describe_names_the_records_it_touches(db, users, client_row):
    card = describe(tool_by_name("create_invoice"), {"body": {"client_id": client_row.id}}, db)
    assert "Acme Traders" in card["summary"]
    assert {"label": "Client", "value": f"Acme Traders (#{client_row.id})"} in card["fields"]


def test_run_tool_goes_through_the_api_as_the_user(db, users):
    res = run_tool(tool_by_name("create_client"), {"body": client_body()}, users["ceo"], db)
    assert res.ok, res.error
    assert res.data["business_name"] == "Globex"
    denied = run_tool(tool_by_name("delete_client"), {"client_id": res.data["id"]}, users["coordinator"], db)
    assert not denied.ok and denied.status == 403


# ---------- runner ----------


def test_a_read_runs_at_once_and_its_result_goes_back(db, users, conv, monkeypatch, client_row):
    fake = FakeModel(call("list_clients", {}), LLMReply(text="You have 1 client."))
    monkeypatch.setattr(runner.llm, "chat", fake)
    runner.handle_message(db, users["ceo"], conv, "How many clients?", [])
    assert "Acme Traders" in fake.seen[1][-1]["content"]
    assert runner.conversation_view(db, conv)["messages"][-1]["text"] == "You have 1 client."


def test_a_write_only_proposes_until_confirmed_and_runs_once(db, users, conv, monkeypatch):
    fake = FakeModel(call("create_client", {"body": client_body()}), LLMReply(text="Done."))
    monkeypatch.setattr(runner.llm, "chat", fake)
    runner.handle_message(db, users["ceo"], conv, "Add Globex", [])
    action = db.query(AssistantAction).one()
    assert action.status == "pending" and db.query(Client).count() == 0
    assert action.summary == "Create client Globex"
    runner.confirm_action(db, users["ceo"], action.id)
    db.refresh(action)
    assert action.status == "done" and db.query(Client).count() == 1
    with pytest.raises(runner.HTTPException) as again:
        runner.confirm_action(db, users["ceo"], action.id)
    assert again.value.status_code == 409


def test_only_the_asker_can_confirm(db, users, conv, monkeypatch):
    monkeypatch.setattr(runner.llm, "chat", FakeModel(call("create_client", {"body": client_body()})))
    runner.handle_message(db, users["ceo"], conv, "Add Globex", [])
    action = db.query(AssistantAction).one()
    with pytest.raises(runner.HTTPException) as other:
        runner.confirm_action(db, users["cfo"], action.id)
    assert other.value.status_code == 404


def test_an_expired_action_is_refused(db, users, conv, monkeypatch):
    monkeypatch.setattr(runner.llm, "chat", FakeModel(call("create_client", {"body": client_body()})))
    runner.handle_message(db, users["ceo"], conv, "Add Globex", [])
    action = db.query(AssistantAction).one()
    action.created_at = action.created_at - timedelta(minutes=31)
    db.commit()
    with pytest.raises(runner.HTTPException) as late:
        runner.confirm_action(db, users["ceo"], action.id)
    assert late.value.status_code == 410
    assert db.query(Client).count() == 0


def test_a_refused_api_call_marks_the_action_failed(db, users, conv, monkeypatch):
    fake = FakeModel(call("delete_client", {"client_id": 999}), LLMReply(text="It failed."))
    monkeypatch.setattr(runner.llm, "chat", fake)
    runner.handle_message(db, users["ceo"], conv, "Delete client 999", [])
    action = db.query(AssistantAction).one()
    runner.confirm_action(db, users["ceo"], action.id)
    db.refresh(action)
    assert action.status == "failed" and action.error


def test_a_new_message_drops_a_change_still_waiting(db, users, conv, monkeypatch):
    fake = FakeModel(call("create_client", {"body": client_body()}), LLMReply(text="Okay."))
    monkeypatch.setattr(runner.llm, "chat", fake)
    runner.handle_message(db, users["ceo"], conv, "Add Globex", [])
    runner.handle_message(db, users["ceo"], conv, "Never mind", [])
    assert db.query(AssistantAction).one().status == "cancelled"


# ---------- API ----------


def test_only_ceo_and_cfo_can_use_the_assistant(client, users):
    for role in ("exec", "manager", "coordinator"):
        assert client.get("/api/assistant/status", headers=auth_header(users[role])).status_code == 403
    assert client.get("/api/assistant/status", headers=auth_header(users["cfo"])).status_code == 200


def test_without_a_key_status_says_so_and_messages_return_503(client, users, monkeypatch):
    monkeypatch.setattr(llm.settings, "openrouter_api_key", "")
    h = auth_header(users["ceo"])
    assert client.get("/api/assistant/status", headers=h).json()["configured"] is False
    conv = client.post("/api/assistant/conversations", headers=h).json()
    res = client.post(f"/api/assistant/conversations/{conv['id']}/messages", json={"text": "hi"}, headers=h)
    assert res.status_code == 503 and "not set up" in res.json()["detail"]


def test_a_conversation_is_private_to_its_owner(client, users):
    conv = client.post("/api/assistant/conversations", headers=auth_header(users["ceo"])).json()
    assert client.get(f"/api/assistant/conversations/{conv['id']}", headers=auth_header(users["cfo"])).status_code == 404


def test_attach_a_file_then_propose_attaching_it_to_a_vendor_bill(client, users, monkeypatch, configured):
    h = auth_header(users["ceo"])
    conv = client.post("/api/assistant/conversations", headers=h).json()
    up = client.post(f"/api/assistant/conversations/{conv['id']}/files", headers=h,
                     files={"file": ("bill.pdf", b"%PDF-1.4 test", "application/pdf")})
    assert up.status_code == 200
    bad = client.post(f"/api/assistant/conversations/{conv['id']}/files", headers=h,
                      files={"file": ("run.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 422
    fake = FakeModel(call("attach_vendor_bill", {"invoice_id": 1, "file_id": up.json()["id"]}, "c9"))
    monkeypatch.setattr(runner.llm, "chat", fake)
    view = client.post(f"/api/assistant/conversations/{conv['id']}/messages",
                       json={"text": "attach it to bill 1", "file_ids": [up.json()["id"]]}, headers=h).json()
    assert view["messages"][0]["files"][0]["name"] == "bill.pdf"
    card = view["messages"][-1]["actions"][0]
    assert card["status"] == "pending" and card["tool"] == "attach_vendor_bill"
    assert "file_id=" in fake.seen[0][-1]["content"]


# ---------- phase 2: every O2 action is a tool ----------

EXCLUDED_PREFIXES = ("/api/auth", "/api/public", "/api/admin", "/api/assistant")


def test_every_o2_action_has_a_tool():
    covered = {(t.method.lower(), "/api" + t.path) for t in TOOLS}
    missing = [
        f"{method.upper()} {path}"
        for path, ops in app.openapi()["paths"].items()
        if path.startswith("/api") and not path.startswith(EXCLUDED_PREFIXES)
        for method in ops
        if (method, path) not in covered
    ]
    assert missing == []


def test_an_upload_tool_carries_its_form_fields():
    from app.assistant.tools import form_fields

    tool = tool_by_name("upload_bank_statement")
    assert form_fields(tool, app.openapi()) == {"bank_name", "account_number"}
    schema = {s["function"]["name"]: s for s in tool_schemas(app.openapi())}["upload_bank_statement"]
    assert {"file_id", "bank_name"} <= schema["function"]["parameters"]["properties"].keys()


def test_the_system_prompt_is_marked_for_prompt_caching(db, users, conv, monkeypatch):
    fake = FakeModel(LLMReply(text="Hi."))
    monkeypatch.setattr(runner.llm, "chat", fake)
    runner.handle_message(db, users["ceo"], conv, "Hello", [])
    system = fake.seen[0][0]
    assert system["role"] == "system"
    assert system["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_tool_schemas_are_valid_and_keep_fields_named_title():
    """Claude rejects the whole request if any tool's schema is malformed."""
    schemas = {s["function"]["name"]: s["function"]["parameters"] for s in tool_schemas(app.openapi())}

    def walk(node, where):
        if isinstance(node, dict):
            if "type" in node:
                kind = node["type"]
                assert isinstance(kind, str) or (isinstance(kind, list) and all(isinstance(k, str) for k in kind)), where
            for key, value in node.items():
                walk(value, f"{where}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{where}[{i}]")

    for name, schema in schemas.items():
        walk(schema, name)
    assert "title" in schemas["create_project"]["properties"]["body"]["properties"]
    assert "title" in schemas["update_project"]["properties"]["body"]["properties"]


def test_long_results_keep_their_true_count_instead_of_being_cut():
    from app.assistant.executor import MAX_RESULT_CHARS, ToolResult, for_model

    rows = [{"id": i, "title": f"Project {i}", "vendors": [{"name": "x" * 200}] * 5} for i in range(60)]
    text = for_model(ToolResult(True, 200, rows))
    data = json.loads(text)
    assert len(text) <= MAX_RESULT_CHARS
    assert data["total_count"] == 60
    assert data["items"][0] == {"id": 0, "title": "Project 0", "vendors_count": 5}


# ---------- plain-text replies ----------


def test_reply_markdown_is_stripped_before_it_is_shown(db, users, conv, monkeypatch):
    text = "## Summary\n**Managing projects** and __invoices__.\n* Check `INV-12`\n- Pay 2*3 items"
    monkeypatch.setattr(runner.llm, "chat", FakeModel(LLMReply(text=text)))
    runner.handle_message(db, users["ceo"], conv, "Help", [])
    shown = runner.conversation_view(db, conv)["messages"][-1]["text"]
    assert shown == "Summary\nManaging projects and invoices.\n- Check INV-12\n- Pay 2*3 items"
    assert "**" not in shown


def test_the_model_is_told_to_write_plain_text(db, users, conv, monkeypatch):
    fake = FakeModel(LLMReply(text="Hi"))
    monkeypatch.setattr(runner.llm, "chat", fake)
    runner.handle_message(db, users["ceo"], conv, "Hi", [])
    system = fake.seen[0][0]["content"]
    system = system if isinstance(system, str) else system[0]["text"]
    assert "no Markdown" in system


def test_plain_text_leaves_ordinary_text_alone():
    from app.assistant.formatting import plain_text

    assert plain_text("Total: 2 * 3 = 6, file_name_v2") == "Total: 2 * 3 = 6, file_name_v2"
    assert plain_text(None) is None
    assert plain_text("") == ""
