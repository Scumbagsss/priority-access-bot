# Security policy

The public `api_demo/` service is a **local-only synthetic code sample**, not a hardened authentication/authorization server. Its `X-API-Key` map is configured using **required environment variables**; the CLI walkthrough binds to `127.0.0.1`.

Do not deploy this example to the public internet. It lacks production authentication, rate limiting, credential rotation, TLS termination, operational monitoring and identity-provider integration.

Never commit:
- bot tokens, private API keys, real Discord or Steam account identifiers;
- real player or admin data, databases, logs, backups or secret configuration;
- full proprietary entitlement delivery logic or customer-specific endpoints.

If a key is exposed, revoke/rotate it and investigate the entire history; rewriting Git history alone does not invalidate a compromised secret.

Synthetic test tokens in `tests/` are fixtures only and confer no access to anything.
