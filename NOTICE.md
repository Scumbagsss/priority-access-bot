# Public portfolio boundary

The commercial Discord priority-access bot remains **private**. The production implementation, real APIs, operational configuration, sensitive permissions, user records and credentials are not released here.

This repository includes a **separate runnable synthetic FastAPI example** in `api_demo/`. It models an illustrative approval workflow using fictional member/staff principals and a local SQLite entitlement table. It cannot issue access to a game server, validate Steam account ownership, or deploy the commercial Discord bot.

The `src/public_interface.py` file is an unimplemented architectural sketch. It must not be presented as working production code.

No private game-server endpoints or rules are required to run the synthetic API.

See [docs/API_DEMO.md](docs/API_DEMO.md) for limitations, setup and testing.
