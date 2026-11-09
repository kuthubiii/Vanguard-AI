
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permission_catalog import PERMISSION_CATALOG
from app.models.permission import Permission


def seed_permissions(db: Session) -> int:
    """
    Insert permissions that do not already exist.

    Returns the number of permissions inserted.
    """
    existing_codes = set(
        db.scalars(select(Permission.code)).all()
    )

    new_permissions = [
        Permission(code=code, description=description)
        for code, description in PERMISSION_CATALOG.items()
        if code not in existing_codes
    ]

    if not new_permissions:
        return 0

    db.add_all(new_permissions)
    db.commit()

    return len(new_permissions)
