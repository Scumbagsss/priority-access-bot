"""HTTP and persistence tests for the separate synthetic FastAPI demonstration."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3

from fastapi.testclient import TestClient
import pytest

from api_demo.main import AccessStore, Principal, RequestBody, create_app, create_app_from_env

MEMBER_ONE = "member-one-test-token-12345"
MEMBER_TWO = "member-two-test-token-12345"
STAFF = "staff-only-test-token-12345"
STEAM_ONE = "76561198000000001"
STEAM_TWO = "76561198000000002"


@pytest.fixture
def demo(tmp_path):
    clock = [datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)]
    credentials = {
        MEMBER_ONE: Principal("member-one"),
        MEMBER_TWO: Principal("member-two"),
        STAFF: Principal("moderator", True),
    }
    db_path = tmp_path / "demo.sqlite3"
    app = create_app(db_path, credentials, now=lambda: clock[0])
    with TestClient(app) as client:
        yield client, clock, db_path


def headers(token=MEMBER_ONE):
    return {"X-API-Key": token}


def make_request(client, steam=STEAM_ONE, token=MEMBER_ONE, reason="A synthetic request"):
    return client.post(
        "/requests", headers=headers(token),
        json={"steam_id": steam, "reason": reason},
    )


def test_health_has_no_private_data(demo):
    client, _, _ = demo
    assert client.get("/health").json() == {"status": "ok", "mode": "synthetic"}


def test_missing_auth(demo):
    client, _, _ = demo
    assert client.post("/requests", json={"steam_id": STEAM_ONE, "reason": "Test reason"}).status_code == 401


def test_invalid_auth(demo):
    client, _, _ = demo
    assert make_request(client, token="invalid-key-value-000000").status_code == 401


def test_submit_and_owner_view(demo):
    client, _, _ = demo
    r = make_request(client)
    assert r.status_code == 201
    row = r.json()["request"]
    assert r.json()["created"] is True
    assert row["status"] == "pending"
    assert row["steam_id"] == STEAM_ONE
    assert client.get(f"/requests/{row['id']}", headers=headers()).json() == row


def test_duplicate_pending_request_is_idempotent(demo):
    client, _, _ = demo
    first = make_request(client).json()["request"]["id"]
    second = make_request(client, reason="Different reason")
    assert second.status_code == 200
    assert not second.json()["created"]
    assert second.json()["request"]["id"] == first
    events = client.get(f"/requests/{first}/events", headers=headers()).json()
    assert [e["action"] for e in events] == ["submitted"]


def test_other_member_can_submit_same_steam(demo):
    client, _, _ = demo
    a = make_request(client).json()["request"]["id"]
    b = make_request(client, token=MEMBER_TWO).json()["request"]["id"]
    assert a != b


def test_invalid_steam_syntax(demo):
    client, _, _ = demo
    assert make_request(client, steam="12345678901234567").status_code == 422


def test_invalid_steam_length(demo):
    client, _, _ = demo
    assert make_request(client, steam="123").status_code == 422


def test_reason_only_whitespace(demo):
    client, _, _ = demo
    assert make_request(client, reason="      ").status_code == 422


def test_extra_fields_rejected(demo):
    client, _, _ = demo
    response = client.post(
        "/requests", headers=headers(),
        json={"steam_id": STEAM_ONE, "reason": "Example", "admin": True},
    )
    assert response.status_code == 422


def test_staff_only_list(demo):
    client, _, _ = demo
    make_request(client)
    assert client.get("/requests", headers=headers()).status_code == 403
    rows = client.get("/requests", headers=headers(STAFF)).json()
    assert len(rows) == 1


def test_list_pagination_and_validation(demo):
    client, _, _ = demo
    make_request(client, steam=STEAM_ONE)
    make_request(client, steam=STEAM_TWO)
    response = client.get("/requests?limit=1&offset=1", headers=headers(STAFF))
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert client.get("/requests?limit=101", headers=headers(STAFF)).status_code == 422


def test_member_cannot_see_another_members_request(demo):
    client, _, _ = demo
    request_id = make_request(client).json()["request"]["id"]
    assert client.get(f"/requests/{request_id}", headers=headers(MEMBER_TWO)).status_code == 404
    assert client.get(f"/requests/{request_id}/events", headers=headers(MEMBER_TWO)).status_code == 404


def test_staff_can_see_all_requests(demo):
    client, _, _ = demo
    request_id = make_request(client).json()["request"]["id"]
    assert client.get(f"/requests/{request_id}", headers=headers(STAFF)).status_code == 200


def test_missing_request_404(demo):
    client, _, _ = demo
    assert client.get("/requests/9999", headers=headers()).status_code == 404


def test_member_cannot_approve(demo):
    client, _, _ = demo
    request_id = make_request(client).json()["request"]["id"]
    assert client.post(f"/requests/{request_id}/approve", headers=headers()).status_code == 403


def test_member_cannot_reject(demo):
    client, _, _ = demo
    request_id = make_request(client).json()["request"]["id"]
    assert client.post(
        f"/requests/{request_id}/reject",
        headers=headers(), json={"reason": "No thanks"},
    ).status_code == 403


def test_approval_creates_local_entitlement(demo):
    client, _, _ = demo
    request_id = make_request(client).json()["request"]["id"]
    response = client.post(f"/requests/{request_id}/approve", headers=headers(STAFF))
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    items = client.get("/entitlements/me", headers=headers()).json()
    assert len(items) == 1
    assert items[0]["request_id"] == request_id
    assert items[0]["status"] == "active"


def test_duplicate_approval_is_idempotent(demo):
    client, _, db = demo
    request_id = make_request(client).json()["request"]["id"]
    for _ in range(3):
        assert client.post(f"/requests/{request_id}/approve", headers=headers(STAFF)).status_code == 200
    events = client.get(f"/requests/{request_id}/events", headers=headers()).json()
    assert [e["action"] for e in events] == ["submitted", "approved"]
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM entitlements").fetchone()[0] == 1


def test_rejection_sets_reason_and_audit(demo):
    client, _, _ = demo
    request_id = make_request(client).json()["request"]["id"]
    response = client.post(
        f"/requests/{request_id}/reject",
        headers=headers(STAFF), json={"reason": "Missing synthetic evidence"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["review_reason"] == "Missing synthetic evidence"
    events = client.get(f"/requests/{request_id}/events", headers=headers()).json()
    assert [e["action"] for e in events] == ["submitted", "rejected"]


def test_repeated_rejection_does_not_add_event(demo):
    client, _, _ = demo
    request_id = make_request(client).json()["request"]["id"]
    for reason in ("First reason", "Another reason"):
        assert client.post(
            f"/requests/{request_id}/reject",
            headers=headers(STAFF), json={"reason": reason},
        ).status_code == 200
    assert len(client.get(f"/requests/{request_id}/events", headers=headers()).json()) == 2


def test_approve_rejected_conflicts(demo):
    client, _, _ = demo
    request_id = make_request(client).json()["request"]["id"]
    client.post(
        f"/requests/{request_id}/reject",
        headers=headers(STAFF), json={"reason": "Declined"},
    )
    assert client.post(f"/requests/{request_id}/approve", headers=headers(STAFF)).status_code == 409


def test_reject_approved_conflicts(demo):
    client, _, _ = demo
    request_id = make_request(client).json()["request"]["id"]
    client.post(f"/requests/{request_id}/approve", headers=headers(STAFF))
    assert client.post(
        f"/requests/{request_id}/reject",
        headers=headers(STAFF), json={"reason": "Declined"},
    ).status_code == 409


def test_rejection_blank_reason(demo):
    client, _, _ = demo
    request_id = make_request(client).json()["request"]["id"]
    assert client.post(
        f"/requests/{request_id}/reject",
        headers=headers(STAFF), json={"reason": "   "},
    ).status_code == 422


def test_entitlement_expiry_is_clock_based(demo):
    client, clock, _ = demo
    request_id = make_request(client).json()["request"]["id"]
    client.post(f"/requests/{request_id}/approve", headers=headers(STAFF))
    clock[0] += timedelta(hours=4)
    entitlements = client.get("/entitlements/me", headers=headers()).json()
    assert entitlements[0]["status"] == "expired"
    # No automatic game-side revocation: synthetic local-only projection.
    assert client.get(f"/requests/{request_id}", headers=headers()).json()["status"] == "approved"


def test_entitlements_private_to_owner(demo):
    client, _, _ = demo
    request_id = make_request(client).json()["request"]["id"]
    client.post(f"/requests/{request_id}/approve", headers=headers(STAFF))
    assert client.get("/entitlements/me", headers=headers(MEMBER_TWO)).json() == []


def test_persists_after_new_store_instance(demo):
    client, _, db = demo
    request_id = make_request(client).json()["request"]["id"]
    client.post(f"/requests/{request_id}/approve", headers=headers(STAFF))
    another = AccessStore(db)
    assert another.get(Principal("member-one"), request_id)["status"] == "approved"


def test_sql_metacharacters_are_data(demo):
    client, _, db = demo
    payload = "Reason with SQL syntax: '; DROP TABLE requests; --"
    response = make_request(client, reason=payload)
    assert response.status_code == 201
    assert response.json()["request"]["reason"] == payload
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1


def test_invalid_short_keys_rejected(tmp_path):
    with pytest.raises(ValueError):
        create_app(tmp_path / "db.sqlite3", {"short": Principal("member")})


def test_factory_requires_explicit_env_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("DEMO_MEMBER_TOKEN", raising=False)
    monkeypatch.delenv("DEMO_STAFF_TOKEN", raising=False)
    monkeypatch.setenv("DEMO_DB_PATH", str(tmp_path / "db.sqlite3"))
    with pytest.raises(ValueError):
        create_app_from_env()


def test_concurrent_approvals_commit_one_entitlement(demo):
    client, clock, db_path = demo
    request_id = make_request(client).json()["request"]["id"]
    store = AccessStore(db_path, now=lambda: clock[0])
    moderator = Principal("moderator", True)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: store.approve(moderator, request_id), range(4)))
    assert all(row["status"] == "approved" for row in results)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM entitlements").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='approved'"
        ).fetchone()[0] == 1
