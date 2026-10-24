"""pytest fixtures: one worldserver for the whole session, started and stopped by tests/harness.py.

Run from the repo root:  python\\.venv\\Scripts\\python.exe -m pytest tests -v
Needs your own test accounts and characters, named in environment variables or in tests/local.env (gitignored;
copy tests/local.env.example): ACSDK_TEST_CHARACTERS, two characters on two different accounts matching
ACSDK.HeadlessAccountPrefix; ACSDK_TEST_CASTERS, a mage and a priest to create on those accounts. Enables
ACSDK.Debug.Enable for the DebugService commands.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import Realm, read_token, set_module_option

from azerothcore_sdk import Client

_LOCAL_ENV = Path(__file__).resolve().parent / "local.env"
if _LOCAL_ENV.exists():
    for _line in _LOCAL_ENV.read_text().splitlines():
        _key, _sep, _value = _line.partition("=")
        if _sep and not _line.lstrip().startswith("#"):
            os.environ.setdefault(_key.strip(), _value.strip())


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        pytest.exit(f"set {name} (environment or tests/local.env; see tests/local.env.example)", returncode=4)
    return value


TARGET = os.environ.get("ACSDK_TARGET", "127.0.0.1:50051")
CHARACTERS = _required("ACSDK_TEST_CHARACTERS").split(",")
# A mage and a priest on the two test accounts, for cast-correlation tests: (account, name, class id).
CASTERS = [(account, name, int(cls)) for account, name, cls in
           (c.split(":") for c in _required("ACSDK_TEST_CASTERS").split(","))]
# Spot ~90 yd from Northshire Abbey with level 1 Kobold Vermin within ~40 yd.
KOBOLD_FIELD = (-8830.0, -128.0, 82.8)
LEASE_TTL_SEC = 4  # short so expiry tests are quick


def _provision_casters(realm: Realm) -> None:
    """Create the caster test characters through the dev-only DebugService (AzerothCore's own char-create
    handler). Runs before anything logs in: an account with an online character cannot create one."""
    from azerothcore_sdk.plumbing import channel as _channel  # noqa: F401  (puts _generated on sys.path)

    from acsdk.v1 import debug_pb2, debug_pb2_grpc

    async def create() -> None:
        async with Client(TARGET, token=read_token(), controller_id="provision") as c:
            dbg = debug_pb2_grpc.DebugServiceStub(c.channel)
            for account, name, cls in CASTERS:
                req = debug_pb2.CreateCharacterRequest(account_username=account, name=name, race=1, class_id=cls, gender=0)
                r = await dbg.CreateCharacter(req, timeout=30)
                assert r.ok or r.reason == "NAME_IN_USE", r

    asyncio.run(create())


@pytest.fixture(scope="session")
def realm() -> Iterator[Realm]:
    set_module_option("ACSDK.Debug.Enable", "1")
    set_module_option("ACSDK.Lease.TtlSec", str(LEASE_TTL_SEC))
    set_module_option("ACSDK.RawPackets.Enable", "1")      # group invites in the prompt test
    set_module_option("ACSDK.EventQueueSize", "256")       # small, so the stalled-subscriber test overflows
    r = Realm()
    r.start()
    _provision_casters(r)
    # Start every run from a known state: alive, in Northshire (works for offline characters too).
    for name in CHARACTERS + [c[1] for c in CASTERS]:
        r.console(f"revive {name}", settle=0.5)
        r.console(f"tele name {name} NorthshireValley", settle=0.5)
    try:
        yield r
    finally:
        code = r.stop()
        set_module_option("ACSDK.Lease.TtlSec", "15")
        assert code == 0, f"worldserver exit code {code}"


@pytest.fixture(scope="session")
def token(realm: Realm) -> str:
    return read_token()


@pytest_asyncio.fixture
async def make_client(token: str) -> AsyncIterator[object]:
    clients: list[Client] = []

    async def _make(controller_id: str, server_token: str | None = None) -> Client:
        c = Client(TARGET, token=server_token or token, controller_id=controller_id)
        await c.connect()
        clients.append(c)
        return c

    yield _make
    for c in clients:
        await c.close()
