"""Phase 0 Spike B, now on the real API: headless login, stay online, server-side move, say, logout.

(The original spike used a temporary DebugService that Phase 3 replaced with ActionService.)

    python examples\\spike_b.py --token <ACSDK.AuthToken> --character Name [--minutes 10]
"""

import argparse
import asyncio
import math
import os
import time

from azerothcore_sdk import Client


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="127.0.0.1:50051")
    parser.add_argument("--token", default=os.environ.get("ACSDK_TOKEN", ""))
    parser.add_argument("--character", required=True)
    parser.add_argument("--minutes", type=float, default=10.0, help="how long to keep the character online")
    parser.add_argument("--distance", type=float, default=8.0, help="how far to walk (yards)")
    args = parser.parse_args()

    async with Client(args.target, token=args.token, controller_id="spike-b") as ac:
        char = await ac.characters.login(args.character)
        async with char.lease():
            s = await char.state()
            x, y, z = s.position
            print(f"online: map {s.map_id} zone {s.zone_id} ({x:.1f}, {y:.1f}, {z:.1f})")

            tx, ty = x + args.distance * math.cos(s.orientation), y + args.distance * math.sin(s.orientation)
            print("move:", await char.move_to(tx, ty, z, tolerance=1.5))
            print("say:", await char.say("hello"))  # COMPLETED = the server's SMSG_MESSAGECHAT echo arrived

            deadline = time.monotonic() + args.minutes * 60
            while time.monotonic() < deadline:
                await asyncio.sleep(min(60, max(0.0, deadline - time.monotonic())))
                s = await char.state()
                print(f"[{time.strftime('%H:%M:%S')}] {s.state.name} {s.position}")

            await char.logout()
        print("logged out")


if __name__ == "__main__":
    asyncio.run(main())
