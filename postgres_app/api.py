"""A deployable *synthetic* FastAPI/PostgreSQL workflow.

Does not connect to Steam, Discord, billing, or production game-server APIs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Response, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .db import build_engine
from .models import AccessRequest, AuditEvent, Entitlement, User
from .security import login, logout, resolve

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Actor:
    id: int
    username: str
    staff: bool


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=48)
    password: str = Field(min_length=1, max_length=128)


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steam_id: str = Field(min_length=17, max_length=17, pattern=r"^7656119[0-9]{10}$")
    reason: str = Field(min_length=5, max_length=500)


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=300)


def request_dict(row: AccessRequest) -> dict:
    return {
        "id": row.id, "owner_id": row.owner_id, "steam_id": row.steam_id,
        "reason": row.reason, "status": row.status, "created_at": row.created_at,
        "reviewed_by_id": row.reviewed_by_id, "reviewed_at": row.reviewed_at,
        "review_reason": row.review_reason,
    }


def create_app(
    engine: Engine | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    engine = engine if engine is not None else build_engine()
    factory = sessionmaker(engine, expire_on_commit=False)
    now = clock or (lambda: datetime.now(timezone.utc))
    app = FastAPI(
        title="PostgreSQL Priority Workflow — Synthetic Showcase",
        version="1.0.0",
        description="Deployable educational API; never grants real server access.",
    )

    def token_from_header(
        credentials: HTTPAuthorizationCredentials | None = Security(bearer),
    ) -> str:
        if not credentials or credentials.scheme.lower() != "bearer":
            raise HTTPException(401, "Bearer session required", headers={"WWW-Authenticate": "Bearer"})
        return credentials.credentials

    def current_actor(token: str = Depends(token_from_header)) -> Actor:
        with factory() as db:
            user = resolve(db, token, now())
            if user is None:
                raise HTTPException(401, "Session invalid or expired", headers={"WWW-Authenticate": "Bearer"})
            return Actor(user.id, user.username, user.is_staff)

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "synthetic-postgres"}

    @app.get("/ready")
    def ready():
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise HTTPException(503, "Database unavailable") from exc
        return {"status": "ready"}

    @app.post("/auth/login")
    def authenticate(body: LoginBody, response: Response):
        with factory.begin() as db:
            result = login(db, body.username, body.password, now())
        if result is None:
            raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"})
        token, expires_at = result
        response.headers["Cache-Control"] = "no-store"
        return {"access_token": token, "token_type": "bearer", "expires_at": expires_at}

    @app.get("/auth/me")
    def me(actor: Actor = Depends(current_actor)):
        return {"id": actor.id, "username": actor.username, "is_staff": actor.staff}

    @app.post("/auth/logout")
    def revoke(token: str = Depends(token_from_header), actor: Actor = Depends(current_actor)):
        with factory.begin() as db:
            logout(db, token)
        return {"status": "revoked"}

    @app.post("/requests")
    def submit(body: RequestBody, response: Response, actor: Actor = Depends(current_actor)):
        reason = body.reason.strip()
        if len(reason) < 5:
            raise HTTPException(422, "Reason must contain at least 5 visible characters")
        try:
            with factory.begin() as db:
                existing = db.scalar(select(AccessRequest).where(
                    AccessRequest.owner_id == actor.id,
                    AccessRequest.steam_id == body.steam_id,
                    AccessRequest.status == "pending",
                ))
                if existing:
                    response.status_code = 200
                    return {"created": False, "request": request_dict(existing)}
                timestamp = now()
                item = AccessRequest(
                    owner_id=actor.id, steam_id=body.steam_id,
                    reason=reason, status="pending", created_at=timestamp,
                )
                db.add(item)
                db.flush()
                db.add(AuditEvent(
                    request_id=item.id, actor_id=actor.id, action="submitted",
                    detail="", created_at=timestamp,
                ))
                payload = request_dict(item)
            response.status_code = 201
            return {"created": True, "request": payload}
        except IntegrityError:
            # The PostgreSQL unique partial index protects against simultaneous submits.
            with factory() as db:
                existing = db.scalar(select(AccessRequest).where(
                    AccessRequest.owner_id == actor.id,
                    AccessRequest.steam_id == body.steam_id,
                    AccessRequest.status == "pending",
                ))
                if existing is None:
                    raise HTTPException(409, "Concurrent request conflict")
                response.status_code = 200
                return {"created": False, "request": request_dict(existing)}

    @app.get("/requests")
    def list_requests(
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        actor: Actor = Depends(current_actor),
    ):
        if not actor.staff:
            raise HTTPException(403, "Staff permission required")
        with factory() as db:
            result = db.scalars(
                select(AccessRequest).order_by(AccessRequest.id.desc()).limit(limit).offset(offset)
            ).all()
            return [request_dict(row) for row in result]

    @app.get("/requests/{request_id}")
    def get_request(request_id: int, actor: Actor = Depends(current_actor)):
        with factory() as db:
            row = db.get(AccessRequest, request_id)
            if row is None or (not actor.staff and row.owner_id != actor.id):
                raise HTTPException(404, "Request not found")
            return request_dict(row)

    def review(request_id: int, actor: Actor, approve: bool, reason: str = "") -> dict:
        with factory.begin() as db:
            # Check current staff role inside transaction, not only the HTTP dependency.
            reviewer = db.scalar(select(User).where(User.id == actor.id).with_for_update())
            if reviewer is None or not reviewer.is_active or not reviewer.is_staff:
                raise HTTPException(403, "Staff permission required")
            item = db.scalar(select(AccessRequest).where(
                AccessRequest.id == request_id,
            ).with_for_update())
            if item is None:
                raise HTTPException(404, "Request not found")
            target = "approved" if approve else "rejected"
            if item.status == target:
                return request_dict(item)
            if item.status != "pending":
                raise HTTPException(409, "Request already finalized")
            timestamp = now()
            item.status = target
            item.reviewed_at = timestamp
            item.reviewed_by_id = actor.id
            if not approve:
                item.review_reason = reason
            else:
                db.add(Entitlement(
                    request_id=item.id, owner_id=item.owner_id,
                    steam_id=item.steam_id, granted_at=timestamp,
                    expires_at=timestamp + timedelta(hours=3),
                ))
            db.add(AuditEvent(
                request_id=item.id, actor_id=actor.id, action=target,
                detail=reason if not approve else "Synthetic local grant",
                created_at=timestamp,
            ))
            return request_dict(item)

    @app.post("/requests/{request_id}/approve")
    def approve(request_id: int, actor: Actor = Depends(current_actor)):
        return review(request_id, actor, True)

    @app.post("/requests/{request_id}/reject")
    def reject(request_id: int, body: RejectBody, actor: Actor = Depends(current_actor)):
        reason = body.reason.strip()
        if len(reason) < 3:
            raise HTTPException(422, "Rejection reason too short")
        return review(request_id, actor, False, reason)

    @app.get("/requests/{request_id}/events")
    def events(request_id: int, actor: Actor = Depends(current_actor)):
        with factory() as db:
            row = db.get(AccessRequest, request_id)
            if row is None or (not actor.staff and row.owner_id != actor.id):
                raise HTTPException(404, "Request not found")
            result = db.scalars(select(AuditEvent).where(
                AuditEvent.request_id == request_id,
            ).order_by(AuditEvent.id)).all()
            return [{"actor_id": event.actor_id, "action": event.action,
                     "detail": event.detail, "created_at": event.created_at} for event in result]

    @app.get("/entitlements/me")
    def my_entitlements(actor: Actor = Depends(current_actor)):
        with factory() as db:
            grants = db.scalars(select(Entitlement).where(
                Entitlement.owner_id == actor.id,
            ).order_by(Entitlement.id.desc())).all()
            current = now()
            return [{
                "request_id": grant.request_id,
                "steam_id": grant.steam_id,
                "expires_at": grant.expires_at,
                "status": "active" if grant.expires_at > current else "expired",
            } for grant in grants]

    return app


def app_factory() -> FastAPI:
    """Uvicorn imports this factory after Docker/Alembic migrations are completed."""
    return create_app()
