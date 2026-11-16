"""Local operator-only provisioning. Never passes a password through CLI args."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from getpass import getpass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import build_engine
from .security import create_user


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision a fictional local API account")
    parser.add_argument("username")
    parser.add_argument("--staff", action="store_true")
    args = parser.parse_args()
    password = getpass("Password (12+ chars): ")
    if password != getpass("Confirm password: "):
        parser.error("Passwords differ")
    try:
        with Session(build_engine()) as db, db.begin():
            create_user(db, args.username, password, datetime.now(timezone.utc), staff=args.staff)
    except (ValueError, IntegrityError) as exc:
        parser.error("Cannot create user: invalid input or username already exists")
    print("Created fictional user:", args.username)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
