"""Print server deployment secrets without persisting the plaintext password."""

from __future__ import annotations

import getpass
import secrets

from argon2 import PasswordHasher


def main() -> None:
    password = getpass.getpass("Administrator password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if not password or password != confirmation:
        raise SystemExit("Passwords are empty or do not match.")
    password_hash = PasswordHasher().hash(password)
    print(f"XIVPULSE_AUTH_PASSWORD_HASH='{password_hash}'")
    print(f"XIVPULSE_AUTH_SESSION_SECRET={secrets.token_urlsafe(48)}")


if __name__ == "__main__":
    main()
