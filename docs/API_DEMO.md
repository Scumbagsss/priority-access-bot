# Synthetic FastAPI API — technical notes

> **Independent portfolio sample, not a DayZ or Discord production system.** No real server, Steam registration endpoint, payment gateway or private entitlement service is contacted.

## Data flow

1. Caller sends an \`X-API-Key\`. The server maps that key to one of a small set of *preconfigured synthetic principals*.
2. Member sends a SteamID64-shaped string and reason to \`POST /requests\`.
3. The service validates basic syntax and uses \`BEGIN IMMEDIATE\` plus a partial UNIQUE index to guarantee at most one pending request for the same synthetic owner and Steam ID.
4. A staff principal approves or rejects the request.
5. On approval, **one local SQLite entitlement record** is created in the same transaction. A second approval is idempotent.
6. \`GET /entitlements/me\` calculates active vs expired from an explicit timestamp; it does **not** revoke real game permissions.

\`\`\`mermaid
stateDiagram-v2
  [*] --> pending: submit
  pending --> approved: staff approves
  pending --> rejected: staff rejects
  approved --> approved: repeat approve (no-op)
  rejected --> rejected: repeat reject (no-op)
\`\`\`

The actual entitlement row is time-limited to **three hours in this demo**. This is only a code example. It does not imply that the private product follows the same policy.

## Endpoints

| Method | Path | Who | Meaning |
| --- | --- | --- | --- |
| GET | \`/health\` | Public | Show synthetic-mode health |
| POST | \`/requests\` | Authenticated | Submit, or reuse, a pending request |
| GET | \`/requests\` | Staff | Paginated request list |
| GET | \`/requests/{id}\` | Owner / staff | Read request |
| POST | \`/requests/{id}/approve\` | Staff | Atomic local-only grant |
| POST | \`/requests/{id}/reject\` | Staff | Reject with reason |
| GET | \`/requests/{id}/events\` | Owner / staff | Audit events |
| GET | \`/entitlements/me\` | Authenticated | Own synthetic entitlements |

### Example calls

Run the API from the repository root (Unix-like shell):

\`\`\`bash
python -m pip install -r requirements-demo.txt
export DEMO_MEMBER_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
export DEMO_STAFF_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
export DEMO_DB_PATH="priority-demo.sqlite3"
uvicorn api_demo.main:create_app_from_env --factory --host 127.0.0.1 --port 8000
\`\`\`

In a second terminal, use the **same member token**:

\`\`\`bash
curl -X POST http://127.0.0.1:8000/requests \
  -H "X-API-Key: $DEMO_MEMBER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"steam_id":"76561198000000001","reason":"Synthetic demo access request"}'
\`\`\`

The returned ID can then be approved with the staff token:

\`\`\`bash
curl -X POST http://127.0.0.1:8000/requests/1/approve \
  -H "X-API-Key: $DEMO_STAFF_TOKEN"
\`\`\`

Open \`http://127.0.0.1:8000/docs\` for generated OpenAPI docs. The environment variables used by the running service must be set in the shell where Uvicorn is launched.

## Defensive decisions demonstrated

- No working credentials checked into the source: both demo tokens are **mandatory environment variables**.
- API identities/roles are configured server-side; a user-submitted role flag cannot grant staff rights.
- HTTP 401 for missing/wrong keys, 403 for staff-only operations, 404 for another user's record, and 409 for invalid decision transitions.
- Request creation and approvals use SQLite write transactions; duplicate approvals cannot multiply local entitlements.
- Ownership checks apply to request details and audit history.
- SQLite queries use bound parameters.
- The database is committed or rolled back and its connections are closed after each operation.
- No real game-system connection is made.

## Explicit omissions

- A fixed token-to-principal map is a **test fixture**, not a production identity provider. For real use, replace it with a mature auth system (JWT/OAuth2 with validated claims, session management, rotation, rate limits, TLS, monitoring).
- SteamID64 **syntax** validation does not prove account registration or identity ownership.
- No external delivery semantics, webhook retries or outbox/reconciliation service.
- No production migration mechanism, monitoring, request throttling, distributed coordination or deployment hardening.
- The local "expired" status is a read-time projection, **not** an external entitlement revocation.
- All player identities in examples are invented.

## Verification

\`\`\`bash
python -m pytest -q tests/test_api_demo.py
\`\`\`

GitHub Actions runs this suite under Python 3.11, 3.12 and 3.13. Tests include authorization boundaries, owner isolation, approval idempotence, concurrency, expiration and SQLite persistence.
