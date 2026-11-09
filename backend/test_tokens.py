
from uuid import uuid4

from app.core.tokens import (
    create_access_token,
    create_refresh_token,
    verify_token_type,
)


def main():
    user_id = str(uuid4())

    access_token = create_access_token(user_id)
    refresh_token = create_refresh_token(user_id)

    # Correct token types
    access_payload = verify_token_type(access_token, "access")
    refresh_payload = verify_token_type(refresh_token, "refresh")

    print("Access token accepted:", access_payload["type"] == "access")
    print("Refresh token accepted:", refresh_payload["type"] == "refresh")

    # Wrong token type must be rejected
    try:
        verify_token_type(refresh_token, "access")
        print("Refresh token rejected as access: False")
    except ValueError:
        print("Refresh token rejected as access: True")

    try:
        verify_token_type(access_token, "refresh")
        print("Access token rejected as refresh: False")
    except ValueError:
        print("Access token rejected as refresh: True")


if __name__ == "__main__":
    main()
