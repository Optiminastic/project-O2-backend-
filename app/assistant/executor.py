"""Run a tool through O2's own API, in process, as the user. Every permission check, validation rule
and audit entry applies exactly as when the user clicks the button themselves."""

import json
import os
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.assistant.tools import Tool, form_fields, split_arguments
from app.config import settings
from app.core.security import create_access_token
from app.models import AssistantFile, User

MAX_RESULT_CHARS = 6000
MAX_ERROR_CHARS = 500


@dataclass
class ToolResult:
    ok: bool
    status: int
    data: object
    error: str | None = None


def _attachment(db: Session, file_id, user: User) -> AssistantFile | None:
    if not isinstance(file_id, int) or isinstance(file_id, bool):
        return None
    row = db.get(AssistantFile, file_id)
    return row if row is not None and row.user_id == user.id else None


def _read(reference: str) -> bytes:
    if reference.startswith("s3://"):
        from app.services.storage import _client  # noqa: PLC0415

        bucket, _, key = reference.removeprefix("s3://").partition("/")
        return _client().get_object(Bucket=bucket, Key=key)["Body"].read()
    with open(os.path.abspath(reference), "rb") as fh:
        return fh.read()


def _error_text(data, fallback: str) -> str:
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):  # validation errors: say which field and why
        parts = [f"{'.'.join(map(str, e.get('loc', [])[1:]))}: {e.get('msg')}" for e in detail if isinstance(e, dict)]
        return "; ".join(parts)[:MAX_ERROR_CHARS] or fallback
    return fallback[:MAX_ERROR_CHARS]


def run_tool(tool: Tool, args: dict, user: User, db: Session) -> ToolResult:
    # Imported here: app.main imports the assistant router, which imports this module.
    from fastapi.testclient import TestClient  # noqa: PLC0415  (in-process ASGI calls)

    from app.main import app  # noqa: PLC0415

    path_args, query, body, file_id = split_arguments(tool, args)
    try:
        url = settings.api_v1_prefix + tool.path.format(**path_args)
    except KeyError as missing:
        return ToolResult(False, 422, None, f"Missing {missing.args[0]}")
    token = create_access_token(user.email, user.role.value, user.name)
    kwargs: dict = {"params": query, "headers": {"Authorization": f"Bearer {token}"}}
    if tool.file_field:
        upload = _attachment(db, file_id, user)
        if upload is None:
            return ToolResult(False, 422, None, "Attach the file in the chat first.")
        kwargs["files"] = {tool.file_field: (upload.file_name, _read(upload.file_path), upload.content_type)}
        # Plain form fields (such as a statement's bank name) go in the form, not the query string.
        fields = form_fields(tool, app.openapi())
        kwargs["data"] = {k: str(v) for k, v in query.items() if k in fields}
        kwargs["params"] = {k: v for k, v in query.items() if k not in fields}
    elif body is not None:
        kwargs["json"] = body
    # Not used as a context manager, so the app's startup work does not run on every call.
    http = TestClient(app, raise_server_exceptions=False)
    try:
        res = http.request(tool.method, url, **kwargs)
    finally:
        http.close()
    try:
        data = res.json()
    except ValueError:
        data = None
    if res.status_code >= 400:
        return ToolResult(False, res.status_code, data, _error_text(data, res.text or f"HTTP {res.status_code}"))
    return ToolResult(True, res.status_code, data)


def _slim(item):
    """An item with its plain fields only; nested lists become counts, nested objects their plain fields."""
    if not isinstance(item, dict):
        return item
    out = {}
    for key, value in item.items():
        if isinstance(value, list):
            out[f"{key}_count"] = len(value)
        elif isinstance(value, dict):
            out[key] = {k: v for k, v in value.items() if not isinstance(v, (dict, list))}
        else:
            out[key] = value
    return out


def _dump(payload) -> str:
    return json.dumps(payload, default=str)


def for_model(result: ToolResult) -> str:
    """The tool result as the model sees it, within MAX_RESULT_CHARS.

    Never cut mid-list: a cut list makes the model miscount. Long lists keep their true total and
    slim items, and say how many are shown when even that is too long.
    """
    payload = result.data if result.ok else {"error": result.error, "status": result.status}
    text = _dump(payload)
    if len(text) <= MAX_RESULT_CHARS:
        return text
    if isinstance(payload, list):
        items = [_slim(x) for x in payload]
        shown = len(items)
        while shown > 1 and len(_dump(items[:shown])) > MAX_RESULT_CHARS:
            shown //= 2
        compact = {"total_count": len(items), "items": items[:shown]}
        if shown < len(items):
            compact["note"] = f"Showing {shown} of {len(items)}. Use a filter, or look one up by id, for the rest."
        return _dump(compact)
    if isinstance(payload, dict):
        slim = {k: ([_slim(x) for x in v] if isinstance(v, list) else v) for k, v in payload.items()}
        text = _dump(slim)
        if len(text) <= MAX_RESULT_CHARS:
            return text
        return _dump(_slim(payload))
    return text[:MAX_RESULT_CHARS]
