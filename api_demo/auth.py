"""Server-side local session authentication for the *synthetic* FastAPI demo.

Users are provisioned outside HTTP. Passwords use salted scrypt hashes; randomly
generated bearer tokens are hashed at rest and checked on every request.
This is a teaching example, not an Internet-facing identity provider.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from pathlib import Path
import secrets
import sqlite3
from typing import Callable, Iterator


@dataclass(frozen=True)
class Principal:
    id: str
    is_staff: bool = False


class AuthStore:
    """SQLite-backed local identities, short-lived sessions and revocation."""

    def __init__(self, path: str | Path,
                 now: Callable[[], datetime] | None = None):
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("Use a file-backed database")
        self.now = now or (lambda: datetime.now(timezone.utc))
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS demo_users (
                    username TEXT PRIMARY KEY,
                    salt BLOB NOT NULL,
                    digest BLOB NOT NULL,
                    staff INTEGER NOT NULL DEFAULT 0 CHECK(staff IN (0,1)),
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS demo_sessions (
                    token_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL REFERENCES demo_users(username),
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_demo_sessions_expiry
                    ON demo_sessions(expires_at);
            """)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _utcnow(self) -> datetime:
        now = self.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Clock must return a timezone-aware instant")
        return now.astimezone(timezone.utc)

    @staticmethod
    def _digest(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt,
                             n=2**14, r=8, p=1, dklen=32)

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _username(value: str) -> str:
        if not isinstance(value, str) or not (
            3 <= len(value) <= 48
        ) or not all(c.isascii() and (c.isalnum() or c in "_-") for c in value):
            raise ValueError("Username must be 3–48 ASCII letters, digits, '_' or '-'")
        return value

    def create_user(self, username: str, password: str, staff: bool = False) -> None:
        username = self._username(username)
        if not isinstance(password, str) or not 12 <= len(password) <= 128:
            raise ValueError("Password must be 12–128 characters")
        if type(staff) is not bool:
            raise ValueError("Staff flag must be boolean")
        salt = secrets.token_bytes(16)
        digest = self._digest(password, salt)
        with self._db() as db:
            db.execute(
                "INSERT INTO demo_users(username,salt,digest,staff) VALUES(?,?,?,?)",
                (username, salt, digest, int(staff)),
            )

    def authenticate(self, username: str, password: str) -> tuple[str, str] | None:
        """Return raw bearer token only on successful login. Never persist the token."""
        if not isinstance(username, str) or not isinstance(password, str):
            return None
        with self._db() as db:
            user = db.execute("SELECT * FROM demo_users WHERE username=?",
                              (username,)).fetchone()
        # A dummy derivation avoids a fast, obvious username-existence oracle.
        salt = user["salt"] if user else b"\x00" * 16
        computed = self._digest(password, salt)
        expected = user["digest"] if user else b"\x00" * 32
        if not hmac.compare_digest(computed, expected) or not user or not user["enabled"]:
            return None
        token = secrets.token_urlsafe(32)
        expiry = (self._utcnow() + timedelta(minutes=60)).isoformat()
        with self._db() as db:
            db.execute(
                "INSERT INTO demo_sessions(token_hash,username,expires_at) VALUES(?,?,?)",
                (self._token_hash(token), username, expiry),
            )
        return token, expiry

    def resolve(self, token: str) -> Principal | None:
        if not isinstance(token, str) or not 20 <= len(token) <= 200:
            return None
        with self._db() as db:
            row = db.execute("""
                SELECT u.username,u.staff,u.enabled,s.expires_at
                FROM demo_sessions s JOIN demo_users u ON u.username=s.username
                WHERE s.token_hash=?
            """, (self._token_hash(token),)).fetchone()
        if not row or not row["enabled"] or row["expires_at"] <= self._utcnow().isoformat():
            return None
        return Principal(row["username"], bool(row["staff"]))

    def revoke(self, token: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM demo_sessions WHERE token_hash=?",
                       (self._token_hash(token),))

    def set_staff(self, username: str, value: bool) -> None:
        """Administrative local fixture hook; intentionally not an HTTP endpoint."""
        if type(value) is not bool:
            raise ValueError("Staff flag must be boolean")
        with self._db() as db:
            cursor = db.execute(
                "UPDATE demo_users SET staff=? WHERE username=?",
                (int(value), self._username(username)),
            )
            if cursor.rowcount != 1:
                raise ValueError("Unknown user")

    def disable_user(self, username: str) -> None:
        """Sessions stop working immediately because identity is rechecked."""
        with self._db() as db:
            cursor = db.execute(
                "UPDATE demo_users SET enabled=0 WHERE username=?",
                (self._username(username),),
            )
            if cursor.rowcount != 1:
                raise ValueError("Unknown user")
