"""Phase 0 Spike A check: Health/GetInfo inside a running worldserver, login, and wrong-password rejection.

    python examples\\ping.py

Log in with an AzerothCore account: set ACSDK_USERNAME and ACSDK_PASSWORD (environment variables).
"""

import argparse
import asyncio
import os

from azerothcore_sdk import Client, Unauthenticated


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="127.0.0.1:50051")
    args = parser.parse_args()

    async with Client(args.target, label="ping") as ac:
        print("serving:", await ac.server.health())
        print(await ac.server.info())
        print("logged in as", ac.session)

    # The server must reject a wrong password.
    try:
        async with Client(args.target, username=os.environ["ACSDK_USERNAME"], password="wrong-password"):
            print("ERROR: a wrong password was accepted")
    except Unauthenticated:
        print("wrong password rejected: UNAUTHENTICATED")


if __name__ == "__main__":
    asyncio.run(main())
