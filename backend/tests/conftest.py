import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.database.base import Base


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    Base.metadata.create_all(engine)

    db = TestingSessionLocal()

    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def postgres_engine():
    if os.getenv("RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL integration tests are disabled")

    settings = get_settings()
    database_url = settings.database_url.get_secret_value()

    from sqlalchemy.engine import make_url

    test_url = make_url(database_url).set(
        database="vanguard_ai_test"
    )

    if test_url.database != "vanguard_ai_test":
        pytest.fail("Refusing to use a non-test database")

    engine = create_engine(
        test_url,
        pool_pre_ping=True,
    )

    try:
        yield engine
    finally:
        engine.dispose()