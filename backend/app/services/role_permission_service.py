from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.role_permission_catalog import ROLE_PERMISSION_CATALOG
from app.models.permission import Permission
from app.models.role import Role
from app.models.role_permission import RolePermission


def seed_role_permissions(
    db: Session,
    organization_id: UUID,
) -> int:
    """Assign catalog permissions to platform and organization roles."""

    inserted = 0

    try:
        # Fetch all permissions required by the catalog.
        permission_codes = {
            code
            for codes in ROLE_PERMISSION_CATALOG.values()
            for code in codes
        }

        permissions = db.scalars(
            select(Permission).where(
                Permission.code.in_(permission_codes)
            )
        ).all()

        permission_map = {
            permission.code: permission.id
            for permission in permissions
        }

        missing_permissions = permission_codes - permission_map.keys()

        if missing_permissions:
            raise ValueError(
                f"Missing permissions in database: "
                f"{sorted(missing_permissions)}"
            )

        # Fetch the platform and organization roles.
        roles = db.scalars(
            select(Role).where(
                (Role.organization_id.is_(None))
                | (Role.organization_id == organization_id)
            )
        ).all()

        role_map = {
            role.code: role
            for role in roles
        }

        missing_roles = set(ROLE_PERMISSION_CATALOG) - role_map.keys()

        if missing_roles:
            raise ValueError(
                f"Missing roles in database: {sorted(missing_roles)}"
            )

        # Load existing assignments to prevent duplicates.
        role_ids = [role.id for role in roles]

        existing_assignments = set(
            db.execute(
                select(
                    RolePermission.role_id,
                    RolePermission.permission_id,
                ).where(
                    RolePermission.role_id.in_(role_ids)
                )
            ).all()
        )

        # Create only missing assignments.
        for role_code, permission_codes in ROLE_PERMISSION_CATALOG.items():
            role = role_map[role_code]

            for permission_code in permission_codes:
                permission_id = permission_map[permission_code]
                assignment = (role.id, permission_id)

                if assignment in existing_assignments:
                    continue

                db.add(
                    RolePermission(
                        role_id=role.id,
                        permission_id=permission_id,
                    )
                )

                existing_assignments.add(assignment)
                inserted += 1

        db.commit()
        return inserted

    except Exception:
        db.rollback()
        raise