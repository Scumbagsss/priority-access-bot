# Security — synthetic local demo

The public `api_demo/` service is a **portfolio-only code sample**, not a hardened identity provider or production entitlement server.

It implements locally provisioned usernames, salted scrypt password hashes, opaque 60-minute bearer sessions, token hashing at rest, logout revocation, re-checked staff roles and ownership boundaries. Accounts are provisioned manually through a `getpass` CLI; no public registration route exists.

**Do not deploy this example to the public internet.** It lacks login throttling and account lockouts, credential rotation, identity-provider integration, HTTPS termination, monitoring, multi-factor authentication and deployment security controls.

Do not commit production secrets, private access APIs, live game-server records, user data, databases or backups. `*.sqlite3` and `.env` are ignored by Git. If a credential is exposed, rotate it immediately and investigate downstream access; merely rewriting Git history does not revoke it.

All identities and SteamID-shaped values in the test suite are fictional. The project cannot grant, extend or revoke real server priority.

## PostgreSQL Docker example

The separate `postgres_app/` example is **deployable on local Docker Compose** and uses SQLAlchemy, Alembic and PostgreSQL. Its Compose configuration exposes only the API on `127.0.0.1`; the database is not mapped to a host port. Required database credentials come from `.env` (gitignored), and the API runs as a non-root container user.

This is **not production-hardened software**. It does not implement login rate limiting, MFA, SSO/OAuth, TLS/reverse proxy setup, abuse controls, secrets rotation, automated backups, resource monitoring or incident management. Never expose it directly to the public internet. Its entitlements are local fictional database records and **never grant real DayZ access**.

**Tests are destructive:** `tests_postgres/` clears tables in a disposable database and refuses to run unless its database name is exactly `priority_test`. Do not rename a real database to bypass the safeguard.

See [PostgreSQL deployment and security instructions](docs/POSTGRES_DEPLOYMENT.md).
