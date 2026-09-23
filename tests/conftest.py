"""Test environment.

The env vars are set before any app module is imported, because settings and the
engine are both built at import time.

`client` gives each test module its own database. Without it every API test file
shared one SQLite file, which meant assertions had to be written loosely enough to
survive whatever another file had already done — and loose assertions are the ones
that miss regressions.
"""

import os
import tempfile

os.environ.setdefault("CLASSIFIER_BACKEND", "mock")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="masroufi-test-"), "test.db"
)

import pytest  # noqa: E402  - must come after the env is set
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402


@pytest.fixture(scope="module")
def client():
    """A TestClient backed by a fresh in-memory database, seeded like a new install."""
    from app import idempotency  # noqa: F401  - registers its table
    from app.db import get_session
    from app.kitchen import ensure_kitchen
    from app.main import app
    from app.sources import ensure_sources

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared connection, so :memory: survives
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        ensure_sources(session)
        ensure_kitchen(session)

    def _session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session_override
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
