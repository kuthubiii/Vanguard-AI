"""SQLite engine, session factory, and request dependency."""

from collections.abc import Generator

from sqlalchemy import URL, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from aai.core.config import settings

settings.database_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    URL.create("sqlite", database=str(settings.database_path)),
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(connection, _record) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_database() -> None:
    # Import registers the model with Base.metadata before migrations run.
    from aai.db import models  # noqa: F401
    from aai.db.base import Base
    from aai.db.migrations import apply_sqlite_migrations

    apply_sqlite_migrations(engine, Base.metadata)


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
