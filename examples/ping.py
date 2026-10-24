"""Phase 0 Spike A check: Health/GetInfo inside a running worldserver, and token rejection.

    python examples\\ping.py --token <ACSDK.AuthToken>
"""

import argparse
import asyncio
import os

from azerothcore_sdk import Client, Unauthenticated


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="127.0.0.1:50051")
    parser.add_argument("--token", default=os.environ.get("ACSDK_TOKEN", ""))
    args = parser.parse_args()

    async with Client(args.target, token=args.token, controller_id="ping") as ac:
        print("serving:", await ac.server.health())
        print(await ac.server.info())

    # The listener must reject calls without the right token.
    try:
        async with Client(args.target, token="wrong-token"):
            print("ERROR: call with a wrong token was accepted")
    except Unauthenticated:
        print("wrong token rejected: UNAUTHENTICATED")


if __name__ == "__main__":
    asyncio.run(main())
