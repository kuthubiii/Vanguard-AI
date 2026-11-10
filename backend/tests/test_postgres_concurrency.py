
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.core.tokens import create_refresh_token
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.refresh_token_service import (
    rotate_refresh_token,
    store_refresh_token,
)

@pytest.mark.postgres
def test_concurrent_refresh_requests_revoke_token_family(
    postgres_engine,
):
    """
    Two simultaneous refresh requests using the same token
    should trigger strict token reuse detection.
    """

    testing_session_local = sessionmaker(
        bind=postgres_engine,
        autoflush=False,
        autocommit=False,
    )

    # 1. Create a test user and initial refresh token.
    with testing_session_local() as db:
        user = User(
            id=uuid4(),
            email=f"{uuid4().hex}@example.com",
            full_name="PostgreSQL Concurrency Test User",
            password_hash=hash_password("TestPassword123!"),
            is_active=True,
        )

        db.add(user)
        db.commit()

        user_id = user.id

        original_token = create_refresh_token(
            str(user_id)
        )

        original_record = store_refresh_token(
            db=db,
            user_id=user_id,
            refresh_token=original_token,
        )

        family_id = original_record.family_id
        db.commit()

    # 2. Synchronize both requests before token rotation.
    barrier = Barrier(2)

    def refresh_request():
        with testing_session_local() as db:
            barrier.wait(timeout=15)

            result = rotate_refresh_token(
                db=db,
                raw_refresh_token=original_token,
            )

            db.commit()

            return result

    try:
        # 3. Execute two independent requests concurrently.
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(refresh_request),
                executor.submit(refresh_request),
            ]

            results = [
                future.result(timeout=30)
                for future in futures
            ]

        # 4. Exactly one request should rotate successfully.
        successful_results = [
            result
            for result in results
            if result.access_token and result.refresh_token
        ]

        reuse_results = [
            result
            for result in results
            if result.reuse_detected
        ]

        assert len(successful_results) == 1
        assert len(reuse_results) == 1

        # 5. Verify that the entire token family was revoked.
        with testing_session_local() as db:
            family_records = db.scalars(
                select(RefreshToken).where(
                    RefreshToken.family_id == family_id
                )
            ).all()

            assert len(family_records) == 2

            for record in family_records:
                assert record.revoked_at is not None

    finally:
        # 6. Remove this test's user and associated tokens.
        with testing_session_local() as db:
            test_user = db.get(User, user_id)

            if test_user is not None:
                db.delete(test_user)
                db.commit()