"""Plugins: remote MCP servers whose tools Ask O2 can use alongside O2's own.

Same rule as O2's own tools: a tool the server marks read-only runs at once, anything else becomes a
confirm card. Plugin URLs must be public https addresses, so a plugin cannot be pointed at the O2
server's own network.
"""

import base64
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.assistant import mcp_client
from app.assistant.executor import MAX_RESULT_CHARS, ToolResult
from app.assistant.mcp_client import Connection, PluginError
from app.assistant.secrets import SecretUnreadable, seal, unseal
from app.models import AssistantPlugin

PREFIX = "plugin"
TOOL_NAME_MAX = 64
MAX_TOOLS_PER_PLUGIN = 40
DESCRIPTION_MAX = 1000
FIELD_VALUE_MAX = 200
MAX_FIELDS = 8
_NAME_RE = re.compile(rf"^{PREFIX}(\d+)__(.+)$")
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")

AUTH_NONE, AUTH_BEARER, AUTH_BASIC, AUTH_HEADER, AUTH_URL = "none", "bearer", "basic", "header", "url"

# Ready-made plugins shown on the Plugins page. "url" auth means the key is part of the URL itself.
DIRECTORY = [
    {
        "key": "razorpay",
        "name": "Razorpay",
        "description": "Payments, orders, refunds, settlements and payment links from your Razorpay account.",
        "url": "https://mcp.razorpay.com/mcp",
        "auth": AUTH_BASIC,
        "auth_help": "Razorpay Dashboard, Account & Settings, API Keys. Use the Key Id and the Key Secret.",
        "docs": "https://razorpay.com/docs/mcp-server/",
    },
    {
        "key": "stripe",
        "name": "Stripe",
        "description": "Customers, payments, invoices and refunds in Stripe.",
        "url": "https://mcp.stripe.com",
        "auth": AUTH_BEARER,
        "auth_help": "A restricted (agent) API key from the Stripe Dashboard, Developers, API keys.",
        "docs": "https://docs.stripe.com/mcp",
    },
    {
        "key": "zapier",
        "name": "Zapier",
        "description": "Gmail, Google Sheets, Zoho, Slack, Tally and thousands more apps, through the actions you enable in Zapier.",
        "url": "",
        "auth": AUTH_URL,
        "auth_help": "Make a server at mcp.zapier.com, add the actions you want, then copy its server URL from the Connect tab.",
        "docs": "https://docs.zapier.com/mcp",
    },
]


@dataclass
class PluginTool:
    plugin: AssistantPlugin
    name: str
    description: str
    input_schema: dict
    read_only: bool

    @property
    def public_name(self) -> str:
        return tool_name(self.plugin.id, self.name)


def tool_name(plugin_id: int, name: str) -> str:
    return f"{PREFIX}{plugin_id}__{_UNSAFE.sub('_', name)}"[:TOOL_NAME_MAX]


# ---------- connecting ----------


def _resolve(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)]


def check_url(url: str) -> str:
    """The URL's host, if it is a public https address. Raises PluginError otherwise."""
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or not parts.hostname:
        raise PluginError("Use the plugin's full https:// URL.")
    try:
        addresses = _resolve(parts.hostname)
    except OSError as exc:
        raise PluginError("That address could not be found.") from exc
    if not addresses or not all(ipaddress.ip_address(a).is_global for a in addresses):
        raise PluginError("Plugins must be on the public internet.")
    return parts.hostname


def auth_headers(auth: dict) -> dict[str, str]:
    kind = auth.get("type", AUTH_NONE)
    if kind == AUTH_BEARER:
        return {"Authorization": f"Bearer {auth.get('token', '').strip()}"}
    if kind == AUTH_BASIC:
        pair = f"{auth.get('username', '').strip()}:{auth.get('password', '').strip()}"
        return {"Authorization": f"Basic {base64.b64encode(pair.encode()).decode()}"}
    if kind == AUTH_HEADER:
        return {auth.get("header_name", "").strip(): auth.get("header_value", "").strip()}
    return {}


def _connection(plugin: AssistantPlugin) -> Connection:
    try:
        stored = unseal(plugin.connection)
    except SecretUnreadable as exc:
        raise PluginError("This plugin's key can no longer be read. Remove it and add it again.") from exc
    return Connection(url=stored["url"], headers=stored.get("headers") or {})


def _fetch_tools(connection: Connection) -> list[dict]:
    check_url(connection.url)
    tools = mcp_client.list_tools(connection)[:MAX_TOOLS_PER_PLUGIN]
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema, "read_only": t.read_only}
        for t in tools
    ]


def connect(plugin: AssistantPlugin, url: str, headers: dict[str, str]) -> None:
    """Checks the server answers and lists its tools, then stores the connection (encrypted)."""
    connection = Connection(url=url.strip(), headers=headers)
    plugin.host = check_url(connection.url)
    plugin.tools = _fetch_tools(connection)
    plugin.connection = seal({"url": connection.url, "headers": headers})
    plugin.last_error = None


def refresh(plugin: AssistantPlugin) -> None:
    try:
        plugin.tools = _fetch_tools(_connection(plugin))
        plugin.last_error = None
    except PluginError as exc:
        plugin.last_error = str(exc)
        raise


# ---------- the agent's view ----------


def _schema(raw: dict) -> dict:
    schema = {k: v for k, v in raw.items() if k != "$schema"}
    if schema.get("type") != "object":
        schema = {"type": "object", "properties": schema.get("properties") or {}}
    schema.setdefault("properties", {})
    return schema


def active_tools(db: Session) -> list[PluginTool]:
    out = []
    for plugin in db.query(AssistantPlugin).filter_by(enabled=True).order_by(AssistantPlugin.id).all():
        for t in plugin.tools or []:
            out.append(PluginTool(plugin, t["name"], t.get("description", ""), t.get("input_schema") or {}, bool(t.get("read_only"))))
    return out


def tool_schemas(db: Session) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.public_name,
                "description": f"[{t.plugin.name} plugin] {t.description}"[:DESCRIPTION_MAX],
                "parameters": _schema(t.input_schema),
            },
        }
        for t in active_tools(db)
    ]


def find(db: Session, public_name: str) -> PluginTool | None:
    match = _NAME_RE.match(public_name or "")
    if not match:
        return None
    plugin = db.get(AssistantPlugin, int(match.group(1)))
    if plugin is None or not plugin.enabled:
        return None
    for t in plugin.tools or []:
        if tool_name(plugin.id, t["name"]) == public_name:
            return PluginTool(plugin, t["name"], t.get("description", ""), t.get("input_schema") or {}, bool(t.get("read_only")))
    return None


def _value(value) -> str:
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= FIELD_VALUE_MAX else text[: FIELD_VALUE_MAX - 1] + "…"


def describe(tool: PluginTool, args: dict) -> dict:
    """The confirm card: which plugin, which tool, and the arguments it will be sent."""
    label = tool.name.replace("_", " ").replace("-", " ").strip().capitalize()
    fields = [{"label": "Plugin", "value": tool.plugin.name}]
    fields += [{"label": k.replace("_", " ").capitalize(), "value": _value(v)} for k, v in list(args.items())[:MAX_FIELDS]]
    return {"summary": f"{label} ({tool.plugin.name})", "fields": fields}


def run(tool: PluginTool, args: dict) -> ToolResult:
    try:
        connection = _connection(tool.plugin)
        check_url(connection.url)
        ok, text = mcp_client.call_tool(connection, tool.name, args)
    except PluginError as exc:
        return ToolResult(ok=False, status=502, data=None, error=str(exc))
    text = text[:MAX_RESULT_CHARS]
    return ToolResult(ok=True, status=200, data={"result": text}) if ok else ToolResult(ok=False, status=400, data=None, error=text or "The plugin tool failed.")
