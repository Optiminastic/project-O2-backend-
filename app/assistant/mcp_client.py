"""A small client for remote MCP servers over Streamable HTTP: list their tools and call one.

Each call opens a short session (initialize, then the request) and closes it. That costs a round trip
or two, but holds no state between requests and works with every server that follows the spec.
Request headers carry plugin keys, so nothing here logs a request or a header.
"""

import json
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger("o2.assistant.plugins")

PROTOCOL_VERSION = "2025-06-18"
TIMEOUT_SECONDS = 30
MAX_TOOL_PAGES = 10
CLIENT_INFO = {"name": "project-o2", "version": "1.0"}
# Swapped for a mock transport in tests.
_transport: httpx.BaseTransport | None = None


class PluginError(Exception):
    """The plugin could not be reached or refused the request. The message is safe to show the user."""


@dataclass
class Connection:
    url: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class RemoteTool:
    name: str
    description: str
    input_schema: dict
    read_only: bool


def _from_sse(body: str, request_id: int) -> dict:
    """The JSON-RPC reply with our id from an event stream (servers may send notifications first)."""
    for block in body.split("\n\n"):
        data = "\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith("data:"))
        if not data:
            continue
        try:
            message = json.loads(data)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("id") == request_id:
            return message
    raise PluginError("The plugin sent no reply.")


class _Session:
    def __init__(self, http: httpx.Client, connection: Connection):
        self.http = http
        self.url = connection.url
        self.headers = {
            **connection.headers,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        self.next_id = 1

    def _post(self, payload: dict) -> httpx.Response:
        try:
            res = self.http.post(self.url, json=payload, headers=self.headers)
        except httpx.HTTPError as exc:
            logger.warning("Plugin request failed: %s", type(exc).__name__)
            raise PluginError("Could not reach the plugin. Check the URL and try again.") from exc
        if res.status_code in (401, 403):
            raise PluginError("The plugin refused the key. Check the key and try again.")
        if res.status_code == 404:
            raise PluginError("No MCP server answered at that URL.")
        if res.status_code >= 400:
            raise PluginError(f"The plugin answered with an error ({res.status_code}).")
        return res

    def request(self, method: str, params: dict | None = None) -> dict:
        request_id = self.next_id
        self.next_id += 1
        res = self._post({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        try:
            if "text/event-stream" in res.headers.get("content-type", ""):
                message = _from_sse(res.text, request_id)
            else:
                message = res.json()
        except ValueError as exc:
            raise PluginError("The plugin's reply was not valid MCP.") from exc
        if not isinstance(message, dict):
            raise PluginError("The plugin's reply was not valid MCP.")
        if "error" in message:
            error = message["error"] if isinstance(message["error"], dict) else {}
            raise PluginError(str(error.get("message") or "The plugin returned an error.")[:300])
        self._remember_session(res)
        return message.get("result") or {}

    def notify(self, method: str) -> None:
        self._post({"jsonrpc": "2.0", "method": method})

    def _remember_session(self, res: httpx.Response) -> None:
        session_id = res.headers.get("mcp-session-id")
        if session_id:
            self.headers["Mcp-Session-Id"] = session_id

    def initialize(self) -> None:
        result = self.request(
            "initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO}
        )
        self.headers["MCP-Protocol-Version"] = str(result.get("protocolVersion") or PROTOCOL_VERSION)
        self.notify("notifications/initialized")


def _open() -> httpx.Client:
    # Redirects are not followed: a plugin URL that bounces elsewhere is refused, not trusted.
    return httpx.Client(timeout=TIMEOUT_SECONDS, transport=_transport, follow_redirects=False)


def _tool(raw: dict) -> RemoteTool | None:
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        return None
    annotations = raw.get("annotations") if isinstance(raw.get("annotations"), dict) else {}
    schema = raw.get("inputSchema") if isinstance(raw.get("inputSchema"), dict) else {}
    return RemoteTool(
        name=name,
        description=str(raw.get("description") or annotations.get("title") or name),
        input_schema=schema,
        read_only=annotations.get("readOnlyHint") is True,
    )


def list_tools(connection: Connection) -> list[RemoteTool]:
    with _open() as http:
        session = _Session(http, connection)
        session.initialize()
        tools: list[RemoteTool] = []
        cursor = None
        for _ in range(MAX_TOOL_PAGES):
            result = session.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += [t for t in (_tool(r) for r in result.get("tools") or [] if isinstance(r, dict)) if t]
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools


def _content_text(result: dict) -> str:
    parts = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        else:
            parts.append(f"[{item.get('type', 'content')} not shown]")
    if not parts and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], default=str))
    return "\n".join(parts)


def call_tool(connection: Connection, name: str, arguments: dict) -> tuple[bool, str]:
    """Runs one tool. Returns (ok, text); ok is False when the tool itself reported an error."""
    with _open() as http:
        session = _Session(http, connection)
        session.initialize()
        result = session.request("tools/call", {"name": name, "arguments": arguments})
        return not result.get("isError"), _content_text(result)
