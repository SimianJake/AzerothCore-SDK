"""Phase 3 acceptance: find a hostile mob, path to it, attack it, cast on it, and kill it.

    python examples\\kill_a_mob.py --character <name> [--spell "Heroic Strike"]

Every await resolves to a terminal result with AzerothCore's own reason on failure.

Log in with an AzerothCore account: set ACSDK_USERNAME and ACSDK_PASSWORD (environment variables).
"""

import argparse
import asyncio

from azerothcore_sdk import Client
from azerothcore_sdk.helpers import kill


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="127.0.0.1:50051")
    parser.add_argument("--character", required=True)
    parser.add_argument("--spell", action="append", default=[], help="spell name or id to cast each round (repeatable)")
    parser.add_argument("--radius", type=float, default=100.0)
    args = parser.parse_args()

    async with Client(args.target, label="kill-a-mob") as ac:
        char = await ac.characters.login(args.character)
        async with char.lease():
            mobs = [m for m in await char.nearby("creature", radius=args.radius, hostile=True) if m.attackable]
            if not mobs:
                print("no attackable hostile creature nearby")
                return
            mob = mobs[0]
            print(f"target: {mob.name} (level {mob.level}) at {mob.distance:.1f} yd")

            spells = [int(s) if s.isdigit() else s for s in args.spell]
            result = await kill(char, mob, spells=spells)
            for spell, r in result.casts:
                print(f"  cast {spell}: {r.status.name} {r.reason}")
            print(f"killed={result.killed} reason={result.reason!r} in {result.seconds:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
