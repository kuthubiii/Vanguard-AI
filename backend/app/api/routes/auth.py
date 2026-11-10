
import jwt

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.core.tokens import create_access_token, create_refresh_token
from app.database.session import get_db
from app.models.user import User
from app.services.refresh_token_service import (
    rotate_refresh_token,
    store_refresh_token,
)


router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)


class RefreshRequest(BaseModel):
    """Request body for refreshing an authentication session."""

    refresh_token: str = Field(min_length=1)


@router.post("/login")
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """Authenticate a user and issue JWT tokens."""

    user = db.scalar(
        select(User).where(
            User.email == form_data.username.strip().lower()
        )
    )

    # Validate user credentials
    if (
        user is None
        or not user.is_active
        or not verify_password(
            form_data.password,
            user.password_hash,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        # Generate access and refresh tokens
        access_token = create_access_token(str(user.id))
        refresh_token = create_refresh_token(str(user.id))

        # Store only the hashed refresh token in the database
        store_refresh_token(
            db=db,
            user_id=user.id,
            refresh_token=refresh_token,
        )

        # Commit the token record
        db.commit()

    except Exception:
        # Roll back database changes if token storage fails
        db.rollback()
        raise

    # Return tokens to the authenticated client
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post("/refresh")
def refresh_session(
    request: RefreshRequest,
    db: Session = Depends(get_db),
):
    """Rotate a refresh token and issue a new token pair."""

    try:
        # Validate and rotate the existing refresh token
        result = rotate_refresh_token(
            db=db,
            raw_refresh_token=request.refresh_token,
        )

    except (jwt.InvalidTokenError, ValueError):
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    # Handle reuse of an already-revoked refresh token
    if result.reuse_detected:
        try:
            # Commit family revocation before returning 401
            db.commit()
        except Exception:
            db.rollback()
            raise

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    # Handle inactive user accounts
    if result.account_inactive:
        try:
            # Commit family revocation before returning 401
            db.commit()
        except Exception:
            db.rollback()
            raise

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    # Ensure the rotation returned a complete token pair
    if not result.access_token or not result.refresh_token:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    try:
        # Atomically commit old-token revocation and new-token storage
        db.commit()

    except Exception:
        db.rollback()
        raise

    # Return the newly generated token pair
    return {
        "access_token": result.access_token,
        "refresh_token": result.refresh_token,
        "token_type": "bearer",
    }
