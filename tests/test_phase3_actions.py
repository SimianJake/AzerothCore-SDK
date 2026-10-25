"""Phase 3 acceptance (PLAN.md §9): actions via injected packets, SMSG-driven results, channels, kill a mob."""

from __future__ import annotations

import asyncio
import math
import time

import pytest
from conftest import CASTERS, CHARACTERS, KOBOLD_FIELD

from azerothcore_sdk import Character, Client
from azerothcore_sdk.character import CommandHandle
from azerothcore_sdk.helpers import kill
from azerothcore_sdk.plumbing import CommandResult, CommandStatus

WARRIOR, OTHER = CHARACTERS[0], CHARACTERS[1]


@pytest.fixture
def casters(realm) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    return CASTERS[0][1], CASTERS[1][1]  # created by conftest before anything logs in


async def go_to_kobolds(char: Character) -> None:
    x, y, z = KOBOLD_FIELD
    r = await char.move_to(x, y, z, tolerance=3.0, timeout=40)
    assert isinstance(r, CommandResult) and r.ok, r

async def logout_if_online(client: Client, *names: str) -> None:
    # One online character per account: the warrior pair and the caster pair share the two test accounts.
    # A client lists (and may log out) only its own account's characters.
    for c in await client.characters.list():
        if c.name in names and c.state.name != "OFFLINE":
            await client._plumbing_characters.logout(c.guid)
            for _ in range(40):
                if (await client.characters.get(c.guid).state()).state.name == "OFFLINE":
                    break
                await asyncio.sleep(0.25)


async def forward_point(char, yards: float) -> tuple[float, float, float]:  # type: ignore[no-untyped-def]
    s = await char.state()
    x, y, z = s.position
    return x + yards * math.cos(s.orientation), y + yards * math.sin(s.orientation), z


async def test_move_to_completes(make_client) -> None:  # type: ignore[no-untyped-def]
    ctl = await make_client("move-test")
    w = await ctl.characters.login(WARRIOR)
    async with w.lease():
        x, y, z = await forward_point(w, 6)
        r = await w.move_to(x, y, z, tolerance=1.5)
        assert isinstance(r, CommandResult)
        assert r.ok, r
        s = await w.state()
        assert math.dist(s.position[:2], (x, y)) <= 1.5 and not s.moving


async def test_new_move_supersedes_running_one(make_client) -> None:  # type: ignore[no-untyped-def]
    ctl = await make_client("supersede-test")
    w = await ctl.characters.login(WARRIOR)
    async with w.lease():
        x, y, z = await forward_point(w, 25)
        first = await w.move_to(x, y, z, wait=False)
        assert isinstance(first, CommandHandle)
        await asyncio.sleep(0.5)
        assert (await w.stop()).ok
        done = await first.done()
        assert done.status == CommandStatus.CANCELLED and done.reason == "SUPERSEDED", done
        await asyncio.sleep(0.5)
        assert not (await w.state()).moving


async def test_unreachable_move_fails_with_reason(make_client) -> None:  # type: ignore[no-untyped-def]
    ctl = await make_client("nopath-test")
    w = await ctl.characters.login(WARRIOR)
    async with w.lease():
        s = await w.state()
        x, y, z = s.position
        r = await w.move_to(x, y, z - 400, timeout=5)  # far underground
        assert isinstance(r, CommandResult)
        assert not r.ok and r.reason in ("NO_PATH", "STOPPED_SHORT", "STUCK"), r


async def test_say_and_whisper(make_client, casters) -> None:  # type: ignore[no-untyped-def]
    ctl = await make_client("chat-test")
    w = await ctl.characters.login(WARRIOR)
    async with w.lease():
        assert (await w.say("hello from the SDK")).ok
        nobody = await w.whisper("Nosuchplayerx", "hi")
        assert nobody.status == CommandStatus.FAILED and nobody.reason == "PLAYER_NOT_FOUND", nobody


async def test_cast_failures_carry_azerothcore_reasons(make_client) -> None:  # type: ignore[no-untyped-def]
    ctl = await make_client("cast-fail-test")
    w = await ctl.characters.login(WARRIOR)
    async with w.lease():
        # Heroic Strike with no target / no rage: AzerothCore answers with SMSG_CAST_FAILED.
        r = await w.cast("Heroic Strike")
        assert isinstance(r, CommandResult)
        assert r.status == CommandStatus.FAILED and r.reason.startswith("SPELL_FAILED_"), r

        # A spell the warrior does not know: AzerothCore stays silent, so the watcher times out.
        silent = await w.cast(133, timeout=3)  # Fireball
        assert isinstance(silent, CommandResult)
        assert silent.status == CommandStatus.TIMEOUT and silent.reason == "NO_RESPONSE", silent


async def test_other_players_spell_does_not_resolve_own_cast(make_client, casters) -> None:  # type: ignore[no-untyped-def]
    mage_name, priest_name = casters
    ctl_mage = await make_client("correlation-mage", 0)
    ctl_priest = await make_client("correlation-priest", 1)
    await logout_if_online(ctl_mage, WARRIOR)
    await logout_if_online(ctl_priest, OTHER)
    mage = await ctl_mage.characters.login(mage_name)
    priest = await ctl_priest.characters.login(priest_name)
    try:
        await _correlation(mage, priest)
    finally:
        await logout_if_online(ctl_mage, mage_name)
        await logout_if_online(ctl_priest, priest_name)


async def _correlation(mage, priest) -> None:  # type: ignore[no-untyped-def]
    async with mage.lease(), priest.lease():
        await asyncio.gather(go_to_kobolds(mage), go_to_kobolds(priest))
        mobs = [m for m in await mage.nearby("creature", radius=60, hostile=True) if m.attackable]
        assert mobs, "no kobold near the mage"
        target = mobs[0]
        if target.distance > 25:
            x, y, z = target.position
            await mage.move_to(x, y, z, tolerance=20)
        await mage.face(target)

        started = time.monotonic()
        fireball = await mage.cast("Fireball", target, wait=False)
        assert isinstance(fireball, CommandHandle)
        await asyncio.sleep(0.3)
        # While the mage is still casting, the priest casts on the mage: SMSG_SPELL_GO for the priest's
        # spell reaches the mage too, but its caster guid differs, so the mage's watcher must ignore it.
        heal = await priest.cast("Lesser Heal", mage)
        assert isinstance(heal, CommandResult)
        result = await fireball.done()
        elapsed = time.monotonic() - started

        # The priest's spell landed on the mage mid-cast, and the mage's own cast still resolved on its own
        # SMSG_SPELL_GO: Fireball rank 1 (id 133, 1.5 s cast), not the priest's heal.
        assert heal.ok, heal
        assert result.ok, result
        assert result.spell_id == 133 and heal.spell_id != 133, (result, heal)
        assert elapsed >= 1.2, (result, elapsed)


async def test_kill_a_mob(make_client) -> None:  # type: ignore[no-untyped-def]
    ctl = await make_client("kill-test")
    w = await ctl.characters.login(WARRIOR)
    async with w.lease():
        await go_to_kobolds(w)
        nearby = await w.nearby("creature", radius=60, hostile=True)
        mobs = [m for m in nearby if m.attackable and m.level <= 3]
        assert mobs, f"no low-level hostile creature near the warrior: {[(m.name, m.level) for m in nearby]}"
        result = await kill(w, mobs[0], spells=["Heroic Strike"], timeout=120)
        assert result.killed, result
        # Every cast attempt resolved to a terminal status with a reason when it failed.
        for _spell, r in result.casts:
            assert r.done and (r.ok or r.reason), r
        await w.stop_attack()


async def test_commands_need_the_lease_token(make_client) -> None:  # type: ignore[no-untyped-def]
    ctl = await make_client("no-token", 1)
    other = await ctl.characters.login(OTHER)
    r = await other.say("not leased")
    assert r.status == CommandStatus.FAILED and r.reason == "INVALID_LEASE_TOKEN", r
