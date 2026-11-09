import uuid

from app.database.session import SessionLocal
from app.services.role_permission_service import seed_role_permissions


# Existing ABC Logistics organization ID
ORGANIZATION_ID = uuid.UUID(
    "7f48fed2-bfe1-4e56-a266-ccdf8678dae3"
)


def main():
    db = SessionLocal()

    try:
        inserted = seed_role_permissions(
            db,
            ORGANIZATION_ID,
        )

        print(f"New role-permission assignments: {inserted}")

    finally:
        db.close()


if __name__ == "__main__":
    main()