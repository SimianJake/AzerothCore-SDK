"""kill(): approach -> face -> attack -> cast loop, composed from plumbing primitives (PLAN.md §4.0)."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from azerothcore_sdk.character import Character, CommandHandle
from azerothcore_sdk.plumbing import CommandResult, CommandStatus, WorldObject

log = logging.getLogger(__name__)

MELEE_RANGE = 4.0


@dataclass
class KillResult:
    killed: bool
    reason: str = ""
    casts: list[tuple[str | int, CommandResult]] = field(default_factory=list)
    seconds: float = 0.0


async def _find(char: Character, guid: int) -> WorldObject | None:
    # By guid, at any distance on the map (a nearby() scan would lose a target that is far away).
    obj, _los = await char._client.world.get_object(char.guid, guid)
    return obj


async def kill(char: Character, target: WorldObject, *, spells: Sequence[str | int] = (), range_: float = MELEE_RANGE,
               timeout: float = 120.0, stop_below_health: float | None = 20.0) -> KillResult:
    """Walk into `range_`, face, start auto-attack, then keep casting `spells` (in order, each when possible)
    until the target dies. Every step is an ordinary command; failures are reported, not hidden.

    If the character's own health drops to `stop_below_health` percent or less, the fight is stopped and the
    result says LOW_HEALTH, so the caller can flee, heal or eat (None fights to the end)."""
    started = time.monotonic()
    result = KillResult(killed=False)
    attack: CommandHandle | None = None

    while time.monotonic() - started < timeout:
        mob = await _find(char, target.guid)
        if mob is None:
            result.reason = "TARGET_GONE"
            break
        if not mob.alive:
            result.killed = True
            break

        me = await char.state()
        if me.dead:
            result.reason = "DIED"
            break
        if stop_below_health is not None and me.max_health and me.health * 100.0 / me.max_health <= stop_below_health:
            result.reason = "LOW_HEALTH"
            break

        if mob.distance > range_:
            x, y, z = mob.position
            # Stop a bit short of the target's centre, on the line towards it.
            mx, my, _ = me.position
            d = math.dist((mx, my), (x, y)) or 1.0
            stop_at = max(range_ - 1.5, 0.5)
            gx, gy = x - (x - mx) / d * stop_at, y - (y - my) / d * stop_at
            moved = await char.move_to(gx, gy, z, tolerance=1.0, timeout=20)
            assert isinstance(moved, CommandResult)
            if not moved.ok and moved.reason not in ("SUPERSEDED", "STOPPED_SHORT"):
                log.info("approach failed: %s", moved.reason)
            continue

        await char.face(mob)

        if attack is None:
            handle = await char.attack(mob, wait=False)
            assert isinstance(handle, CommandHandle)
            attack = handle

        for spell in spells:
            r = await char.cast(spell, mob)
            assert isinstance(r, CommandResult)
            result.casts.append((spell, r))

        if attack is not None:
            try:
                done = await asyncio.wait_for(attack.done(poll_ms=1000), timeout=1.2)
                if done.status == CommandStatus.COMPLETED:
                    result.killed = True
                    break
                if done.status != CommandStatus.CANCELLED:
                    log.info("attack ended: %s %s", done.status.name, done.reason)
                attack = None
            except TimeoutError:
                pass  # still fighting
    else:
        result.reason = "TIMEOUT"

    if not result.killed and attack is not None and attack._result is None:
        await char.stop_attack()
    result.seconds = time.monotonic() - started
    return result

