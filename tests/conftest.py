"""Test harness: a dedicated Postgres database, never the real one.

The environment is set before ``app`` is imported because ``app.config`` and
``app.database`` read settings at import time.
"""

import os

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://o2_admin:o2_secret@localhost:5544/projecto2_test",
)
if not TEST_DATABASE_URL.rsplit("/", 1)[-1].endswith("_test"):
    raise RuntimeError(f"Refusing to run tests against a non-test database: {TEST_DATABASE_URL}")

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["SMTP_HOST"] = ""  # never send real email from tests
os.environ["FRONTEND_ORIGIN"] = "http://testserver"
os.environ["COMPANY_LEGAL_NAME"] = "Test Supplier Pvt Ltd"
os.environ["COMPANY_GSTIN"] = "27AAAAA0000A1Z5"
os.environ["COMPANY_STATE"] = "Maharashtra"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Client, Service, User, UserRole  # noqa: E402
from app.services.agents import ensure_house_agent, house_agent  # noqa: E402

assert settings.database_url == TEST_DATABASE_URL, "settings did not pick up the test database"


DEFAULT_SERVICE_TITLE = "Finance retainer"


def default_service_id() -> int:
    """The service seeded for every test; invoices cannot be created without one."""
    with SessionLocal() as session:
        return session.query(Service).filter(Service.title == DEFAULT_SERVICE_TITLE).one().id


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _clean_tables():
    # The app guarantees the in-house "Opti" agent exists (created at startup).
    with SessionLocal() as session:
        ensure_house_agent(session)
        session.add(Service(title=DEFAULT_SERVICE_TITLE, description="Monthly finance retainer", sac_code="998311", gst_rate=18))
        session.commit()
    yield
    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _make_user(db, email: str, role: UserRole) -> User:
    user = User(name=email.split("@")[0].title(), email=email, hashed_password=hash_password("x"), role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def auth_header(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.email, user.role.value, user.name)}"}


@pytest.fixture
def users(db):
    return {
        "exec": _make_user(db, "exec@optiminastic.com", UserRole.FINANCE_EXECUTIVE),
        "manager": _make_user(db, "manager@optiminastic.com", UserRole.FINANCE_MANAGER),
        "cfo": _make_user(db, "cfo@optiminastic.com", UserRole.CFO),
        "ceo": _make_user(db, "ceo@optiminastic.com", UserRole.ADMIN_CEO),
    }


@pytest.fixture
def client_row(db):
    row = Client(
        business_name="Acme Traders",
        email="accounts@acme.example",
        gst_number="27BBBBB1111B1Z5",
        agent_id=house_agent(db).id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
