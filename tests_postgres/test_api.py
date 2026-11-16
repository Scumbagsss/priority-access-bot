"""Integration tests requiring a migrated, isolated PostgreSQL database.

These tests truncate tables before each case: never run against a real database.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from postgres_app.api import create_app
from postgres_app.db import build_engine
from postgres_app.models import AccessRequest, AuditEvent, Entitlement, SessionToken, User
from postgres_app.security import create_user

PASSWORD = "sample-account-password-12345"
STEAM = "76561198000000001"
SECOND_STEAM = "76561198000000002"


@pytest.fixture
def demo():
    engine = build_engine()
    # WARNING: only execute this suite against a disposable test DB.
    assert engine.url.database == "priority_test", "Refusing to clear a non-test database"
    with engine.begin() as connection:
        connection.execute(text(
            "TRUNCATE audit_events, entitlements, access_requests, "
            "session_tokens, users RESTART IDENTITY CASCADE"
        ))
    clock = [datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)]
    with Session(engine) as db, db.begin():
        create_user(db, "member-one", PASSWORD, clock[0])
        create_user(db, "member-two", PASSWORD, clock[0])
        create_user(db, "moderator", PASSWORD, clock[0], staff=True)
    with TestClient(create_app(engine, clock=lambda: clock[0])) as client:
        yield client, engine, clock
    engine.dispose()


def sign_in(client, who="member-one", password=PASSWORD):
    return client.post("/auth/login", json={"username": who, "password": password})


def bearer(client, who="member-one"):
    response = sign_in(client, who)
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def submit(client, header, steam=STEAM, reason="Synthetic access review"):
    return client.post("/requests", headers=header, json={"steam_id": steam, "reason": reason})


def request_id(client, header):
    response = submit(client, header)
    assert response.status_code == 201, response.text
    return response.json()["request"]["id"]


def test_migrations_create_expected_tables(demo):
    _, engine, _ = demo
    tables = set(inspect(engine).get_table_names())
    assert {"users", "session_tokens", "access_requests", "entitlements", "audit_events", "alembic_version"} <= tables


def test_health_and_ready(demo):
    client, *_ = demo
    assert client.get("/health").status_code == 200
    assert client.get("/ready").json() == {"status": "ready"}


def test_password_login_and_owner(demo):
    client, *_ = demo
    headers = bearer(client)
    result = client.get("/auth/me", headers=headers)
    assert result.status_code == 200
    assert result.json()["is_staff"] is False


def test_bad_password_rejected(demo):
    client, *_ = demo
    assert sign_in(client, password="incorrect").status_code == 401


def test_unknown_user_rejected(demo):
    client, *_ = demo
    assert sign_in(client, who="missing-user").status_code == 401


def test_missing_and_invalid_bearer(demo):
    client, *_ = demo
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer invalid-token-value"}).status_code == 401


def test_token_is_hashed_in_postgres(demo):
    client, engine, _ = demo
    response = sign_in(client)
    raw = response.json()["access_token"]
    with Session(engine) as db:
        token = db.scalar(select(SessionToken))
        assert token.token_hash == hashlib.sha256(raw.encode()).hexdigest()
        assert token.token_hash != raw


def test_logout_revokes_session(demo):
    client, *_ = demo
    headers = bearer(client)
    assert client.post("/auth/logout", headers=headers).status_code == 200
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_session_expires(demo):
    client, _, clock = demo
    headers = bearer(client)
    clock[0] += timedelta(minutes=31)
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_deactivated_user_loses_access(demo):
    client, engine, _ = demo
    headers = bearer(client)
    with Session(engine) as db, db.begin():
        row = db.scalar(select(User).where(User.username == "member-one"))
        row.is_active = False
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_roles_rechecked_after_change(demo):
    client, engine, _ = demo
    headers = bearer(client)
    assert client.get("/requests", headers=headers).status_code == 403
    with Session(engine) as db, db.begin():
        row = db.scalar(select(User).where(User.username == "member-one"))
        row.is_staff = True
    assert client.get("/requests", headers=headers).status_code == 200


def test_create_and_view_request(demo):
    client, *_ = demo
    headers = bearer(client)
    rid = request_id(client, headers)
    assert client.get(f"/requests/{rid}", headers=headers).json()["status"] == "pending"


def test_duplicate_pending_is_idempotent(demo):
    client, engine, _ = demo
    headers = bearer(client)
    rid = request_id(client, headers)
    again = submit(client, headers)
    assert again.status_code == 200
    assert again.json()["created"] is False
    assert again.json()["request"]["id"] == rid
    with Session(engine) as db:
        assert len(db.scalars(select(AccessRequest)).all()) == 1


def test_two_owners_can_use_same_fictional_id(demo):
    client, *_ = demo
    a, b = bearer(client), bearer(client, "member-two")
    assert request_id(client, a) != request_id(client, b)


def test_invalid_steam_rejected(demo):
    client, *_ = demo
    assert submit(client, bearer(client), steam="not-a-steam-id").status_code == 422


def test_blank_reason_rejected(demo):
    client, *_ = demo
    assert submit(client, bearer(client), reason="     ").status_code == 422


def test_extra_role_flag_rejected(demo):
    client, *_ = demo
    headers = bearer(client)
    assert client.post("/requests", headers=headers, json={
        "steam_id": STEAM, "reason": "Just a demo", "is_staff": True,
    }).status_code == 422


def test_owner_isolation(demo):
    client, *_ = demo
    a, b = bearer(client), bearer(client, "member-two")
    rid = request_id(client, a)
    assert client.get(f"/requests/{rid}", headers=b).status_code == 404
    assert client.get(f"/requests/{rid}/events", headers=b).status_code == 404


def test_staff_only_listing(demo):
    client, *_ = demo
    a, s = bearer(client), bearer(client, "moderator")
    request_id(client, a)
    assert client.get("/requests", headers=a).status_code == 403
    assert len(client.get("/requests", headers=s).json()) == 1
    assert client.get("/requests?limit=101", headers=s).status_code == 422


def test_only_staff_can_approve(demo):
    client, *_ = demo
    a = bearer(client)
    rid = request_id(client, a)
    assert client.post(f"/requests/{rid}/approve", headers=a).status_code == 403


def test_approve_creates_one_entitlement_and_audit(demo):
    client, engine, _ = demo
    a, s = bearer(client), bearer(client, "moderator")
    rid = request_id(client, a)
    assert client.post(f"/requests/{rid}/approve", headers=s).json()["status"] == "approved"
    assert client.post(f"/requests/{rid}/approve", headers=s).json()["status"] == "approved"
    assert [x["action"] for x in client.get(f"/requests/{rid}/events", headers=a).json()] == ["submitted", "approved"]
    with Session(engine) as db:
        assert len(db.scalars(select(Entitlement)).all()) == 1
        assert len(db.scalars(select(AuditEvent)).all()) == 2


def test_rejection_reason_and_conflict(demo):
    client, *_ = demo
    a, s = bearer(client), bearer(client, "moderator")
    rid = request_id(client, a)
    result = client.post(f"/requests/{rid}/reject", headers=s, json={"reason": "Review declined"})
    assert result.json()["status"] == "rejected"
    assert result.json()["review_reason"] == "Review declined"
    assert client.post(f"/requests/{rid}/approve", headers=s).status_code == 409


def test_cannot_reject_approved(demo):
    client, *_ = demo
    a, s = bearer(client), bearer(client, "moderator")
    rid = request_id(client, a)
    client.post(f"/requests/{rid}/approve", headers=s)
    assert client.post(f"/requests/{rid}/reject", headers=s, json={"reason": "Later"}).status_code == 409


def test_entitlement_expiry_projection(demo):
    client, _, clock = demo
    a, s = bearer(client), bearer(client, "moderator")
    rid = request_id(client, a)
    client.post(f"/requests/{rid}/approve", headers=s)
    clock[0] += timedelta(hours=4)
    newer = bearer(client)
    items = client.get("/entitlements/me", headers=newer).json()
    assert items[0]["request_id"] == rid
    assert items[0]["status"] == "expired"


def test_concurrent_approvals_are_serialized(demo):
    client, engine, _ = demo
    a, s = bearer(client), bearer(client, "moderator")
    rid = request_id(client, a)
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: client.post(f"/requests/{rid}/approve", headers=s).status_code, range(4)))
    assert statuses == [200] * 4
    with Session(engine) as db:
        assert len(db.scalars(select(Entitlement)).all()) == 1
        assert len(db.scalars(select(AuditEvent).where(AuditEvent.action == "approved")).all()) == 1


def test_concurrent_duplicate_submissions_are_serialized(demo):
    client, engine, _ = demo
    headers = bearer(client)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: submit(client, headers), range(4)))
    assert sorted(row.status_code for row in results) == [200, 200, 200, 201]
    with Session(engine) as db:
        assert len(db.scalars(select(AccessRequest)).all()) == 1
