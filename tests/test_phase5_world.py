"""Phase 5 acceptance (PLAN.md §9): world queries. Python can plot a FindPath result; LoS/height agree with the
game's own behaviour."""

from __future__ import annotations

import math
from pathlib import Path

from conftest import CHARACTERS, KOBOLD_FIELD

from azerothcore_sdk.helpers import paths_svg
from azerothcore_sdk.plumbing import PathFlags

A = CHARACTERS[0]
OUT = Path(__file__).resolve().parent / "out"


async def test_find_path_can_be_plotted(make_client) -> None:  # type: ignore[no-untyped-def]
    ctl = await make_client("path-test")
    a = await ctl.characters.login(A)
    s = await a.state()

    smooth = await a.find_path(*KOBOLD_FIELD)
    corners = await a.find_path(*KOBOLD_FIELD, straight=True)
    assert smooth.reachable and smooth.flags & PathFlags.NORMAL, smooth.flags
    assert len(smooth.points) >= 2 and len(corners.points) >= 2
    assert math.dist(smooth.points[-1][:2], KOBOLD_FIELD[:2]) < 3.0, smooth.points[-1]
    # A walked path is never shorter than the straight line between its ends.
    assert smooth.length + 0.5 >= math.dist(s.position[:2], KOBOLD_FIELD[:2])

    OUT.mkdir(exist_ok=True)
    svg = paths_svg([("#1f77b4", [s.position, *smooth.points]), ("#ff7f0e", [s.position, *corners.points])],
                    markers=[("start", s.position), ("end", KOBOLD_FIELD)], title="FindPath: Northshire -> kobold field")
    (OUT / "find_path.svg").write_text(svg)
    assert "<polyline" in svg


async def test_height_matches_where_the_character_stands(make_client) -> None:  # type: ignore[no-untyped-def]
    ctl = await make_client("height-test")
    a = await ctl.characters.login(A)
    s = await a.state()
    x, y, z = s.position
    h = await ctl.world.height(a.guid, x, y, z + 5)
    assert h.found and abs(h.height - z) < 1.5, (h, z)
    # Points along a walkable path sit near the ground AzerothCore reports. The navmesh is a simplified
    # surface (detail mesh), so allow a few yards (stairs, slopes near the abbey).
    path = await a.find_path(*KOBOLD_FIELD)
    for px, py, pz in path.points[:: max(1, len(path.points) // 10)]:
        g = await ctl.world.height(a.guid, px, py, pz + 2)
        assert g.found and abs(g.height - pz) < 4.0, ((px, py, pz), g)


async def test_line_of_sight_agrees_with_the_game(make_client) -> None:  # type: ignore[no-untyped-def]
    ctl = await make_client("los-test")
    a = await ctl.characters.login(A)
    async with a.lease():
        x, y, z = KOBOLD_FIELD
        await a.move_to(x, y, z, tolerance=3, timeout=40)
    s = await a.state()
    eye = (s.position[0], s.position[1], s.position[2] + 2.0)

    checked = 0
    for o in await a.nearby("creature", radius=60, alive=False, limit=20):
        found, in_los = await ctl.world.get_object(a.guid, o.guid)
        assert found is not None and found.name == o.name
        mine = await ctl.world.line_of_sight(a.guid, eye, (o.position[0], o.position[1], o.position[2] + 2.0))
        assert mine == in_los, (o.name, mine, in_los)  # same answer as the game's IsWithinLOSInMap
        checked += 1
    assert checked > 0

    # Straight up into open sky is always clear.
    assert await ctl.world.line_of_sight(a.guid, eye, (eye[0], eye[1], eye[2] + 30))


async def test_static_lookups(make_client) -> None:  # type: ignore[no-untyped-def]
    ctl = await make_client("static-test")
    a = await ctl.characters.login(A)
    items = await ctl.world.item_info([25, 999999])  # Worn Shortsword, unknown
    assert [i.id for i in items] == [25] and items[0].name == "Worn Shortsword", items
    mobs = [m for m in await a.nearby("creature", radius=100, alive=False) if m.entry]
    if mobs:
        info = await ctl.world.creature_info([mobs[0].entry])
        assert info and info[0].name == mobs[0].name
    kobold = await ctl.world.creature_info([6])
    assert kobold and kobold[0].name == "Kobold Vermin" and kobold[0].min_level >= 1
