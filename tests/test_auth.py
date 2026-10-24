"""Account logins (mod-acsdk 1.1, ARCHITECTURE.md §4.3): sessions, owner-only access, bans, revocation, admin.

Keep the number of failed logins per run well under 20: the server's backoff per peer address (shared by every
test client on 127.0.0.1) allows 20 failures before it delays logins. Per username it allows three.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

import grpc
import pytest
from conftest import ACCOUNTS, ADMIN, BANNED, CHARACTERS, RECHECK_SEC, TARGET, Account
from harness import Realm

from azerothcore_sdk import AccountRefused, Client, NotYourCharacter, ServerBusy, Unauthenticated, events
from azerothcore_sdk.plumbing import DebugService
from azerothcore_sdk.plumbing import channel as _channel  # noqa: F401  (puts _generated on sys.path)

from acsdk.v1 import character_pb2, character_pb2_grpc, server_pb2, server_pb2_grpc

A, B = CHARACTERS[0], CHARACTERS[1]


class Stream:
    """Reads an event stream in the background; remembers the events and how the stream ended."""

    def __init__(self, it) -> None:  # type: ignore[no-untyped-def]
        self.events: list[events.Event] = []
        self.error: BaseException | None = None
        self.ended = asyncio.Event()
        self._task = asyncio.create_task(self._run(it))

    async def _run(self, it) -> None:  # type: ignore[no-untyped-def]
        try:
            async for e in it:
                self.events.append(e)
        except Exception as e:  # noqa: BLE001
            self.error = e
        finally:
            self.ended.set()

    async def stop(self) -> None:
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task


async def wait_until(predicate, timeout: float = 10.0) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(0.25)
    return False


async def test_health_and_info_need_no_session(realm: Realm) -> None:
    async with grpc.aio.insecure_channel(TARGET) as ch:
        server = server_pb2_grpc.ServerServiceStub(ch)
        assert (await server.Health(server_pb2.HealthRequest(), timeout=5)).serving
        info = await server.GetInfo(server_pb2.GetInfoRequest(), timeout=5)
        assert info.api_version == 1 and info.api_minor >= 1 and "basic" in info.auth_modes
        assert info.sdk_version == "1.1.0"

        # Everything else needs a session.
        chars = character_pb2_grpc.CharacterServiceStub(ch)
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await chars.List(character_pb2.ListCharactersRequest(), timeout=5)
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await chars.List(character_pb2.ListCharactersRequest(), timeout=5,
                             metadata=[("authorization", "Bearer " + "0" * 64)])
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED


async def test_login_and_whoami(make_client) -> None:  # type: ignore[no-untyped-def]
    ctl = await make_client("whoami-test")
    assert ctl.session.username == ACCOUNTS[0].username.upper()
    assert not ctl.session.admin
    me = await ctl.whoami()
    assert me.account_id == ctl.session.account_id and me.label == "whoami-test"


async def test_wrong_password_and_unknown_account_look_the_same(realm: Realm) -> None:
    errors = []
    pw = ACCOUNTS[0].password
    wrong = pw[:-1] + ("x" if pw[-1:].lower() != "x" else "y")  # same length: stays within AzerothCore's limit
    for account in (Account(ACCOUNTS[0].username, wrong), Account("ACSDKHCNOSUCH", "whatever")):
        with pytest.raises(Unauthenticated) as e:
            await Client(TARGET, username=account.username, password=account.password).connect()
        errors.append(str(e.value))
    assert errors[0] == errors[1], errors


async def test_one_clients_typos_do_not_lock_out_others(realm: Realm) -> None:
    # Three wrong passwords for one (unknown) username: free. The fourth attempt for it is held back ...
    for _ in range(3):
        with pytest.raises(Unauthenticated):
            await Client(TARGET, username="ACSDKHCTYPO", password="wrong").connect()
    with pytest.raises(ServerBusy, match="too many failed logins"):
        await Client(TARGET, username="ACSDKHCTYPO", password="wrong").connect()
    # ... but other accounts on the same machine (same peer address) log in right away.
    ok = Client(TARGET, username=ACCOUNTS[1].username, password=ACCOUNTS[1].password)
    await ok.connect()
    await ok.close()


async def test_list_shows_only_own_characters(make_client) -> None:  # type: ignore[no-untyped-def]
    for i, (mine, theirs) in enumerate(((A, B), (B, A))):
        ctl = await make_client(f"list-{i}", i)
        listed = await ctl.characters.list()
        names = {c.name for c in listed}
        assert mine in names and theirs not in names, names
        assert {c.account_id for c in listed} == {ctl.session.account_id}


async def test_cannot_touch_another_accounts_character(make_client) -> None:  # type: ignore[no-untyped-def]
    ctl_a = await make_client("owner-check-a", 0)
    ctl_b = await make_client("owner-check-b", 1)
    b = await ctl_b.characters.login(B)
    async with b.lease():
        stranger = ctl_a.characters.get(b.guid, B)
        with pytest.raises(NotYourCharacter):
            await ctl_a.characters.login(B)                       # by name
        with pytest.raises(NotYourCharacter):
            await stranger.state()
        with pytest.raises(NotYourCharacter):
            await stranger.claim()
        with pytest.raises(NotYourCharacter):
            await ctl_a._plumbing_actions.say(b.guid, b.lease_token, "with the owner's token")
        with pytest.raises(NotYourCharacter):
            await stranger.nearby()                                # world query centred on it
        with pytest.raises(NotYourCharacter):
            await ctl_a.world.find_path(b.guid, (0.0, 0.0, 0.0))
        with pytest.raises(NotYourCharacter):
            async for _ in stranger.events():                      # subscribe naming it
                pass

        # A's "all my characters" stream never carries B's events.
        a = await ctl_a.characters.login(A)
        mine = Stream(ctl_a.events())
        await asyncio.sleep(0.5)
        text = f"only for b {time.monotonic():.3f}"
        assert (await b.say(text)).ok
        await asyncio.sleep(1.5)
        await mine.stop()
        assert mine.error is None, mine.error
        assert all(e.character_guid != b.guid for e in mine.events), [e for e in mine.events if e.character_guid == b.guid]
        assert a.guid  # A was online, so the stream was live


async def test_banned_account_is_refused_and_a_ban_ends_a_live_session(realm: Realm) -> None:
    # Live session first, then the ban: the periodic recheck ends the session.
    live = Client(TARGET, username=BANNED.username, password=BANNED.password, label="ban-test", relogin=False)
    await live.connect()
    try:
        await live.whoami()
        realm.console(f"ban account {BANNED.username} 1h acsdk test", settle=0.5)

        async def ended() -> bool:
            try:
                await live.whoami()
                return False
            except Unauthenticated:
                return True

        assert await wait_until(ended, timeout=RECHECK_SEC + 10), "a ban must end the account's live session"

        # And a new login is refused (correct password, so this is no failed login).
        with pytest.raises(AccountRefused, match="BANNED"):
            await Client(TARGET, username=BANNED.username, password=BANNED.password).connect()
    finally:
        await live.close()
        realm.console(f"unban account {BANNED.username}", settle=0.5)


async def test_replaced_session_ends_its_leases_and_streams(make_client) -> None:  # type: ignore[no-untyped-def]
    first = await make_client("first-session", 0, relogin=False)
    a = await first.characters.login(A)
    await a.claim()
    old_token = a.lease_token
    stream = Stream(a.events(events.LeaseLost))
    await asyncio.sleep(0.5)

    # The same account logs in again: one session per account, so the first one ends.
    second = await make_client("second-session", 0)
    await asyncio.wait_for(stream.ended.wait(), timeout=10)
    assert isinstance(stream.error, Unauthenticated), stream.error
    lost = [e for e in stream.events if isinstance(e, events.LeaseLost)]
    assert lost and lost[0].reason == "SESSION_ENDED", stream.events

    with pytest.raises(Unauthenticated):
        await first.whoami()
    # The lease ended with the session; its token is useless in the new session too.
    assert (await second.characters.get(a.guid, A).state()).lease_holder == ""
    r = await second._plumbing_actions.say(a.guid, old_token, "old token")
    assert r.reason == "INVALID_LEASE_TOKEN", r
    # The character stayed logged in and the new session can claim it.
    a2 = second.characters.get(a.guid, A)
    async with a2.lease():
        assert (await a2.say("new session")).ok
    if a._renewer:
        a._renewer.cancel()
    a._lease = None


async def test_revoke_command_and_automatic_relogin(make_client, realm: Realm) -> None:  # type: ignore[no-untyped-def]
    ctl = await make_client("revoke-test", 0)  # relogin=True (default)
    a = await ctl.characters.login(A)
    await a.claim()
    out = realm.console(f"acsdk session revoke {ACCOUNTS[0].username}", settle=1.5)
    assert any("Ended the SDK session" in line for line in out), out

    async def lease_gone() -> bool:
        return a.lease_lost is not None

    # The client logs in again by itself on the next call; the lease did not survive (it belonged to the
    # revoked session), which the renew loop reports.
    assert await wait_until(lease_gone, timeout=10)
    assert (await ctl.whoami()).username == ACCOUNTS[0].username.upper()
    assert (await a.state()).lease_holder == ""
    listed = realm.console("acsdk session list", settle=1.0)
    assert any(ACCOUNTS[0].username.upper() in line for line in listed), listed


async def test_admin_only_calls(make_client) -> None:  # type: ignore[no-untyped-def]
    player = await make_client("not-admin", 0)
    with pytest.raises(NotYourCharacter):
        await player.server.stats()
    with pytest.raises(NotYourCharacter):
        await DebugService(player.channel).create_character(ACCOUNTS[0].username, "Zzneverx", race=1, class_id=1)

    admin = await make_client("admin", ADMIN)
    assert admin.session.admin
    assert (await admin.server.stats()).rpcs_total > 0
    # Admin grants no access to other accounts' characters.
    a = await player.characters.login(A)
    with pytest.raises(NotYourCharacter):
        await admin.characters.get(a.guid).state()
    assert await admin.characters.list() == []
