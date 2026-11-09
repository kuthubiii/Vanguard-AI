
import uuid

from app.database.session import SessionLocal
from app.services.role_service import (
    seed_platform_roles,
    seed_organization_roles,
)

# Existing ABC Logistics organization ID
ORGANIZATION_ID = uuid.UUID(
    "7f48fed2-bfe1-4e56-a266-ccdf8678dae3"
)


def main():
    db = SessionLocal()

    try:
        platform_count = seed_platform_roles(db)
        organization_count = seed_organization_roles(
            db,
            ORGANIZATION_ID,
        )

        print(f"Platform roles inserted: {platform_count}")
        print(f"Organization roles inserted: {organization_count}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
