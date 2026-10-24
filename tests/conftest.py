"""pytest fixtures: one worldserver for the whole session, started and stopped by tests/harness.py.

Run from the repo root:  python\\.venv\\Scripts\\python.exe -m pytest tests -v
Needs your own test accounts and characters, named in environment variables or in tests/local.env (gitignored;
copy tests/local.env.example):
  ACSDK_TEST_ACCOUNTS    two accounts as name:password,name:password, names matching ACSDK.HeadlessAccountPrefix
  ACSDK_TEST_CHARACTERS  one existing character on each of those accounts, in the same order
  ACSDK_TEST_CASTERS     a mage and a priest to create on those accounts (account:name:class)
Enables ACSDK.Debug.Enable and ACSDK.Auth.AllowGmAccounts, and creates two more accounts through the worldserver
console with fresh random passwords each run: an admin (GM level 3, for DebugService and GetStats) and one the
ban tests ban and unban.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import Realm, set_module_option

from azerothcore_sdk import Client

_LOCAL_ENV = Path(__file__).resolve().parent / "local.env"
if _LOCAL_ENV.exists():
    for _line in _LOCAL_ENV.read_text().splitlines():
        _key, _sep, _value = _line.partition("=")
        if _sep and not _line.lstrip().startswith("#"):
            os.environ.setdefault(_key.strip(), _value.strip())


_MISSING: list[str] = []


def _required(name: str) -> str:
    """The variable's value; a missing one stops the run when a test first needs the realm (tests that need no
    server, such as test_proto_pinned.py, still run)."""
    value = os.environ.get(name, "")
    if not value:
        _MISSING.append(name)
    return value


@dataclass(frozen=True)
class Account:
    username: str
    password: str


TARGET = os.environ.get("ACSDK_TARGET", "127.0.0.1:50051")
# ACCOUNTS[i] owns CHARACTERS[i] and CASTERS[i].
ACCOUNTS = [Account(*a.split(":", 1)) for a in _required("ACSDK_TEST_ACCOUNTS").split(",") if a]
CHARACTERS = _required("ACSDK_TEST_CHARACTERS").split(",")
# A mage and a priest on the two test accounts, for cast-correlation tests: (account, name, class id).
CASTERS = [(account, name, int(cls)) for account, name, cls in
           (c.split(":") for c in _required("ACSDK_TEST_CASTERS").split(",") if c)]
# Created by the realm fixture; passwords are random per run and never stored.
ADMIN = Account("ACSDKHCADMIN", secrets.token_hex(6))
BANNED = Account("ACSDKHCBANTEST", secrets.token_hex(6))
# Spot ~90 yd from Northshire Abbey with level 1 Kobold Vermin within ~40 yd.
KOBOLD_FIELD = (-8830.0, -128.0, 82.8)
LEASE_TTL_SEC = 4  # short so expiry tests are quick
RECHECK_SEC = 5    # session revocation recheck, short so the ban test is quick


def _ensure_account(realm: Realm, account: Account, gm_level: int = 0) -> None:
    """Create the account if needed and (re)set its password and GM level through the worldserver console."""
    realm.console(f"account create {account.username} {account.password}", settle=1.0)
    realm.console(f"account set password {account.username} {account.password} {account.password}", settle=1.0)
    realm.console(f"account set gmlevel {account.username} {gm_level} -1", settle=1.0)


def _provision_casters() -> None:
    """Create the caster test characters through the dev-only DebugService (AzerothCore's own char-create
    handler), as the admin. Runs before anything logs in: an account with an online character cannot create one."""
    from azerothcore_sdk.plumbing import DebugService

    async def create() -> None:
        async with Client(TARGET, username=ADMIN.username, password=ADMIN.password, label="provision") as c:
            dbg = DebugService(c.channel)
            for account, name, cls in CASTERS:
                ok, reason, _guid, _name = await dbg.create_character(account, name, race=1, class_id=cls)
                assert ok or reason == "NAME_IN_USE", reason

    asyncio.run(create())


@pytest.fixture(scope="session")
def realm() -> Iterator[Realm]:
    if _MISSING:
        pytest.exit(f"set {', '.join(_MISSING)} (environment or tests/local.env; see tests/local.env.example)", returncode=4)
    set_module_option("ACSDK.Debug.Enable", "1")
    set_module_option("ACSDK.Auth.AllowGmAccounts", "1")     # the admin test account
    set_module_option("ACSDK.Auth.RecheckSec", str(RECHECK_SEC))
    set_module_option("ACSDK.Lease.TtlSec", str(LEASE_TTL_SEC))
    set_module_option("ACSDK.RawPackets.Enable", "1")      # group invites in the prompt test
    set_module_option("ACSDK.EventQueueSize", "256")       # small, so the stalled-subscriber test overflows
    r = Realm()
    r.start()
    _ensure_account(r, ADMIN, gm_level=3)
    _ensure_account(r, BANNED)
    r.console(f"unban account {BANNED.username}", settle=0.5)
    _provision_casters()
    # Start every run from a known state: alive, in Northshire (works for offline characters too).
    for name in CHARACTERS + [c[1] for c in CASTERS]:
        r.console(f"revive {name}", settle=0.5)
        r.console(f"tele name {name} NorthshireValley", settle=0.5)
    try:
        yield r
    finally:
        code = r.stop()
        set_module_option("ACSDK.Lease.TtlSec", "15")
        set_module_option("ACSDK.Auth.AllowGmAccounts", "0")
        set_module_option("ACSDK.Auth.RecheckSec", "60")
        assert code == 0, f"worldserver exit code {code}"


@pytest_asyncio.fixture
async def make_client(realm: Realm) -> AsyncIterator[object]:
    """make_client(label, account=0): a connected Client logged in as ACCOUNTS[account] (or an Account).

    One session per account: a second client for the same account ends the first one's session."""
    clients: list[Client] = []

    async def _make(label: str, account: int | Account = 0, **kwargs: object) -> Client:
        acct = ACCOUNTS[account] if isinstance(account, int) else account
        c = Client(TARGET, username=acct.username, password=acct.password, label=label, **kwargs)  # type: ignore[arg-type]
        await c.connect()
        clients.append(c)
        return c

    yield _make
    for c in clients:
        await c.close()
