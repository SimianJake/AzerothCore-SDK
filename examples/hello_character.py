"""Phase 1 acceptance: connect, show server info, list your account's characters.

    python examples\\hello_character.py

Log in with an AzerothCore account: set ACSDK_USERNAME and ACSDK_PASSWORD (environment variables).
"""

import argparse
import asyncio

from azerothcore_sdk import Client


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="127.0.0.1:50051")
    args = parser.parse_args()

    async with Client(args.target, label="hello") as ac:
        info = await ac.server.info()
        print(f"Connected to realm '{info.realm_name}' ({info.core_revision}), uptime {info.uptime_ms / 1000:.0f}s")

        characters = await ac.characters.list()
        if not characters:
            print(f"Account {ac.session.username} has no characters.")
        for c in characters:
            state = "online" if c.online else "offline"
            print(f"  {c.name:<12} level {c.level:<3} account {c.account_name:<16} guid {c.guid:<8} {state}")

        if ac.session.admin:
            print(await ac.server.stats())  # admin sessions only


if __name__ == "__main__":
    asyncio.run(main())
