
from getpass import getpass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.security import hash_password
from app.database.session import SessionLocal
from app.models.user import User


def main():
    print("=== Vanguard AI - Create Test User ===")

    email = input("Enter test email: ").strip().lower()
    full_name = input("Enter full name: ").strip()

    if not email or not full_name:
        print("Email and full name are required.")
        return

    password = getpass("Enter test password: ")
    confirm_password = getpass("Confirm test password: ")

    if password != confirm_password:
        print("Passwords do not match.")
        return

    if len(password) < 12:
        print("Use a password with at least 12 characters.")
        return

    db = SessionLocal()

    try:
        existing_user = db.scalar(
            select(User).where(User.email == email)
        )

        if existing_user:
            print("A user with this email already exists.")
            return

        new_user = User(
            email=email,
            full_name=full_name,
            password_hash=hash_password(password),
            is_active=True,
            email_verified=False,
        )

        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        print("\nTest user created successfully!")
        print("User ID:", new_user.id)
        print("Email:", new_user.email)
        print("Active:", new_user.is_active)
        print("Email verified:", new_user.email_verified)
        print("Password stored as an Argon2id hash.")

    except IntegrityError:
        db.rollback()
        print("Could not create user. The email may already exist.")

    except Exception:
        db.rollback()
        print("User creation failed. Check the database configuration.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
