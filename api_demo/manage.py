"""Local-only account provisioning CLI for synthetic API sessions.

Passwords are read with getpass and never passed as command-line arguments.
"""
from __future__ import annotations

import argparse
from getpass import getpass
import sqlite3

from .auth import AuthStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic API account management")
    parser.add_argument("--db", default="priority-demo.sqlite3")
    sub = parser.add_subparsers(dest="action", required=True)
    user = sub.add_parser("create-user", help="Create a local fictional account")
    user.add_argument("--username", required=True)
    user.add_argument("--staff", action="store_true")
    args = parser.parse_args()

    if args.action == "create-user":
        password = getpass("Demo password (12+ characters): ")
        repeat = getpass("Repeat password: ")
        if password != repeat:
            parser.error("Passwords do not match")
        try:
            AuthStore(args.db).create_user(args.username, password, staff=args.staff)
        except (ValueError, sqlite3.IntegrityError) as exc:
            parser.error(str(exc))
        print(f"Created local demo account: {args.username} (staff={args.staff})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
