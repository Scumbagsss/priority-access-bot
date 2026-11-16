# Priority Access API — Python Backend & Authentication Demo

**FastAPI · SQLite · Session authentication · Authorization · Transactional workflows**

[![CI](https://github.com/Scumbagsss/priority-access-bot/actions/workflows/api-demo.yml/badge.svg)](https://github.com/Scumbagsss/priority-access-bot/actions/workflows/api-demo.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-REST-009688?logo=fastapi&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-Transactions-003B57?logo=sqlite&logoColor=white)
![Project](https://img.shields.io/badge/Commercial%20bot-private-orange)

> **Original synthetic backend, not a real DayZ service.** The production Discord priority bot and integrations remain private. Public examples use fictional identities, a local SQLite database and **no** external Steam/DayZ access.

![Synthetic FastAPI workflow](assets/api-demo.svg)

*Concept illustration: local-only simulated entitlements, not production screenshots.*

## PostgreSQL + Docker deployment example (new)

[![PostgreSQL deployment demo](https://github.com/Scumbagsss/priority-access-bot/actions/workflows/postgres-service.yml/badge.svg)](https://github.com/Scumbagsss/priority-access-bot/actions/workflows/postgres-service.yml)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.x-CB3C3C)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)

The **[PostgreSQL variant](postgres_app/)** is an **independent, deployable synthetic FastAPI application** alongside the original lightweight SQLite demo. It adds:

- **PostgreSQL 16** with SQLAlchemy 2 ORM, explicit relationships and transactional state;
- **versioned Alembic migrations** (no automatic `create_all` on startup);
- **Dockerfile + Compose**: health-checked database, an API on `127.0.0.1:8000`, non-root application container and persistent volume;
- **local user provisioning** and hashed, revocable bearer sessions;
- **row locking and a partial unique index** to handle concurrent reviews and duplicate submissions;
- **isolated PostgreSQL integration tests** and GitHub Actions Docker Compose startup checks.

### Run PostgreSQL version locally

```bash
cp deployment.env.example .env
# Change POSTGRES_PASSWORD in .env to a long random local secret!
docker compose up -d --build --wait
docker compose exec -it api python -m postgres_app.cli demo-member
docker compose exec -it api python -m postgres_app.cli demo-moderator --staff
```

Open **http://127.0.0.1:8000/docs** to test a fictional request. The [deployment guide](docs/POSTGRES_DEPLOYMENT.md) includes migration commands, database persistence, test isolation and shutdown instructions.

> **Deployment boundary:** this is a *self-hosted educational example*, not a public production service. It has **no** real Discord/Steam/DayZ API integration or server-access delivery. It lacks public-facing safeguards including login throttling, MFA, TLS termination, monitoring and backup automation. **Do not expose it to the internet as-is.**

The smaller SQLite demo below remains runnable without Docker, PostgreSQL or additional deployment configuration.

## The backend problem

An access-request workflow combines user identity, staff permissions, approval state, safe retries and audit records. A real backend cannot trust a client-supplied role or repeat external delivery blindly.

This repository includes a **self-contained FastAPI demonstration** that covers part of those challenges without exposing the commercial application.

## Authentication added to the public demo

- User accounts are **explicitly provisioned locally** by a CLI, not through open signup.
- Passwords are stored as salted **scrypt** hashes, never in plaintext.
- Login issues random, **60-minute opaque bearer sessions**; only SHA-256 session-token hashes are stored in SQLite.
- Logout revokes the session immediately.
- Each request reloads the user's enabled status and staff role from SQLite, so changes apply to existing sessions.
- All application permissions are checked server-side. Staff can view/review requests; members can view only their own.
- Invalid, expired or revoked sessions receive HTTP 401.

The demo is **not a production identity provider**: it omits request throttling, OAuth/SSO, TLS termination, MFA and deployment hardening. It is intended for local review only.

## Run locally

Requires Python 3.11+ and the dependencies in [requirements-demo.txt](requirements-demo.txt).

```bash
python -m pip install -r requirements-demo.txt
python -m api_demo.manage --db priority-demo.sqlite3 create-user --username demo-member
python -m api_demo.manage --db priority-demo.sqlite3 create-user --username demo-moderator --staff
uvicorn api_demo.main:create_app_from_env --factory --host 127.0.0.1 --port 8000
```

The CLI **prompts for separate passwords** and does not put them in process arguments or source files. Then open [local OpenAPI docs](http://127.0.0.1:8000/docs). Use `POST /auth/login` to obtain a bearer session; pass it as `Authorization: Bearer <token>`.

### Verify

```bash
python -m pytest -q tests/test_api_demo.py
```

GitHub Actions validates the synthetic API on Python 3.11, 3.12 and 3.13. Tests include HTTP login/logout, session expiry, password hashing, role changes, access boundaries, approval idempotency, concurrency and audit persistence.

## Endpoint summary

| Method | Endpoint | Permission |
|---|---|---|
| POST | `/auth/login` | Password login for provisioned local user |
| POST | `/auth/logout` | Authenticated session holder |
| GET | `/auth/me` | Authenticated session holder |
| POST | `/requests` | Authenticated |
| GET | `/requests` | Staff only |
| GET | `/requests/{id}` | Owner or staff |
| POST | `/requests/{id}/approve` | Staff only |
| POST | `/requests/{id}/reject` | Staff only |
| GET | `/requests/{id}/events` | Owner or staff |
| GET | `/entitlements/me` | Authenticated |

Approval creates only a **fictional SQLite entitlement** in one transaction. It does not grant a real DayZ priority or validate real Steam ownership.

## Architecture

```mermaid
flowchart LR
  P[Local users] --> A[Password login]
  A --> B[(Hashed sessions)]
  B --> C[Bearer auth dependency]
  C --> R[Role + ownership check]
  R --> W[Request workflow]
  W --> D[(SQLite decisions / audit)]
  W --> E[(Local synthetic entitlements)]
```

See the [complete API walkthrough](docs/API_DEMO.md), [security notes](SECURITY.md) and [failure-mode analysis](docs/FAILURE_MODES.md).

## Public/private boundary

- [`api_demo/auth.py`](api_demo/auth.py) — independent demo auth/session store
- [`api_demo/main.py`](api_demo/main.py) — FastAPI + local workflow service
- [`api_demo/manage.py`](api_demo/manage.py) — password-prompted local account creation
- [`tests/test_api_demo.py`](tests/test_api_demo.py) — synthetic HTTP/database tests
- [`src/public_interface.py`](src/public_interface.py) — **unimplemented** commercial interface sketch

The private Discord implementation, real identity service adapters, API endpoints, secrets and player data are not released.

**Contact:** Discord `scumbagsss` · Telegram [@dilarion](https://t.me/dilarion)
