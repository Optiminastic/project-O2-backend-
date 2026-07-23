from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.database import Base, engine
import app.models  # noqa: F401  (register models on Base)
from app.routers import (
    auth,
    team,
    agents,
    clients,
    invoices,
    vendors,
    taxation,
    reports,
    approvals,
    verification,
    audit,
    dashboard,
    gst,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure tables exist (Alembic is the source of truth in production; this is a
    # convenience for first run / SQLite dev).
    Base.metadata.create_all(bind=engine)
    # Lightweight additive migrations. create_all() only creates missing *tables*,
    # never new columns on existing ones, and this project has no Alembic yet — so
    # apply idempotent ADD COLUMNs here. Safe to run on every startup.
    for stmt in (
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ",
        # Backfill legacy rows: existing members joined by accepting an invite (or
        # signing up), which required a login — seed their last-active from join time.
        "UPDATE users SET last_login_at = created_at WHERE last_login_at IS NULL",
    ):
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception:  # noqa: BLE001 — non-Postgres (e.g. SQLite) or already applied
            pass
    yield


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Project O2 — finance platform API (clients, vendors, invoices, taxation, approvals, reconciliation).",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # Allow the configured production origin (e.g. the Vercel URL), plus any
    # localhost port for dev, any *.vercel.app deployment, and the optiminastic.com
    # custom domain (e.g. https://o2.optiminastic.com).
    allow_origins=[settings.frontend_origin],
    allow_origin_regex=r"https://([a-z0-9-]+\.)*vercel\.app|https://([a-z0-9-]+\.)*optiminastic\.com|http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = settings.api_v1_prefix
for r in (auth, team, dashboard, agents, clients, invoices, vendors, taxation, reports, approvals, verification, audit, gst):
    app.include_router(r.router, prefix=api)


@app.get("/", tags=["health"])
def root():
    return {"app": settings.app_name, "status": "ok", "docs": "/docs"}


@app.get("/health", tags=["health"])
def health():
    return {"status": "healthy"}
