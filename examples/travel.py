"""Long-distance pathfinding: walk a headless character to a far point with helpers.travel(), then write the
route as an SVG and a JSON log.

    python examples\\travel.py --character <name> --to -10624.5 1096.66 33.76 \\
        --name "Sentinel Hill" --timeout 3600 --out travel_out

Log in with an AzerothCore account: set ACSDK_USERNAME and ACSDK_PASSWORD (environment variables).
"""

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path

from azerothcore_sdk import Client
from azerothcore_sdk.helpers import paths_svg, travel

WESTFALL_ZONE = 40


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="127.0.0.1:50051")
    parser.add_argument("--character", required=True)
    parser.add_argument("--to", type=float, nargs=3, required=True, metavar=("X", "Y", "Z"))
    parser.add_argument("--name", default="destination")
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument("--out", default="travel_out")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    dest = tuple(args.to)

    async with Client(args.target, label="travel") as ac:
        char = await ac.characters.login(args.character)
        async with char.lease():
            first_zone_change: dict[str, object] = {}

            def note_zone(zone: int, pos: tuple[float, float, float]) -> bool:
                if zone == WESTFALL_ZONE and not first_zone_change:
                    first_zone_change.update(zone=zone, position=pos, at_s=round(time.monotonic() - t0, 1))
                    print(f"entered Westfall at {pos}", flush=True)
                return False  # keep going to the destination itself

            t0 = time.monotonic()
            result = await travel(char, dest, tolerance=5.0, timeout=args.timeout, arrive_when=note_zone)

    summary = {
        "character": args.character,
        "destination_name": args.name,
        "destination": dest,
        "arrived": result.arrived,
        "reason": result.reason,
        "start": result.start,
        "start_zone": result.start_zone,
        "end": result.end,
        "end_zone": result.end_zone,
        "entered_westfall": first_zone_change or None,
        "seconds": round(result.seconds, 1),
        "distance_walked_yd": round(result.distance_walked, 1),
        "straight_line_yd": round(((dest[0] - result.start[0]) ** 2 + (dest[1] - result.start[1]) ** 2) ** 0.5, 1),
        "hops": result.hops,
        "path_queries": result.replans,
    }
    (out / "travel.json").write_text(json.dumps(summary, indent=2))
    svg = paths_svg([("#bbb", p) for p in result.planned] + [("#1f77b4", result.trail)],
                    markers=[("start", result.start), (args.name, dest), ("end", result.end)],
                    title=f"{args.character}: {args.name} ({'arrived' if result.arrived else result.reason})")
    (out / "travel.svg").write_text(svg)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
