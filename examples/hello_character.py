"""Phase 1 acceptance: connect, show server info, list eligible headless characters.

    python examples\\hello_character.py --token <ACSDK.AuthToken>

The token defaults to the ACSDK_TOKEN environment variable.
"""

import argparse
import asyncio
import os

from azerothcore_sdk import Client


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="127.0.0.1:50051")
    parser.add_argument("--token", default=os.environ.get("ACSDK_TOKEN", ""))
    args = parser.parse_args()

    async with Client(args.target, token=args.token, controller_id="hello") as ac:
        info = await ac.server.info()
        print(f"Connected to realm '{info.realm_name}' ({info.core_revision}), uptime {info.uptime_ms / 1000:.0f}s")

        characters = await ac.characters.list()
        if not characters:
            print("No eligible headless characters (accounts matching ACSDK.HeadlessAccountPrefix).")
        for c in characters:
            state = "online" if c.online else "offline"
            print(f"  {c.name:<12} level {c.level:<3} account {c.account_name:<16} guid {c.guid:<8} {state}")

        print(await ac.server.stats())


if __name__ == "__main__":
    asyncio.run(main())
