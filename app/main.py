from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure tables exist (Alembic is the source of truth in production; this is a
    # convenience for first run / SQLite dev).
    Base.metadata.create_all(bind=engine)
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
    # localhost port for dev, plus any *.vercel.app preview/prod deployment.
    allow_origins=[settings.frontend_origin],
    allow_origin_regex=r"https://([a-z0-9-]+\.)*vercel\.app|http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = settings.api_v1_prefix
for r in (auth, team, dashboard, agents, clients, invoices, vendors, taxation, reports, approvals, verification, audit):
    app.include_router(r.router, prefix=api)


@app.get("/", tags=["health"])
def root():
    return {"app": settings.app_name, "status": "ok", "docs": "/docs"}


@app.get("/health", tags=["health"])
def health():
    return {"status": "healthy"}
