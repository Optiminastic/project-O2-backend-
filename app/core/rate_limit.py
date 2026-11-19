"""Rate limits: stop password guessing and request floods.

Counts live in memory, one set per server worker (production runs two), so a
limit can be reached at most once per worker. That is enough to stop guessing:
five wrong passwords per worker is still only ten tries in fifteen minutes.
Counts reset when the server restarts.
"""

import math
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import settings


@dataclass(frozen=True)
class Limit:
    """At most ``hits`` events per ``window`` seconds for one key."""

    hits: int
    window: float


MINUTE = 60
LOGIN_FAILURES_PER_EMAIL = Limit(hits=5, window=15 * MINUTE)
LOGIN_FAILURES_PER_IP = Limit(hits=20, window=15 * MINUTE)
PUBLIC_AUTH_PER_IP = Limit(hits=20, window=15 * MINUTE)
REQUESTS_PER_IP = Limit(hits=300, window=MINUTE)

# Unauthenticated endpoints that create accounts or read invite tokens.
PUBLIC_AUTH_PATHS = ("/auth/signup", "/auth/invite/")


class SlidingWindow:
    """Thread-safe event counter per key over a sliding time window."""

    def __init__(self, limit: Limit):
        self.limit = limit
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, key: str, now: float) -> deque[float]:
        events = self._events[key]
        while events and events[0] <= now - self.limit.window:
            events.popleft()
        return events

    def retry_after(self, key: str) -> int:
        """Seconds until ``key`` may act again, or 0 if it is under the limit."""
        now = time.monotonic()
        with self._lock:
            events = self._trim(key, now)
            if len(events) < self.limit.hits:
                if not events:
                    self._events.pop(key, None)
                return 0
            return max(1, math.ceil(events[0] + self.limit.window - now))

    def hit(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._trim(key, now).append(now)

    def take(self, key: str) -> int:
        """Record one event if allowed; return 0, or the seconds to wait if over the limit."""
        now = time.monotonic()
        with self._lock:
            events = self._trim(key, now)
            if len(events) >= self.limit.hits:
                return max(1, math.ceil(events[0] + self.limit.window - now))
            events.append(now)
            return 0

    def clear(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


login_failures_by_email = SlidingWindow(LOGIN_FAILURES_PER_EMAIL)
login_failures_by_ip = SlidingWindow(LOGIN_FAILURES_PER_IP)
public_auth_by_ip = SlidingWindow(PUBLIC_AUTH_PER_IP)
requests_by_ip = SlidingWindow(REQUESTS_PER_IP)
ALL_WINDOWS = (login_failures_by_email, login_failures_by_ip, public_auth_by_ip, requests_by_ip)


def reset_all() -> None:
    """Forget every count (tests)."""
    for window in ALL_WINDOWS:
        window.reset()


def client_ip(request: Request) -> str:
    """The caller's IP address.

    Behind the proxy (Coolify's Traefik) every request comes from the proxy, so the
    real address is read from X-Forwarded-For. Each trusted proxy appends the address
    it saw, so the entry ``trusted_proxy_hops`` from the right is the one no caller
    can forge. With no header (local runs) the socket address is used.
    """
    forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
    hops = settings.trusted_proxy_hops
    if hops > 0 and forwarded:
        return forwarded[-min(hops, len(forwarded))]
    return request.client.host if request.client else "unknown"


def too_many(seconds: int) -> HTTPException:
    minutes = max(1, math.ceil(seconds / MINUTE))
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Too many attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}.",
        headers={"Retry-After": str(seconds)},
    )


def check_login_allowed(request: Request, email: str) -> None:
    """Refuse a login before checking the password once this email or IP has failed too often."""
    if not settings.rate_limit_enabled:
        return
    wait = max(login_failures_by_email.retry_after(email), login_failures_by_ip.retry_after(client_ip(request)))
    if wait:
        raise too_many(wait)


def record_login_failure(request: Request, email: str) -> None:
    if not settings.rate_limit_enabled:
        return
    login_failures_by_email.hit(email)
    login_failures_by_ip.hit(client_ip(request))


def record_login_success(email: str) -> None:
    login_failures_by_email.clear(email)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Caps requests per IP across the whole API, with a tighter cap on public auth endpoints."""

    async def dispatch(self, request: Request, call_next):
        if not settings.rate_limit_enabled or request.method == "OPTIONS":
            return await call_next(request)
        ip = client_ip(request)
        wait = requests_by_ip.take(ip)
        path = request.url.path.removeprefix(settings.api_v1_prefix)
        if not wait and path.startswith(PUBLIC_AUTH_PATHS):
            wait = public_auth_by_ip.take(ip)
        if wait:
            error = too_many(wait)
            return JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=error.headers)
        return await call_next(request)
