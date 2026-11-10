
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.tokens import (
    create_access_token,
    create_refresh_token,
    verify_token_type,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User


def hash_refresh_token(token: str) -> str:
    """Return the SHA-256 hash of a refresh token."""

    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def store_refresh_token(
    db: Session,
    user_id: uuid.UUID,
    refresh_token: str,
    family_id: uuid.UUID | None = None,
) -> RefreshToken:
    """Validate and store a hashed refresh token."""

    payload = verify_token_type(
        refresh_token,
        expected_type="refresh",
    )

    try:
        token_user_id = uuid.UUID(payload["sub"])
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("Invalid refresh token subject") from exc

    if token_user_id != user_id:
        raise ValueError("Refresh token user does not match")

    expires_at = datetime.fromtimestamp(
        payload["exp"],
        tz=timezone.utc,
    )

    refresh_token_record = RefreshToken(
        user_id=user_id,
        token_hash=hash_refresh_token(refresh_token),
        family_id=family_id or uuid.uuid4(),
        expires_at=expires_at,
    )

    db.add(refresh_token_record)
    db.flush()

    return refresh_token_record


@dataclass(frozen=True)
class RefreshRotationResult:
    """Result returned by the refresh-token rotation process."""

    access_token: str | None = None
    refresh_token: str | None = None
    reuse_detected: bool = False
    account_inactive: bool = False


def revoke_token_family(
    db: Session,
    family_id: uuid.UUID,
    revoked_at: datetime,
) -> None:
    """Revoke every active refresh token in a token family."""

    db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=revoked_at)
    )


def rotate_refresh_token(
    db: Session,
    raw_refresh_token: str,
) -> RefreshRotationResult:
    """
    Rotate a refresh token using strict token rotation.

    A valid token is revoked and replaced with a new token
    belonging to the same token family.

    Reuse of a revoked token revokes every active token
    belonging to that family.

    Inactive accounts also have their token family revoked.

    This function does not commit the transaction.
    The API route controls transaction commits.
    """

    # 1. Validate the JWT.
    payload = verify_token_type(
        raw_refresh_token,
        expected_type="refresh",
    )

    # 2. Validate the user ID inside the token.
    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("Invalid refresh token subject") from exc

    # 3. Hash the raw token before database lookup.
    token_hash = hash_refresh_token(raw_refresh_token)

    # 4. Retrieve and lock the matching database record.
    # populate_existing=True prevents stale ORM identity-map data.
    token_record = db.scalar(
        select(RefreshToken)
        .where(
            RefreshToken.token_hash == token_hash
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if token_record is None or token_record.user_id != user_id:
        raise ValueError("Refresh token not recognized")

    now = datetime.now(timezone.utc)

    # 5. Check token expiration.
    expires_at = token_record.expires_at

    # SQLite can return a naive datetime.
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(
            tzinfo=timezone.utc
        )

    if expires_at <= now:
        raise ValueError("Refresh token expired")

    # 6. Detect reuse of a revoked token.
    if token_record.revoked_at is not None:

        revoke_token_family(
            db=db,
            family_id=token_record.family_id,
            revoked_at=now,
        )

        # Caller must commit this revocation before returning 401.
        return RefreshRotationResult(
            reuse_detected=True
        )

    # 7. Check whether the user account is active.
    user = db.scalar(
        select(User).where(User.id == user_id)
    )

    if user is None or not user.is_active:

        revoke_token_family(
            db=db,
            family_id=token_record.family_id,
            revoked_at=now,
        )

        return RefreshRotationResult(
            account_inactive=True
        )

    # 8. Revoke the old refresh token.
    token_record.revoked_at = now

    # 9. Generate a new access token.
    new_access_token = create_access_token(
        str(user.id)
    )

    # 10. Generate a new refresh token.
    new_refresh_token = create_refresh_token(
        str(user.id)
    )

    # 11. Store the new token in the original token family.
    store_refresh_token(
        db=db,
        user_id=user.id,
        refresh_token=new_refresh_token,
        family_id=token_record.family_id,
    )

    # 12. Return the new token pair.
    return RefreshRotationResult(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
    )
