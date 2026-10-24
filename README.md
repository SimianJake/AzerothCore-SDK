# AzerothCore SDK

Python SDK for driving headless characters (logged in with no game client) on an AzerothCore 3.3.5a server.

The SDK talks to the server through the **[mod-acsdk](https://github.com/SimianJake/mod-acsdk)** module. The two live in separate repositories:

- **Server owners and admins:** install the module from [mod-acsdk](https://github.com/SimianJake/mod-acsdk). It is a drop-in module for an
  unmodified AzerothCore and needs nothing from this repository.
- **People writing scripts against a server that already runs the module:** you only need this SDK.
  Start with [docs/installation.md](docs/installation.md).

## In this repository

- **Using the SDK:** [docs/SDK_GUIDE.md](docs/SDK_GUIDE.md) (every call and error reason) and
  [docs/installation.md](docs/installation.md) (step by step for new users)
- **Examples:** `examples/`
- **Building and hosting the pip package:** [docs/PUBLISHING.md](docs/PUBLISHING.md), `scripts\build_sdk.ps1`
- **Protos:** `proto/`, a copy of the module's API used to generate the Python stubs ([proto/README.md](proto/README.md))
- **Design and decisions:** [PLAN.md](PLAN.md); how everything works, file by file:
  [ARCHITECTURE.md](https://github.com/SimianJake/mod-acsdk/blob/main/docs/ARCHITECTURE.md) in the module repository
- **Tests:** `python\.venv\Scripts\python.exe -m pytest tests -v`. They start a local worldserver built with the
  module (see `tests/harness.py`) and need test accounts named in `tests/local.env` (see `tests/local.env.example`).
