"""Synthetic priority-access REST API for portfolio review.

This is NOT the proprietary Discord bot and never calls a game server or payment API.
Members and staff are provisioned locally; HTTP uses expiring bearer sessions.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import sqlite3
from typing import Callable, Iterator

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from .auth import AuthStore, Principal

STEAM_ID_PATTERN = re.compile(r"^7656119[0-9]{10}$")
bearer_scheme = HTTPBearer(auto_error=False)


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steam_id: str = Field(min_length=17, max_length=17)
    reason: str = Field(min_length=5, max_length=500)

    def normalized(self) -> "RequestBody":
        if not STEAM_ID_PATTERN.fullmatch(self.steam_id):
            raise HTTPException(422, detail="Invalid SteamID64 syntax (synthetic demo)")
        reason = self.reason.strip()
        if len(reason) < 5:
            raise HTTPException(422, detail="Reason must contain at least 5 non-space characters")
        return RequestBody(steam_id=self.steam_id, reason=reason)


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=300)


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=48)
    password: str = Field(min_length=1, max_length=128)


class StoreError(Exception):
    def __init__(self, code: int, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


class AccessStore:
    """Transactional SQLite store for synthetic approval and local entitlements."""

    def __init__(self, db_path: str | Path, now: Callable[[], datetime] | None = None):
        self.db_path = str(db_path)
        if self.db_path == ":memory:":
            raise ValueError("Use a file-backed database so each request shares state")
        self.now = now or (lambda: datetime.now(timezone.utc))
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner TEXT NOT NULL,
                    steam_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
                    review_reason TEXT,
                    reviewed_by TEXT,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS unique_pending_request
                    ON requests(owner, steam_id) WHERE status='pending';
                CREATE TABLE IF NOT EXISTS entitlements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id INTEGER NOT NULL UNIQUE REFERENCES requests(id),
                    owner TEXT NOT NULL,
                    steam_id TEXT NOT NULL,
                    granted_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id INTEGER NOT NULL REFERENCES requests(id),
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details TEXT NOT NULL,
                    at TEXT NOT NULL
                );
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _time(self) -> datetime:
        current = self.now()
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("Clock must return a timezone-aware datetime")
        return current.astimezone(timezone.utc)

    @staticmethod
    def _request(db: sqlite3.Connection, request_id: int) -> dict:
        row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            raise StoreError(404, "Request not found")
        return dict(row)

    @staticmethod
    def _event(db: sqlite3.Connection, request_id: int, actor: str,
               action: str, details: str, timestamp: str) -> None:
        db.execute(
            "INSERT INTO audit_events(request_id,actor,action,details,at) VALUES (?,?,?,?,?)",
            (request_id, actor, action, details, timestamp),
        )

    @staticmethod
    def _visible(request: dict, principal: Principal) -> dict:
        if not principal.is_staff and request["owner"] != principal.id:
            raise StoreError(404, "Request not found")
        return request

    def submit(self, principal: Principal, body: RequestBody) -> tuple[dict, bool]:
        timestamp = self._time().isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT id FROM requests WHERE owner=? AND steam_id=? AND status='pending'",
                (principal.id, body.steam_id),
            ).fetchone()
            if existing:
                return self._request(db, existing["id"]), False

            cursor = db.execute(
                """INSERT INTO requests
                   (owner,steam_id,reason,status,created_at)
                   VALUES (?,?,?,'pending',?)""",
                (principal.id, body.steam_id, body.reason, timestamp),
            )
            request_id = cursor.lastrowid
            self._event(db, request_id, principal.id, "submitted", "", timestamp)
            return self._request(db, request_id), True

    def get(self, principal: Principal, request_id: int) -> dict:
        with self._connect() as db:
            return self._visible(self._request(db, request_id), principal)

    def list(self, principal: Principal, limit: int, offset: int) -> list[dict]:
        if not principal.is_staff:
            raise StoreError(403, "Staff permission required")
        with self._connect() as db:
            return [
                dict(row) for row in db.execute(
                    "SELECT * FROM requests ORDER BY id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]

    def approve(self, principal: Principal, request_id: int) -> dict:
        if not principal.is_staff:
            raise StoreError(403, "Staff permission required")
        now = self._time()
        timestamp = now.isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            request = self._request(db, request_id)
            if request["status"] == "rejected":
                raise StoreError(409, "Rejected request cannot be approved")
            if request["status"] == "approved":
                return request  # idempotent: no additional entitlement or event

            db.execute(
                """UPDATE requests SET status='approved', reviewed_by=?, reviewed_at=?
                   WHERE id=?""",
                (principal.id, timestamp, request_id),
            )
            db.execute(
                """INSERT INTO entitlements
                   (request_id,owner,steam_id,granted_at,expires_at)
                   VALUES (?,?,?,?,?)""",
                (request_id, request["owner"], request["steam_id"],
                 timestamp, (now + timedelta(hours=3)).isoformat()),
            )
            self._event(db, request_id, principal.id, "approved",
                        "Local synthetic entitlement only", timestamp)
            return self._request(db, request_id)

    def reject(self, principal: Principal, request_id: int, reason: str) -> dict:
        if not principal.is_staff:
            raise StoreError(403, "Staff permission required")
        reason = reason.strip()
        if len(reason) < 3:
            raise StoreError(422, "Rejection reason is too short")
        timestamp = self._time().isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            request = self._request(db, request_id)
            if request["status"] == "approved":
                raise StoreError(409, "Approved request cannot be rejected")
            if request["status"] == "rejected":
                return request

            db.execute(
                """UPDATE requests SET status='rejected', review_reason=?,
                   reviewed_by=?, reviewed_at=? WHERE id=?""",
                (reason, principal.id, timestamp, request_id),
            )
            self._event(db, request_id, principal.id, "rejected", reason, timestamp)
            return self._request(db, request_id)

    def events(self, principal: Principal, request_id: int) -> list[dict]:
        with self._connect() as db:
            self._visible(self._request(db, request_id), principal)
            return [
                dict(row) for row in db.execute(
                    """SELECT actor,action,details,at FROM audit_events
                       WHERE request_id=? ORDER BY id""",
                    (request_id,),
                )
            ]

    def entitlements(self, principal: Principal) -> list[dict]:
        now = self._time().isoformat()
        with self._connect() as db:
            rows = db.execute(
                """SELECT request_id,owner,steam_id,granted_at,expires_at
                   FROM entitlements WHERE owner=? ORDER BY id DESC""",
                (principal.id,),
            ).fetchall()
            return [
                {**dict(row), "status": "active" if row["expires_at"] > now else "expired"}
                for row in rows
            ]



def create_app(
    db_path: str | Path,
    now: Callable[[], datetime] | None = None,
) -> FastAPI:
    """Synthetic API with locally provisioned accounts and revocable sessions."""
    store = AccessStore(db_path, now)
    auth = AuthStore(db_path, now)
    app = FastAPI(
        title="Synthetic Priority Access API",
        description="Local only; no external Steam, Discord or entitlement delivery.",
        version="2.0.0",
    )

    def token_from_header(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    ) -> str:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=401, detail="Bearer session required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return credentials.credentials

    def current_principal(token: str = Depends(token_from_header)) -> Principal:
        principal = auth.resolve(token)
        if principal is None:
            raise HTTPException(
                status_code=401, detail="Session invalid or expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return principal

    def guarded(action):
        try:
            return action()
        except StoreError as exc:
            raise HTTPException(exc.code, detail=exc.detail) from exc

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "synthetic"}

    @app.post("/auth/login")
    def login(body: LoginBody):
        result = auth.authenticate(body.username, body.password)
        if result is None:
            raise HTTPException(
                status_code=401, detail="Invalid credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token, expires_at = result
        return {"access_token": token, "token_type": "bearer", "expires_at": expires_at}

    @app.get("/auth/me")
    def me(actor: Principal = Depends(current_principal)):
        return {"id": actor.id, "is_staff": actor.is_staff}

    @app.post("/auth/logout")
    def logout(
        token: str = Depends(token_from_header),
        actor: Principal = Depends(current_principal),
    ):
        auth.revoke(token)
        return {"status": "revoked"}

    @app.post("/requests")
    def submit(
        body: RequestBody,
        response: Response,
        actor: Principal = Depends(current_principal),
    ):
        request, created = guarded(lambda: store.submit(actor, body.normalized()))
        response.status_code = 201 if created else 200
        return {"created": created, "request": request}

    @app.get("/requests")
    def list_requests(
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        actor: Principal = Depends(current_principal),
    ):
        return guarded(lambda: store.list(actor, limit, offset))

    @app.get("/requests/{request_id}")
    def get_request(request_id: int, actor: Principal = Depends(current_principal)):
        return guarded(lambda: store.get(actor, request_id))

    @app.post("/requests/{request_id}/approve")
    def approve(request_id: int, actor: Principal = Depends(current_principal)):
        return guarded(lambda: store.approve(actor, request_id))

    @app.post("/requests/{request_id}/reject")
    def reject(
        request_id: int,
        body: RejectBody,
        actor: Principal = Depends(current_principal),
    ):
        return guarded(lambda: store.reject(actor, request_id, body.reason))

    @app.get("/requests/{request_id}/events")
    def events(request_id: int, actor: Principal = Depends(current_principal)):
        return guarded(lambda: store.events(actor, request_id))

    @app.get("/entitlements/me")
    def my_entitlements(actor: Principal = Depends(current_principal)):
        return store.entitlements(actor)

    return app


def create_app_from_env() -> FastAPI:
    """Uvicorn factory: accounts must be explicitly provisioned through CLI."""
    db_path = os.environ.get("DEMO_DB_PATH", "priority-demo.sqlite3")
    return create_app(db_path)
