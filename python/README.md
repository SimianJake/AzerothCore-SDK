# azerothcore-sdk

Async Python SDK for driving "headless" characters (logged in with no game client) on an
AzerothCore 3.3.5a server running the `mod-acsdk` module.

```bash
pip install azerothcore-sdk          # or: pip install azerothcore_sdk-<version>-py3-none-any.whl
```

```python
import asyncio
from azerothcore_sdk import Client

async def main():
    # username/password default to the ACSDK_USERNAME / ACSDK_PASSWORD environment variables
    async with Client("127.0.0.1:50051", username="acsdkhcalex", password="...", label="my-bot") as ac:
        char = await ac.characters.login("Examplechar")
        async with char.lease():
            print(await char.state())
            r = await char.say("hello")
            print(r.ok, r.status, r.reason)

asyncio.run(main())
```

Requires Python 3.11+ and a worldserver with `mod-acsdk` 1.1 or newer. You log in with an AzerothCore
account and can see and drive only that account's characters. The server listens on `127.0.0.1:50051`
(loopback only) and the connection is not encrypted, so reach a remote server through an SSH tunnel.

The full guide (setup, every call, error reasons) is `docs/SDK_GUIDE.md` in the SDK repository;
the gRPC protos are in `proto/`.
