
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.role_catalog import ROLE_CATALOG
from app.models.role import Role


def seed_platform_roles(db: Session) -> int:
    """Create missing platform-level system roles."""

    inserted = 0

    try:
        for code, details in ROLE_CATALOG.items():
            if details["scope"] != "platform":
                continue

            existing_role = db.scalar(
                select(Role).where(
                    Role.code == code,
                    Role.organization_id.is_(None),
                )
            )

            if existing_role:
                continue

            role = Role(
                id=uuid.uuid4(),
                organization_id=None,
                code=code,
                name=details["name"],
                scope=details["scope"],
                description=details["description"],
                is_system=True,
            )

            db.add(role)
            inserted += 1

        if inserted:
            db.commit()

        return inserted

    except Exception:
        db.rollback()
        raise

def seed_organization_roles(
    db: Session,
    organization_id: uuid.UUID,
) -> int:
    """Create missing organization-specific system roles."""

    inserted = 0

    try:
        for code, details in ROLE_CATALOG.items():
            if details["scope"] == "platform":
                continue

            existing_role = db.scalar(
                select(Role).where(
                    Role.code == code,
                    Role.organization_id == organization_id,
                )
            )

            if existing_role:
                continue

            role = Role(
                organization_id=organization_id,
                code=code,
                name=details["name"],
                scope=details["scope"],
                description=details["description"],
                is_system=True,
            )

            db.add(role)
            inserted += 1

        if inserted:
            db.commit()

        return inserted

    except Exception:
        db.rollback()
        raise
