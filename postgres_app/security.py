"""Local account provisioning and opaque bearer sessions for the PostgreSQL sample."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import hmac
import re
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import SessionToken, User

USER_RE = re.compile(r"^[A-Za-z0-9_-]{3,48}$")


def password_digest(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)


def create_user(db: Session, username: str, password: str, now: datetime, staff: bool = False) -> User:
    if not USER_RE.fullmatch(username) or not 12 <= len(password) <= 128:
        raise ValueError("Username or password length/format invalid")
    salt = secrets.token_bytes(16)
    user = User(
        username=username,
        salt=salt,
        password_hash=password_digest(password, salt),
        is_staff=staff,
        is_active=True,
        created_at=now,
    )
    db.add(user)
    db.flush()
    return user


def login(db: Session, username: str, password: str, now: datetime) -> tuple[str, datetime] | None:
    user = db.scalar(select(User).where(User.username == username))
    salt = user.salt if user else b"\x00" * 16
    actual = password_digest(password, salt)
    expected = user.password_hash if user else b"\x00" * 32
    if not hmac.compare_digest(actual, expected) or user is None or not user.is_active:
        return None
    token = secrets.token_urlsafe(32)
    expires = now + timedelta(minutes=30)
    db.add(SessionToken(
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        user_id=user.id,
        created_at=now,
        expires_at=expires,
    ))
    return token, expires


def resolve(db: Session, token: str, now: datetime) -> User | None:
    if not 20 <= len(token) <= 200:
        return None
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    stmt = (
        select(User)
        .join(SessionToken, SessionToken.user_id == User.id)
        .where(SessionToken.token_hash == digest,
               SessionToken.expires_at > now,
               User.is_active.is_(True))
    )
    return db.scalar(stmt)


def logout(db: Session, token: str) -> None:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = db.scalar(select(SessionToken).where(SessionToken.token_hash == digest))
    if row:
        db.delete(row)
