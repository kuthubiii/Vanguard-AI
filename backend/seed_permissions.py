
from app.database.session import SessionLocal
from app.services.permission_service import seed_permissions


def main():
    db = SessionLocal()

    try:
        inserted = seed_permissions(db)

        print(f"New permissions inserted: {inserted}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
