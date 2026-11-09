from app.core.security import hash_password, verify_password


def main():
    password = "VanguardTest@2026"

    hashed = hash_password(password)

    print("Original password:", password)
    print("Generated hash:", hashed)
    print("Correct password:", verify_password(password, hashed))
    print("Wrong password:", verify_password("WrongPassword123", hashed))
    print("Hash differs from password:", hashed != password)


if __name__ == "__main__":
    main()
