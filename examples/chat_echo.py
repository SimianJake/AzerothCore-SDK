"""Phase 4 acceptance: echo every whisper a headless character receives back to its sender.

    python examples\\chat_echo.py --token <ACSDK.AuthToken> --character <name>

Runs until Ctrl+C. Uses one event stream (Chat events of this character) and Whisper commands.
"""

import argparse
import asyncio
import os

from azerothcore_sdk import Client, events


async def name_of(ac: Client, char, guid: int, cache: dict[int, str]) -> str | None:  # type: ignore[no-untyped-def]
    """Resolve a player guid to a name: eligible characters first, then players near the character."""
    if guid in cache:
        return cache[guid]
    for c in await ac.characters.list():
        cache[c.guid] = c.name
    if guid not in cache:
        for p in await char.nearby("player", radius=100, alive=False):
            cache[p.guid] = p.name
    return cache.get(guid)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="127.0.0.1:50051")
    parser.add_argument("--token", default=os.environ.get("ACSDK_TOKEN", ""))
    parser.add_argument("--character", required=True)
    args = parser.parse_args()

    async with Client(args.target, token=args.token, controller_id="chat-echo") as ac:
        char = await ac.characters.login(args.character)
        async with char.lease():
            print(f"{char.name} is echoing whispers (Ctrl+C to stop)")
            names: dict[int, str] = {}
            async for ev in char.events(events.Chat):
                assert isinstance(ev, events.Chat)
                if not ev.is_whisper or ev.from_self:
                    continue
                sender = await name_of(ac, char, ev.sender_guid, names)
                if not sender:
                    print(f"whisper from unknown guid {ev.sender_guid}: {ev.text!r}")
                    continue
                r = await char.whisper(sender, ev.text)
                print(f"{sender}: {ev.text!r} -> echoed ({r.status.name} {r.reason})")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
