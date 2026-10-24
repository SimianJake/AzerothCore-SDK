"""recover(): get a dead character back on its feet, as a player would (porcelain only).

Release spirit, walk the ghost back to the corpse, wait out the reclaim delay and resurrect there. If the corpse
cannot be reached, fall back to the graveyard's spirit healer (resurrection sickness) when allowed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from azerothcore_sdk.character import Character
from azerothcore_sdk.helpers.movement import approach, travel
from azerothcore_sdk.plumbing import StateSection

log = logging.getLogger(__name__)

RECLAIM_RANGE = 30.0  # AzerothCore allows 39 yards; stay well inside it
SPIRIT_HEALER_NAMES = ("Spirit Healer", "Spirit Guide")


@dataclass
class RecoverResult:
    alive: bool
    reason: str = ""      # NO_CORPSE, CORPSE_UNREACHABLE, RECLAIM_FAILED:<reason>, TIMEOUT
    method: str = ""      # ALREADY_ALIVE, CORPSE or SPIRIT_HEALER
    seconds: float = 0.0
    corpse_run: str = ""  # why the run back to the corpse failed (travel() reason), when it did


async def recover(char: Character, *, timeout: float = 900.0, use_spirit_healer: bool = True) -> RecoverResult:
    """Bring `char` back to life. Needs the character's lease."""
    started = time.monotonic()

    corpse_run = ""

    def done(alive: bool, reason: str = "", method: str = "") -> RecoverResult:
        return RecoverResult(alive, reason, method, time.monotonic() - started, corpse_run)

    s = await char.state(StateSection.STATUS)
    if not s.dead:
        return done(True, method="ALREADY_ALIVE")

    if not s.ghost:
        r = await char.release_spirit()
        if not r.ok:
            return done(False, f"RELEASE_FAILED:{r.reason}")

    s = await char.state(StateSection.CORPSE, StateSection.POSITION)
    if not s.has_corpse:
        return await _spirit_healer(char, done) if use_spirit_healer else done(False, "NO_CORPSE")

    reached = False
    if s.corpse_map_id != s.map_id:
        corpse_run = "OTHER_MAP"
    else:
        left = timeout - (time.monotonic() - started)
        trip = await travel(char, s.corpse_position, tolerance=RECLAIM_RANGE / 2, timeout=max(left, 1.0))
        reached = trip.arrived
        corpse_run = "" if trip.arrived else trip.reason or "NOT_ARRIVED"
        log.info("ghost run to corpse: %s %s (%.0f yd)", trip.arrived, trip.reason, trip.distance_walked)

    if not reached:
        if use_spirit_healer:
            return await _spirit_healer(char, done)
        return done(False, "CORPSE_UNREACHABLE")

    s = await char.state(StateSection.CORPSE)
    if s.corpse_reclaim_in_s:
        await asyncio.sleep(s.corpse_reclaim_in_s + 0.5)
    r = await char.reclaim_corpse()
    if r.ok:
        return done(True, method="CORPSE")
    return done(False, f"RECLAIM_FAILED:{r.reason}")


async def _spirit_healer(char: Character, done) -> RecoverResult:  # type: ignore[no-untyped-def]
    healers = [o for o in await char.nearby("creature", radius=80, alive=True, limit=100) if o.name in SPIRIT_HEALER_NAMES]
    if not healers:
        return done(False, "NO_SPIRIT_HEALER")
    healer = min(healers, key=lambda o: o.distance)
    if not await approach(char, healer, within=4.0):
        return done(False, "SPIRIT_HEALER_UNREACHABLE")
    r = await char.spirit_healer(healer)
    if r.ok:
        return done(True, method="SPIRIT_HEALER")
    return done(False, f"SPIRIT_HEALER_FAILED:{r.reason}")
