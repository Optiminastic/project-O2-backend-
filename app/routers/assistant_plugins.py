"""Ask O2 plugins (remote MCP servers) and saved "/" commands.

The CEO adds, changes and removes them for the company; the CEO and CFO both see and use them.
A plugin's URL and key are never sent back, only its name, host and tools.
"""

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.assistant import plugins
from app.assistant.mcp_client import PluginError
from app.core.deps import require_roles
from app.database import get_db
from app.models import AssistantCommand, AssistantPlugin, User, UserRole
from app.services.audit import log_action

router = APIRouter(prefix="/assistant", tags=["assistant"])
viewer = require_roles(UserRole.ADMIN_CEO, UserRole.CFO)
manager = require_roles(UserRole.ADMIN_CEO)

URL_MAX = 2000
SECRET_MAX = 4000
HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
COMMAND_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
# Built into the "/" menu; a saved command cannot take one of these names.
RESERVED_COMMANDS = {
    "client", "clients", "vendor", "vendors", "invoice", "invoices", "bill", "bills", "project", "projects",
    "approval", "approvals", "summary", "overdue", "payables", "pending", "cash", "gst", "tds",
}


# ---------- plugins ----------


class AuthIn(BaseModel):
    type: Literal["none", "bearer", "basic", "header", "url"] = "none"
    token: str = Field("", max_length=SECRET_MAX)
    username: str = Field("", max_length=SECRET_MAX)
    password: str = Field("", max_length=SECRET_MAX)
    header_name: str = Field("", max_length=64)
    header_value: str = Field("", max_length=SECRET_MAX)

    def check(self) -> None:
        missing = {
            "bearer": not self.token.strip(),
            "basic": not (self.username.strip() and self.password.strip()),
            "header": not (HEADER_NAME_RE.match(self.header_name.strip()) and self.header_value.strip()),
        }.get(self.type, False)
        if missing:
            raise HTTPException(status_code=422, detail="Fill in the plugin's key.")


class PluginIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=8, max_length=URL_MAX)
    auth: AuthIn = AuthIn()
    directory_key: str | None = Field(None, max_length=40)


class PluginPatch(BaseModel):
    enabled: bool


def _plugin_view(p: AssistantPlugin) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "host": p.host,
        "directory_key": p.directory_key,
        "enabled": p.enabled,
        "last_error": p.last_error,
        "tools": [{"name": t["name"], "description": t.get("description", ""), "read_only": bool(t.get("read_only"))} for t in p.tools or []],
        "created_at": p.created_at,
    }


def _plugin(db: Session, plugin_id: int) -> AssistantPlugin:
    row = db.get(AssistantPlugin, plugin_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such plugin.")
    return row


@router.get("/plugins")
def list_plugins(db: Session = Depends(get_db), user: User = Depends(viewer)):
    rows = db.query(AssistantPlugin).order_by(AssistantPlugin.id).all()
    return {"directory": plugins.DIRECTORY, "plugins": [_plugin_view(p) for p in rows]}


@router.post("/plugins", status_code=201)
def add_plugin(body: PluginIn, db: Session = Depends(get_db), user: User = Depends(manager)):
    body.auth.check()
    row = AssistantPlugin(name=body.name.strip(), directory_key=body.directory_key, created_by=user.id)
    try:
        plugins.connect(row, body.url, plugins.auth_headers(body.auth.model_dump()))
    except PluginError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.add(row)
    db.flush()
    log_action(db, user, "Plugin added", "assistant_plugin", row.id, f"{row.name} ({row.host})")
    db.commit()
    return _plugin_view(row)


@router.post("/plugins/{plugin_id}/refresh")
def refresh_plugin(plugin_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    row = _plugin(db, plugin_id)
    try:
        plugins.refresh(row)
    except PluginError as exc:
        db.commit()  # keep last_error so the page shows it
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return _plugin_view(row)


@router.patch("/plugins/{plugin_id}")
def update_plugin(plugin_id: int, body: PluginPatch, db: Session = Depends(get_db), user: User = Depends(manager)):
    row = _plugin(db, plugin_id)
    row.enabled = body.enabled
    log_action(db, user, "Plugin turned on" if body.enabled else "Plugin turned off", "assistant_plugin", row.id, row.name)
    db.commit()
    return _plugin_view(row)


@router.delete("/plugins/{plugin_id}", status_code=204)
def remove_plugin(plugin_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    row = _plugin(db, plugin_id)
    log_action(db, user, "Plugin removed", "assistant_plugin", row.id, row.name)
    db.delete(row)
    db.commit()
    return Response(status_code=204)


# ---------- saved commands ----------


class CommandIn(BaseModel):
    name: str = Field(min_length=2, max_length=31)
    description: str = Field("", max_length=160)
    prompt: str = Field(min_length=1, max_length=4000)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip().lstrip("/").lower()
        if not COMMAND_NAME_RE.match(value):
            raise ValueError("Use 2 to 31 lowercase letters, numbers or dashes, starting with a letter.")
        if value in RESERVED_COMMANDS:
            raise ValueError(f"/{value} is already a built-in command.")
        return value


def _command_view(c: AssistantCommand) -> dict:
    return {"id": c.id, "name": c.name, "description": c.description, "prompt": c.prompt}


def _command(db: Session, command_id: int) -> AssistantCommand:
    row = db.get(AssistantCommand, command_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such command.")
    return row


def _name_free(db: Session, name: str, own_id: int | None = None) -> None:
    clash = db.query(AssistantCommand).filter_by(name=name).first()
    if clash is not None and clash.id != own_id:
        raise HTTPException(status_code=409, detail=f"/{name} already exists.")


@router.get("/commands")
def list_commands(db: Session = Depends(get_db), user: User = Depends(viewer)):
    return [_command_view(c) for c in db.query(AssistantCommand).order_by(AssistantCommand.name).all()]


@router.post("/commands", status_code=201)
def add_command(body: CommandIn, db: Session = Depends(get_db), user: User = Depends(manager)):
    _name_free(db, body.name)
    row = AssistantCommand(name=body.name, description=body.description.strip(), prompt=body.prompt.strip(), created_by=user.id)
    db.add(row)
    db.commit()
    return _command_view(row)


@router.put("/commands/{command_id}")
def update_command(command_id: int, body: CommandIn, db: Session = Depends(get_db), user: User = Depends(manager)):
    row = _command(db, command_id)
    _name_free(db, body.name, row.id)
    row.name, row.description, row.prompt = body.name, body.description.strip(), body.prompt.strip()
    db.commit()
    return _command_view(row)


@router.delete("/commands/{command_id}", status_code=204)
def remove_command(command_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    db.delete(_command(db, command_id))
    db.commit()
    return Response(status_code=204)
