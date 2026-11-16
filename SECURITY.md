# Security — synthetic local demo

The public `api_demo/` service is a **portfolio-only code sample**, not a hardened identity provider or production entitlement server.

It implements locally provisioned usernames, salted scrypt password hashes, opaque 60-minute bearer sessions, token hashing at rest, logout revocation, re-checked staff roles and ownership boundaries. Accounts are provisioned manually through a `getpass` CLI; no public registration route exists.

**Do not deploy this example to the public internet.** It lacks login throttling and account lockouts, credential rotation, identity-provider integration, HTTPS termination, monitoring, multi-factor authentication and deployment security controls.

Do not commit production secrets, private access APIs, live game-server records, user data, databases or backups. `*.sqlite3` and `.env` are ignored by Git. If a credential is exposed, rotate it immediately and investigate downstream access; merely rewriting Git history does not revoke it.

All identities and SteamID-shaped values in the test suite are fictional. The project cannot grant, extend or revoke real server priority.
