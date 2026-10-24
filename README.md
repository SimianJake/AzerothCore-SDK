# AzerothCore SDK

Python SDK for driving headless characters (no game client) on an AzerothCore 3.3.5a server.

- **Using it:** [docs/SDK_GUIDE.md](docs/SDK_GUIDE.md)
- **Building the server on Windows:** [docs/windows-build.md](docs/windows-build.md)
- **Design and decisions:** [PLAN.md](PLAN.md)
- **Examples:** `examples/`
- **Tests:** `python\.venv\Scripts\python.exe -m pytest tests -v` (starts its own worldserver)
- **Installing the SDK and writing code (for anyone given the package):** [docs/installation.md](docs/installation.md)
- **Building and hosting the pip package:** [docs/PUBLISHING.md](docs/PUBLISHING.md), `scripts\build_sdk.ps1`
- **Release package:** `scripts\package.ps1` (git tag `Beta-Build-ac-sdk` marks the first beta)
