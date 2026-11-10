
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main
from app.core.security import hash_password
from app.core.tokens import create_access_token, create_refresh_token
from app.database.session import get_db
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.refresh_token_service import (
    hash_refresh_token,
    store_refresh_token,
)


def create_test_user(db):
    """Create a test user in the isolated test database."""

    user = User(
        id=uuid4(),
        email=f"{uuid4().hex}@example.com",
        full_name="Refresh Token Test User",
        password_hash=hash_password("TestPassword123!"),
        is_active=True,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user


def test_valid_refresh_token_is_stored(db_session):
    user = create_test_user(db_session)
    token = create_refresh_token(str(user.id))

    record = store_refresh_token(
        db=db_session,
        user_id=user.id,
        refresh_token=token,
    )

    db_record = db_session.scalar(
        select(RefreshToken).where(
            RefreshToken.id == record.id
        )
    )

    assert db_record is not None
    assert db_record.user_id == user.id
    assert db_record.token_hash == hash_refresh_token(token)
    assert db_record.token_hash != token
    assert db_record.revoked_at is None
    assert db_record.expires_at is not None


def test_invalid_refresh_token_is_rejected(db_session):
    user = create_test_user(db_session)

    with pytest.raises(jwt.InvalidTokenError):
        store_refresh_token(
            db=db_session,
            user_id=user.id,
            refresh_token="invalid-token",
        )


def test_refresh_token_belonging_to_another_user_is_rejected(
    db_session,
):
    user = create_test_user(db_session)
    another_user_id = uuid4()

    token = create_refresh_token(str(user.id))

    with pytest.raises(
        ValueError,
        match="Refresh token user does not match",
    ):
        store_refresh_token(
            db=db_session,
            user_id=another_user_id,
            refresh_token=token,
        )


def test_access_token_cannot_be_stored_as_refresh_token(
    db_session,
):
    user = create_test_user(db_session)
    access_token = create_access_token(str(user.id))

    with pytest.raises(
        ValueError,
        match="Invalid token type",
    ):
        store_refresh_token(
            db=db_session,
            user_id=user.id,
            refresh_token=access_token,
        )


def test_login_stores_refresh_token_hash(db_session):
    """Verify that successful API login persists a hashed refresh token."""

    user = create_test_user(db_session)

    def override_get_db():
        yield db_session

    main.app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(main.app) as client:
            response = client.post(
                "/api/v1/auth/login",
                data={
                    "username": user.email,
                    "password": "TestPassword123!",
                },
            )

        assert response.status_code == 200

        response_data = response.json()

        assert "access_token" in response_data
        assert "refresh_token" in response_data
        assert response_data["token_type"] == "bearer"

        raw_refresh_token = response_data["refresh_token"]

        stored_record = db_session.scalar(
            select(RefreshToken).where(
                RefreshToken.user_id == user.id
            )
        )

        assert stored_record is not None
        assert stored_record.token_hash == hash_refresh_token(
            raw_refresh_token
        )
        assert stored_record.token_hash != raw_refresh_token
        assert stored_record.revoked_at is None

    finally:
        main.app.dependency_overrides.pop(get_db, None)


def test_refresh_endpoint_rotates_token(db_session):
    """A valid refresh token should produce a new token pair."""

    user = create_test_user(db_session)
    old_token = create_refresh_token(str(user.id))

    old_record = store_refresh_token(
        db=db_session,
        user_id=user.id,
        refresh_token=old_token,
    )
    family_id = old_record.family_id
    db_session.commit()

    def override_get_db():
        yield db_session

    main.app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(main.app) as client:
            response = client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": old_token},
            )

        assert response.status_code == 200

        response_data = response.json()

        assert response_data["access_token"]
        assert response_data["refresh_token"]
        assert response_data["refresh_token"] != old_token
        assert response_data["token_type"] == "bearer"

        db_session.expire_all()

        old_record = db_session.scalar(
            select(RefreshToken).where(
                RefreshToken.token_hash == hash_refresh_token(old_token)
            )
        )

        new_record = db_session.scalar(
            select(RefreshToken).where(
                RefreshToken.token_hash
                == hash_refresh_token(response_data["refresh_token"])
            )
        )

        assert old_record.revoked_at is not None
        assert new_record is not None
        assert new_record.revoked_at is None
        assert new_record.family_id == family_id

    finally:
        main.app.dependency_overrides.pop(get_db, None)


def test_reusing_old_refresh_token_revokes_token_family(db_session):
    """Reusing a revoked token should revoke all active family tokens."""

    user = create_test_user(db_session)

    old_token = create_refresh_token(str(user.id))
    old_record = store_refresh_token(
        db=db_session,
        user_id=user.id,
        refresh_token=old_token,
    )

    family_id = old_record.family_id

    def override_get_db():
        yield db_session

    main.app.dependency_overrides[get_db] = override_get_db

    try:
        # First request rotates the token successfully.
        with TestClient(main.app) as client:
            first_response = client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": old_token},
            )

            assert first_response.status_code == 200

            new_token = first_response.json()["refresh_token"]

            # Second request reuses the old token.
            replay_response = client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": old_token},
            )

        assert replay_response.status_code == 401

        # Verify that every token in this family is revoked.
        db_session.expire_all()

        family_records = db_session.scalars(
            select(RefreshToken).where(
                RefreshToken.family_id == family_id
            )
        ).all()

        assert len(family_records) == 2

        for record in family_records:
            assert record.revoked_at is not None

        assert new_token

    finally:
        main.app.dependency_overrides.pop(get_db, None)


def test_inactive_user_cannot_refresh_token(db_session):
    """An inactive account should have its token family revoked."""

    user = create_test_user(db_session)

    refresh_token = create_refresh_token(str(user.id))

    token_record = store_refresh_token(
        db=db_session,
        user_id=user.id,
        refresh_token=refresh_token,
    )

    db_session.commit()

    user.is_active = False
    db_session.commit()

    def override_get_db():
        yield db_session

    main.app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(main.app) as client:
            response = client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": refresh_token},
            )

        assert response.status_code == 401

        db_session.expire_all()

        updated_record = db_session.scalar(
            select(RefreshToken).where(
                RefreshToken.id == token_record.id
            )
        )

        assert updated_record.revoked_at is not None

    finally:
        main.app.dependency_overrides.pop(get_db, None)


def test_invalid_refresh_token_endpoint_returns_401(db_session):
    """An invalid refresh token should be rejected."""

    def override_get_db():
        yield db_session

    main.app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(main.app) as client:
            response = client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": "invalid-refresh-token"},
            )

        assert response.status_code == 401

    finally:
        main.app.dependency_overrides.pop(get_db, None)
