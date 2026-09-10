# Priority Access Bot

> **Portfolio showcase — production source is private and this repository is intentionally non-runnable.**

Application workflow that grants game-server priority access through a private external API after staff approval.

## What it demonstrates

- Priority application panel
- Steam64 validation
- SQLite application state
- Approve/reject workflow
- Cooldown and duplicate protection
- Private API integration layer

## Technology

Python • discord.py • aiohttp • SQLite • REST API

## High-level workflow

1. Submit priority application
2. Validate identity and cooldown
3. Route to staff review
4. On approval call private entitlement adapter
5. Persist result and notify applicant

## Public interface preview

The public skeleton exposes only the architectural surface:

- `submit_priority_request(...)`
- `validate_steam64(...)`
- `review_request(...)`
- `grant_entitlement(...)`

The production implementations behind these methods are withheld. See [`NOTICE.md`](NOTICE.md).

## Repository contents

- `src/public_interface.py` — non-runnable interface skeleton;
- `docs/ARCHITECTURE.md` — high-level design;
- `examples/config.example.jsonc` — sanitized configuration shape;
- `LICENSE` — explicit all-rights-reserved notice.

## Status

Production-tested private project / portfolio edition.
