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
    async with Client("127.0.0.1:50051", token="<ACSDK.AuthToken>", controller_id="my-bot") as ac:
        char = await ac.characters.login("Examplechar")
        async with char.lease():
            print(await char.state())
            r = await char.say("hello")
            print(r.ok, r.status, r.reason)

asyncio.run(main())
```

Requires Python 3.11+ and a worldserver with `mod-acsdk` enabled (`ACSDK.AuthToken` set). The
server listens on `127.0.0.1:50051` by default and the connection is not encrypted, so reach a
remote server through an SSH tunnel or VPN rather than exposing the port.

The full guide (setup, every call, error reasons) is `docs/SDK_GUIDE.md` in the SDK repository;
the gRPC protos are in `proto/`.
