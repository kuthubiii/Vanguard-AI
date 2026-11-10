from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user
from app.core.security import hash_password
from app.core.tokens import create_access_token, create_refresh_token
from app.database.base import Base
from app.models.user import User


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    testing_session_local = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    Base.metadata.create_all(engine)
    db = testing_session_local()

    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def create_test_user(db, is_active=True):
    user = User(
        id=uuid4(),
        email="test@example.com",
        full_name="Test User",
        password_hash=hash_password("TestPassword123!"),
        is_active=is_active,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user


def test_valid_access_token_returns_active_user(db_session):
    user = create_test_user(db_session)
    token = create_access_token(str(user.id))

    authenticated_user = get_current_user(
        token=token,
        db=db_session,
    )

    assert authenticated_user.id == user.id


def test_refresh_token_is_rejected(db_session):
    user = create_test_user(db_session)
    token = create_refresh_token(str(user.id))

    with pytest.raises(HTTPException) as exc:
        get_current_user(token=token, db=db_session)

    assert exc.value.status_code == 401


def test_inactive_user_is_rejected(db_session):
    user = create_test_user(db_session, is_active=False)
    token = create_access_token(str(user.id))

    with pytest.raises(HTTPException) as exc:
        get_current_user(token=token, db=db_session)

    assert exc.value.status_code == 401


def test_nonexistent_user_is_rejected(db_session):
    token = create_access_token(str(uuid4()))

    with pytest.raises(HTTPException) as exc:
        get_current_user(token=token, db=db_session)

    assert exc.value.status_code == 401