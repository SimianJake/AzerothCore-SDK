"""Phase 2 acceptance (PLAN.md §9): leases, observers, impersonation, takeover, orphaning, reflexes, kicks."""

from __future__ import annotations

import asyncio
import math

import pytest
from conftest import CHARACTERS, LEASE_TTL_SEC
from harness import Realm

from azerothcore_sdk import Character, Client, LeaseError, Lifecycle, Unauthenticated
from azerothcore_sdk.plumbing import CommandResult

A, B = CHARACTERS[0], CHARACTERS[1]


async def say(client: Client, char: Character, text: str, token: str | None = None) -> CommandResult:
    """Say through `client`, with an explicit token (default: the character's own lease token)."""
    return await client._plumbing_actions.say(char.guid, char.lease_token if token is None else token, text)


async def move(client: Client, char: Character, x: float, y: float, z: float, token: str | None = None) -> CommandResult:
    """Start a MoveTo without waiting for it to finish."""
    return await client._plumbing_actions.move_to(char.guid, char.lease_token if token is None else token, x, y, z, wait=False)


async def wait_until(predicate, timeout: float = 10.0, interval: float = 0.25) -> bool:  # type: ignore[no-untyped-def]
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(interval)
    return False


async def test_server_token_required(make_client) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(Unauthenticated):
        await make_client("intruder", server_token="not-the-token")


async def test_two_controllers_and_an_observer(make_client) -> None:  # type: ignore[no-untyped-def]
    ctl_a = await make_client("controller-a")
    ctl_b = await make_client("controller-b")
    observer = await make_client("observer")

    a = await ctl_a.characters.login(A)
    b = await ctl_b.characters.login(B)
    async with a.lease(), b.lease():
        # Each controller commands its own character.
        assert (await say(ctl_a, a, "a speaks")).ok
        assert (await say(ctl_b, b, "b speaks")).ok

        # Neither can command the other's character with its own token.
        assert (await say(ctl_a, b, "hijack", token=a.lease_token)).reason == "INVALID_LEASE_TOKEN"

        # The observer reads both without any token.
        for char, holder in ((a, "controller-a"), (b, "controller-b")):
            state = await observer.characters.get(char.guid).state()
            assert state.in_world and state.state == Lifecycle.ONLINE
            assert state.lease_holder == holder

        leases = {e.guid: e.holder_label for e in await observer.characters.leases()}
        assert leases == {a.guid: "controller-a", b.guid: "controller-b"}


async def test_impersonation_without_token_fails(make_client) -> None:  # type: ignore[no-untyped-def]
    owner = await make_client("controller-a")
    impostor = await make_client("controller-a")  # same label, no token
    a = await owner.characters.login(A)
    async with a.lease():
        stolen = impostor.characters.get(a.guid, A)
        assert (await say(impostor, stolen, "x", token="")).reason == "INVALID_LEASE_TOKEN"
        assert (await say(impostor, stolen, "x", token="0" * 64)).reason == "INVALID_LEASE_TOKEN"

        leases = impostor._plumbing_leases
        assert await leases.release(a.guid, "") == (False, "INVALID_LEASE_TOKEN")
        assert await leases.renew(a.guid, "0" * 64) == (None, "INVALID_LEASE_TOKEN")
        assert await impostor._plumbing_characters.logout(a.guid, "") == (False, "INVALID_LEASE_TOKEN")

        # The impostor cannot claim it either without force.
        with pytest.raises(LeaseError, match="ALREADY_LEASED"):
            await stolen.claim()

        # The real holder is unaffected.
        assert (await say(owner, a, "still mine")).ok


async def test_forced_takeover_invalidates_old_token(make_client) -> None:  # type: ignore[no-untyped-def]
    first = await make_client("first")
    second = await make_client("second")
    a1 = await first.characters.login(A)
    await a1.claim()
    old_token = a1.lease_token

    a2 = second.characters.get(a1.guid, A)
    lease = await a2.claim(force=True)
    try:
        assert lease.previous_holder == "first"
        assert (await say(first, a1, "old", token=old_token)).reason == "INVALID_LEASE_TOKEN"
        assert await first._plumbing_leases.renew(a1.guid, old_token) == (None, "INVALID_LEASE_TOKEN")
        assert (await say(second, a2, "new owner")).ok
    finally:
        await a2.release()
        await a1.release()  # old holder's release is a no-op now


async def test_dead_controller_orphans_character_which_stays_put(make_client) -> None:  # type: ignore[no-untyped-def]
    observer = await make_client("observer")
    doomed = Client(observer.target, token=observer._token, controller_id="doomed")
    await doomed.connect()
    a = await doomed.characters.login(A)
    await a.claim()
    start = await a.state()

    # Start a long walk, then "kill" the controller: stop renewing and drop the connection.
    x, y, z = start.position
    started = await move(doomed, a, x + 30 * math.cos(start.orientation), y + 30 * math.sin(start.orientation), z)
    assert started.status.name == "STARTED", started
    assert a._renewer is not None
    a._renewer.cancel()
    await doomed.close()

    watcher = observer.characters.get(a.guid, A)

    async def orphaned() -> bool:
        return (await watcher.state()).lease_holder == ""

    assert await wait_until(orphaned, timeout=LEASE_TTL_SEC + 5)
    stopped = await watcher.state()
    assert stopped.in_world and not stopped.moving, "movement must stop when the lease expires"
    await asyncio.sleep(3)
    later = await watcher.state()
    assert later.position == stopped.position, "an orphaned character must stay where it is"

    # Re-claimable by anyone.
    async with watcher.lease():
        assert (await say(observer, watcher, "reclaimed")).ok


async def test_gm_teleport_completes_via_reflex(make_client, realm: Realm) -> None:  # type: ignore[no-untyped-def]
    ctl = await make_client("teleport-test")
    a = await ctl.characters.login(A)
    async with a.lease():
        before = await a.state()

        # Far teleport (Eastern Kingdoms -> Kalimdor) needs MSG_MOVE_WORLDPORT_ACK. Alliance city: an Alliance
        # character sent to Orgrimmar gets killed by the guards.
        realm.console(f"tele name {A} Darnassus")

        async def on_kalimdor() -> bool:
            s = await a.state()
            return s.in_world and s.map_id == 1 and not s.teleporting

        assert await wait_until(on_kalimdor, timeout=15), "far teleport did not complete"

        # Near teleport (same map) needs MSG_MOVE_TELEPORT_ACK.
        here = await a.state()
        realm.console(f"tele name {A} Auberdine")

        async def moved_on_map() -> bool:
            s = await a.state()
            return s.in_world and s.map_id == 1 and not s.teleporting and math.dist(s.position, here.position) > 100

        assert await wait_until(moved_on_map, timeout=15), "near teleport did not complete"

        # Put it back where it started for the other tests.
        realm.console(f"tele name {A} NorthshireValley")

        async def home() -> bool:
            s = await a.state()
            return s.in_world and s.map_id == before.map_id and not s.teleporting

        assert await wait_until(home, timeout=15)


async def test_gm_kick_logs_character_out(make_client, realm: Realm) -> None:  # type: ignore[no-untyped-def]
    ctl = await make_client("kick-test")
    b = await ctl.characters.login(B)

    realm.console(f"kick {B}")

    async def offline() -> bool:
        return (await b.state()).state == Lifecycle.OFFLINE

    assert await wait_until(offline, timeout=10)
    # And it can be logged in again afterwards.
    b = await ctl.characters.login(B)
    assert (await b.state()).in_world
