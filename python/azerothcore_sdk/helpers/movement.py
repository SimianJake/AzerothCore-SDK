"""travel(): long-distance movement composed from FindPath + MoveTo (PLAN.md §4.0, porcelain only).

AzerothCore answers one path query for at most a few hundred yards (MAX_PATH_LENGTH). travel() asks for a
straight (corner) path toward the destination, walks it corner by corner with ordinary MoveTo commands, and
re-plans from wherever the character ends up, until it arrives, dies, stops making progress, or runs out of
time. Every hop is an ordinary command whose result is recorded.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from azerothcore_sdk.character import Character
from azerothcore_sdk.plumbing import CommandResult, PathFlags, StateSection, WorldObject

log = logging.getLogger(__name__)

Point = tuple[float, float, float]


@dataclass
class TravelResult:
    arrived: bool
    reason: str
    start: Point
    end: Point
    start_zone: int
    end_zone: int
    seconds: float
    distance_walked: float
    hops: int
    replans: int
    trail: list[Point] = field(default_factory=list)          # positions after each hop
    planned: list[list[Point]] = field(default_factory=list)  # every path FindPath returned


def _d2(a: Point, b: Point) -> float:
    return math.dist(a[:2], b[:2])


async def travel(char: Character, dest: Point, *, tolerance: float = 4.0, timeout: float = 3600.0,
                 arrive_when: Callable[[int, Point], bool] | None = None, max_stalls: int = 8) -> TravelResult:
    """Walk `char` to `dest` (same map). `arrive_when(zone_id, position)` may end the trip early, e.g. "entered
    Westfall". Needs the character's lease."""
    started = time.monotonic()
    s = await char.state(StateSection.POSITION, StateSection.STATUS)
    start, start_zone = s.position, s.zone_id
    started_dead = s.dead  # a ghost walking back to its corpse
    result = TravelResult(arrived=False, reason="", start=start, end=start, start_zone=start_zone, end_zone=start_zone,
                          seconds=0.0, distance_walked=0.0, hops=0, replans=0, trail=[start])
    best = _d2(start, dest)
    stalls = 0
    pos, zone = start, start_zone

    def done(arrived: bool, reason: str) -> TravelResult:
        result.arrived, result.reason = arrived, reason
        result.end, result.end_zone = pos, zone
        result.seconds = time.monotonic() - started
        return result

    while time.monotonic() - started < timeout:
        if _d2(pos, dest) <= tolerance or (arrive_when and arrive_when(zone, pos)):
            return done(True, "")

        path = await char.find_path(*dest, straight=True)
        result.replans += 1
        result.planned.append([pos, *path.points])
        if not path.points or path.flags & PathFlags.NOPATH:
            log.info("no path from %s: %s", pos, path.flags)
            stalls += 1
            if stalls >= max_stalls:
                return done(False, "NO_PATH")
            continue

        # Walk the corners. Skip ones we are already standing on. When the destination is beyond this query's
        # reach, the path still lists the requested end as its last point; walking there directly would only
        # fail (NO_PATH), so stop at the last navmesh corner and re-plan from it.
        corners = list(path.points)
        if not path.reachable and len(corners) > 1 and _d2(corners[-1], dest) < 1.0:
            corners.pop()
        # A corner can be farther than one MoveTo plans (~296 yards); MoveTo then fails and we re-plan from
        # where we are, which costs one query. (Splitting long legs at ground heights was tried and is worse:
        # GetHeight picks up roofs and towers, e.g. Sentinel Tower, and strands the character on them.)
        for corner in corners:
            if _d2(pos, corner) < 1.5:
                continue
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                break
            r = await char.move_to(*corner, tolerance=2.0, timeout=min(120.0, remaining))
            assert isinstance(r, CommandResult)
            result.hops += 1
            s = await char.state(StateSection.POSITION, StateSection.STATUS)
            result.distance_walked += _d2(pos, s.position)
            pos, zone = s.position, s.zone_id
            result.trail.append(pos)
            if s.dead and not started_dead:
                return done(False, "DIED")
            if _d2(pos, dest) <= tolerance or (arrive_when and arrive_when(zone, pos)):
                return done(True, "")
            if not r.ok:
                log.info("hop to %s ended %s %s; re-planning", corner, r.status.name, r.reason)
                break  # re-plan from here

        # Progress check: are we closer than ever before?
        now = _d2(pos, dest)
        if now < best - 5.0:
            best, stalls = now, 0
        else:
            stalls += 1
            if stalls >= max_stalls:
                return done(False, "NO_PROGRESS")

    return done(False, "TIMEOUT")


async def approach(char: Character, target: WorldObject | Point, *, within: float = 3.0, timeout: float = 60.0) -> bool:
    """Walk to within `within` yards of an NPC, corpse or point (NPC interactions need about 5 yards).
    Uses travel() for long distances. Returns True when close enough."""
    if isinstance(target, WorldObject):
        dest = target.position
    else:
        dest = target
    s = await char.state(StateSection.POSITION)
    if _d2(s.position, dest) <= within:
        return True
    if _d2(s.position, dest) > 250:
        r = await travel(char, dest, tolerance=within, timeout=timeout)
        return r.arrived
    moved = await char.move_to(*dest, tolerance=max(within - 0.5, 0.5), timeout=timeout)
    assert isinstance(moved, CommandResult)
    s = await char.state(StateSection.POSITION)
    return _d2(s.position, dest) <= within
