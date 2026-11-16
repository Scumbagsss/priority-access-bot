"""HTTP integration and SQLite tests using only fictional identities and data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3

from fastapi.testclient import TestClient
import pytest

from api_demo.auth import AuthStore, Principal
from api_demo.main import AccessStore, create_app, create_app_from_env

PASSWORD = "synthetic-demo-password-12345"
M1, M2, STAFF = "member-one", "member-two", "demo-moderator"
STEAM_1 = "76561198000000001"
STEAM_2 = "76561198000000002"


@pytest.fixture
def demo(tmp_path):
    clock = [datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)]
    db = tmp_path / "demo.sqlite3"
    auth = AuthStore(db, now=lambda: clock[0])
    auth.create_user(M1, PASSWORD)
    auth.create_user(M2, PASSWORD)
    auth.create_user(STAFF, PASSWORD, staff=True)
    with TestClient(create_app(db, now=lambda: clock[0])) as client:
        tokens = {}
        for username in (M1, M2, STAFF):
            result = client.post("/auth/login", json={
                "username": username, "password": PASSWORD,
            })
            assert result.status_code == 200, result.text
            tokens[username] = result.json()["access_token"]
        yield client, clock, db, auth, tokens


def head(tokens, who=M1):
    return {"Authorization": f"Bearer {tokens[who]}"}


def submit(client, tokens, who=M1, steam=STEAM_1, reason="A synthetic request"):
    return client.post(
        "/requests", headers=head(tokens, who),
        json={"steam_id": steam, "reason": reason},
    )


def request_id(client, tokens):
    response = submit(client, tokens)
    assert response.status_code == 201
    return response.json()["request"]["id"]


def test_health(demo):
    client, *_ = demo
    assert client.get("/health").json() == {"status": "ok", "mode": "synthetic"}


def test_missing_bearer(demo):
    client, *_ = demo
    assert client.post("/requests", json={"steam_id": STEAM_1, "reason": "Example"}).status_code == 401


def test_invalid_bearer(demo):
    client, *_ = demo
    assert client.get("/auth/me", headers={"Authorization": "Bearer bogus-token-123456789"}).status_code == 401


def test_unsupported_scheme(demo):
    client, *_ = demo
    assert client.get("/auth/me", headers={"Authorization": "Basic abcdef"}).status_code == 401


def test_login_password_incorrect(demo):
    client, *_ = demo
    result = client.post("/auth/login", json={"username": M1, "password": "wrong-pass"})
    assert result.status_code == 401
    assert result.json()["detail"] == "Invalid credentials"


def test_login_user_unknown(demo):
    client, *_ = demo
    assert client.post("/auth/login", json={
        "username": "never-existed", "password": PASSWORD,
    }).status_code == 401


def test_login_returns_expiration(demo):
    client, *_ = demo
    payload = client.post("/auth/login", json={"username": M1, "password": PASSWORD}).json()
    assert payload["token_type"] == "bearer"
    assert len(payload["access_token"]) >= 30
    assert payload["expires_at"].startswith("2026-11-01")


def test_auth_me_returns_database_role(demo):
    client, _, _, _, tokens = demo
    assert client.get("/auth/me", headers=head(tokens, M1)).json() == {
        "id": M1, "is_staff": False,
    }
    assert client.get("/auth/me", headers=head(tokens, STAFF)).json()["is_staff"] is True


def test_logout_revokes_session(demo):
    client, _, _, _, tokens = demo
    h = head(tokens)
    assert client.post("/auth/logout", headers=h).json()["status"] == "revoked"
    assert client.get("/auth/me", headers=h).status_code == 401
    assert client.post("/auth/logout", headers=h).status_code == 401


def test_expired_session_is_rejected(demo):
    client, clock, _, _, tokens = demo
    clock[0] += timedelta(minutes=61)
    assert client.get("/auth/me", headers=head(tokens)).status_code == 401


def test_role_update_applies_to_existing_session(demo):
    client, _, _, auth, tokens = demo
    assert client.get("/requests", headers=head(tokens)).status_code == 403
    auth.set_staff(M1, True)
    assert client.get("/requests", headers=head(tokens)).status_code == 200


def test_disabled_user_loses_existing_sessions(demo):
    client, _, _, auth, tokens = demo
    auth.disable_user(M1)
    assert client.get("/auth/me", headers=head(tokens)).status_code == 401
    assert client.post("/auth/login", json={"username": M1, "password": PASSWORD}).status_code == 401


def test_password_and_token_not_stored_in_plain_text(demo):
    _, _, db, _, tokens = demo
    with sqlite3.connect(db) as conn:
        user = conn.execute("SELECT salt,digest FROM demo_users WHERE username=?", (M1,)).fetchone()
        session = conn.execute("SELECT token_hash FROM demo_sessions LIMIT 1").fetchone()
    assert user[0] != PASSWORD.encode()
    assert user[1] != PASSWORD.encode()
    assert len(user[1]) == 32
    assert session[0] not in tokens.values()


def test_duplicate_username_prevented(demo):
    _, _, _, auth, _ = demo
    with pytest.raises(sqlite3.IntegrityError):
        auth.create_user(M1, PASSWORD)


def test_short_password_rejected(demo):
    _, _, _, auth, _ = demo
    with pytest.raises(ValueError):
        auth.create_user("new-user", "short")


def test_extra_login_field_rejected(demo):
    client, *_ = demo
    assert client.post("/auth/login", json={
        "username": M1, "password": PASSWORD, "is_staff": True,
    }).status_code == 422


def test_submit_and_get(demo):
    client, _, _, _, tokens = demo
    response = submit(client, tokens)
    assert response.status_code == 201
    row = response.json()["request"]
    assert response.json()["created"] is True
    assert row["status"] == "pending"
    assert client.get(f"/requests/{row['id']}", headers=head(tokens)).json() == row


def test_duplicate_pending_request_idempotent(demo):
    client, _, _, _, tokens = demo
    first = request_id(client, tokens)
    second = submit(client, tokens, reason="Another synthetic reason")
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["request"]["id"] == first
    events = client.get(f"/requests/{first}/events", headers=head(tokens)).json()
    assert [e["action"] for e in events] == ["submitted"]


def test_different_members_same_steam(demo):
    client, _, _, _, tokens = demo
    first = request_id(client, tokens)
    second = submit(client, tokens, who=M2).json()["request"]["id"]
    assert first != second


@pytest.mark.parametrize("steam", ["12345678901234567", "123", "not-a-steam-id"])
def test_invalid_steam(demo, steam):
    client, _, _, _, tokens = demo
    assert submit(client, tokens, steam=steam).status_code == 422


def test_whitespace_reason_rejected(demo):
    client, _, _, _, tokens = demo
    assert submit(client, tokens, reason="      ").status_code == 422


def test_extra_request_fields_rejected(demo):
    client, _, _, _, tokens = demo
    response = client.post("/requests", headers=head(tokens), json={
        "steam_id": STEAM_1, "reason": "Example reason", "is_staff": True,
    })
    assert response.status_code == 422


def test_staff_only_listing(demo):
    client, _, _, _, tokens = demo
    request_id(client, tokens)
    assert client.get("/requests", headers=head(tokens)).status_code == 403
    assert len(client.get("/requests", headers=head(tokens, STAFF)).json()) == 1


def test_list_pagination(demo):
    client, _, _, _, tokens = demo
    submit(client, tokens, steam=STEAM_1)
    submit(client, tokens, steam=STEAM_2)
    assert len(client.get("/requests?limit=1&offset=1", headers=head(tokens, STAFF)).json()) == 1
    assert client.get("/requests?limit=101", headers=head(tokens, STAFF)).status_code == 422


def test_member_cannot_view_others(demo):
    client, _, _, _, tokens = demo
    rid = request_id(client, tokens)
    assert client.get(f"/requests/{rid}", headers=head(tokens, M2)).status_code == 404
    assert client.get(f"/requests/{rid}/events", headers=head(tokens, M2)).status_code == 404


def test_staff_can_view(demo):
    client, _, _, _, tokens = demo
    rid = request_id(client, tokens)
    assert client.get(f"/requests/{rid}", headers=head(tokens, STAFF)).status_code == 200


def test_unknown_request(demo):
    client, _, _, _, tokens = demo
    assert client.get("/requests/99999", headers=head(tokens)).status_code == 404


def test_member_cannot_approve(demo):
    client, _, _, _, tokens = demo
    rid = request_id(client, tokens)
    assert client.post(f"/requests/{rid}/approve", headers=head(tokens)).status_code == 403


def test_member_cannot_reject(demo):
    client, _, _, _, tokens = demo
    rid = request_id(client, tokens)
    assert client.post(f"/requests/{rid}/reject", headers=head(tokens),
                       json={"reason": "Not acceptable"}).status_code == 403


def test_approval_local_only(demo):
    client, _, _, _, tokens = demo
    rid = request_id(client, tokens)
    result = client.post(f"/requests/{rid}/approve", headers=head(tokens, STAFF))
    assert result.status_code == 200
    assert result.json()["status"] == "approved"
    grants = client.get("/entitlements/me", headers=head(tokens)).json()
    assert len(grants) == 1
    assert grants[0]["status"] == "active"
    assert grants[0]["request_id"] == rid


def test_duplicate_approval_is_idempotent(demo):
    client, _, db, _, tokens = demo
    rid = request_id(client, tokens)
    for _ in range(3):
        assert client.post(f"/requests/{rid}/approve", headers=head(tokens, STAFF)).status_code == 200
    events = client.get(f"/requests/{rid}/events", headers=head(tokens)).json()
    assert [e["action"] for e in events] == ["submitted", "approved"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM entitlements").fetchone()[0] == 1


def test_rejection_and_audit(demo):
    client, _, _, _, tokens = demo
    rid = request_id(client, tokens)
    result = client.post(f"/requests/{rid}/reject", headers=head(tokens, STAFF),
                         json={"reason": "Insufficient demo data"})
    assert result.status_code == 200
    assert result.json()["review_reason"] == "Insufficient demo data"
    assert [e["action"] for e in client.get(
        f"/requests/{rid}/events", headers=head(tokens),
    ).json()] == ["submitted", "rejected"]


def test_repeat_rejection_no_second_audit_event(demo):
    client, _, _, _, tokens = demo
    rid = request_id(client, tokens)
    for why in ("First reason", "Second reason"):
        client.post(f"/requests/{rid}/reject", headers=head(tokens, STAFF),
                    json={"reason": why})
    assert len(client.get(f"/requests/{rid}/events", headers=head(tokens)).json()) == 2


def test_approve_after_rejection_conflicts(demo):
    client, _, _, _, tokens = demo
    rid = request_id(client, tokens)
    client.post(f"/requests/{rid}/reject", headers=head(tokens, STAFF),
                json={"reason": "Declined"})
    assert client.post(f"/requests/{rid}/approve", headers=head(tokens, STAFF)).status_code == 409


def test_reject_after_approval_conflicts(demo):
    client, _, _, _, tokens = demo
    rid = request_id(client, tokens)
    client.post(f"/requests/{rid}/approve", headers=head(tokens, STAFF))
    assert client.post(f"/requests/{rid}/reject", headers=head(tokens, STAFF),
                       json={"reason": "Declined"}).status_code == 409


def test_blank_rejection_reason(demo):
    client, _, _, _, tokens = demo
    rid = request_id(client, tokens)
    assert client.post(f"/requests/{rid}/reject", headers=head(tokens, STAFF),
                       json={"reason": "   "}).status_code == 422


def test_expired_entitlement_projection(demo):
    client, clock, _, _, tokens = demo
    rid = request_id(client, tokens)
    client.post(f"/requests/{rid}/approve", headers=head(tokens, STAFF))
    clock[0] += timedelta(hours=4)
    # Start new session after old one expires; entitlement is independent.
    fresh = client.post("/auth/login", json={"username": M1, "password": PASSWORD}).json()
    authorized = {"Authorization": f"Bearer {fresh['access_token']}"}
    assert client.get("/entitlements/me", headers=authorized).json()[0]["status"] == "expired"
    assert client.get(f"/requests/{rid}", headers=authorized).json()["status"] == "approved"


def test_entitlement_owner_isolation(demo):
    client, _, _, _, tokens = demo
    rid = request_id(client, tokens)
    client.post(f"/requests/{rid}/approve", headers=head(tokens, STAFF))
    assert client.get("/entitlements/me", headers=head(tokens, M2)).json() == []


def test_persistence_across_store_instances(demo):
    client, _, db, _, tokens = demo
    rid = request_id(client, tokens)
    client.post(f"/requests/{rid}/approve", headers=head(tokens, STAFF))
    assert AccessStore(db).get(Principal(M1), rid)["status"] == "approved"


def test_sql_metacharacters_treated_as_data(demo):
    client, _, db, _, tokens = demo
    phrase = "Synthetic text: '; DROP TABLE requests; --"
    response = submit(client, tokens, reason=phrase)
    assert response.status_code == 201
    assert response.json()["request"]["reason"] == phrase
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1


def test_concurrent_approvals_single_effect(demo):
    client, clock, db, _, tokens = demo
    rid = request_id(client, tokens)
    store = AccessStore(db, now=lambda: clock[0])
    staff = Principal(STAFF, True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _: store.approve(staff, rid), range(4)))
    assert all(x["status"] == "approved" for x in outcomes)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM entitlements").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='approved'"
        ).fetchone()[0] == 1


def test_api_factory_requires_local_accounts(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DB_PATH", str(tmp_path / "empty.sqlite3"))
    app = create_app_from_env()
    with TestClient(app) as client:
        assert client.post("/auth/login", json={
            "username": "demo-member", "password": PASSWORD,
        }).status_code == 401
