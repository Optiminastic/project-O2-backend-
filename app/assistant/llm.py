"""The OpenRouter chat client: the only place the assistant talks to an AI model."""

import logging
from dataclasses import dataclass, field

import httpx

from app.config import settings

logger = logging.getLogger("o2.assistant")
TIMEOUT_SECONDS = 60
# Swapped for a mock transport in tests.
_transport: httpx.BaseTransport | None = None


class AssistantNotConfigured(Exception):
    """No OpenRouter key is set."""


class AssistantUnavailable(Exception):
    """OpenRouter did not give a usable reply."""


@dataclass
class LLMReply:
    text: str
    tool_calls: list[dict] = field(default_factory=list)


def is_configured() -> bool:
    return bool(settings.openrouter_api_key.strip())


def chat(messages: list[dict], tools: list[dict]) -> LLMReply:
    """One chat completion. Tool calls come back as ``{"id", "name", "arguments"}`` (arguments as JSON text)."""
    if not is_configured():
        raise AssistantNotConfigured()
    body: dict = {"model": settings.openrouter_model, "messages": messages}
    if tools:
        body["tools"] = tools
    headers = {"Authorization": f"Bearer {settings.openrouter_api_key}", "X-Title": "Project O2"}
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, transport=_transport) as http:
            res = http.post(settings.openrouter_url, json=body, headers=headers)
        res.raise_for_status()
        message = res.json()["choices"][0]["message"]
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        # Never log the request itself: its headers carry the key.
        logger.warning("OpenRouter call failed: %s", type(exc).__name__)
        raise AssistantUnavailable() from exc
    calls = [
        {"id": c["id"], "name": c["function"]["name"], "arguments": c["function"].get("arguments") or "{}"}
        for c in message.get("tool_calls") or []
    ]
    return LLMReply(text=message.get("content") or "", tool_calls=calls)
