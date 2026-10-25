"""Phase 4 acceptance (PLAN.md §9): event streams, prompts, no duplicate events, stalled subscribers."""

from __future__ import annotations

import asyncio
import contextlib
import os
import struct
import time
from collections.abc import AsyncIterator

from conftest import ADMIN, CASTERS, CHARACTERS

from azerothcore_sdk import Character, Client, events
from azerothcore_sdk.plumbing import CommandStatus, SessionAuth, StateSection, connect
from azerothcore_sdk.plumbing.eventstream import EventService

A, B = CHARACTERS[0], CHARACTERS[1]
MAGE = CASTERS[0][1]  # shares the first test account with the warrior

CMSG_GROUP_INVITE = 0x06E   # cstring member name, uint32 (unused)
CMSG_GROUP_DISBAND = 0x07B  # empty: leave the group


class Collector:
    """Read a character's event stream in the background into a list."""

    def __init__(self, stream: AsyncIterator[events.Event]) -> None:
        self.events: list[events.Event] = []
        self._task = asyncio.create_task(self._run(stream))

    async def _run(self, stream: AsyncIterator[events.Event]) -> None:
        async for e in stream:
            self.events.append(e)

    def of(self, cls: type[events.Event]) -> list[events.Event]:
        return [e for e in self.events if isinstance(e, cls)]

    async def wait_for(self, predicate, timeout: float = 10.0) -> events.Event:  # type: ignore[no-untyped-def]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for e in self.events:
                if predicate(e):
                    return e
            await asyncio.sleep(0.1)
        raise AssertionError(f"no matching event within {timeout}s; got {[type(e).__name__ for e in self.events]}")

    async def stop(self) -> None:
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task


async def logged_in_pair(make_client, label: str) -> tuple[Character, Character]:  # type: ignore[no-untyped-def]
    """A on the first test account and B on the second, each through its own account's client."""
    pair = []
    for account, name in ((0, A), (1, B)):
        ctl: Client = await make_client(f"{label}-{account}", account)
        for c in await ctl.characters.list():  # the caster pair shares these accounts
            if c.name in (c2[1] for c2 in CASTERS) and c.state.name != "OFFLINE":
                await ctl._plumbing_characters.logout(c.guid)
        for _ in range(40):
            if all(c.state.name == "OFFLINE" for c in await ctl.characters.list() if c.name in (x[1] for x in CASTERS)):
                break
            await asyncio.sleep(0.25)
        pair.append(await ctl.characters.login(name))
    return pair[0], pair[1]


async def test_whispers_are_echoed(make_client) -> None:  # type: ignore[no-untyped-def]
    """examples/chat_echo.py logic: A echoes every whisper back to its sender."""
    a, b = await logged_in_pair(make_client, "chat-echo")
    async with a.lease(), b.lease():
        b_events = Collector(b.events(events.Chat))

        async def echo() -> None:
            async for e in a.events(events.Chat):
                assert isinstance(e, events.Chat)
                if e.is_whisper and not e.from_self and e.sender_guid == b.guid:
                    await a.whisper(b.name, e.text)

        echo_task = asyncio.create_task(echo())
        await asyncio.sleep(0.5)
        try:
            text = f"ping {time.monotonic():.3f}"
            assert (await b.whisper(a.name, text)).ok
            reply = await b_events.wait_for(
                lambda e: isinstance(e, events.Chat) and e.is_whisper and e.sender_guid == a.guid and e.text == text)
            assert isinstance(reply, events.Chat) and not reply.from_self
        finally:
            echo_task.cancel()
            await b_events.stop()


async def test_group_invite_arrives_as_prompt_and_is_accepted(make_client) -> None:  # type: ignore[no-untyped-def]
    a, b = await logged_in_pair(make_client, "prompt-test")
    async with a.lease(), b.lease():
        b_events = Collector(b.events(events.Prompt))
        await asyncio.sleep(0.3)
        try:
            # A invites B the way a client does (CMSG_GROUP_INVITE), through the raw escape hatch.
            sent = await a.send_packet(CMSG_GROUP_INVITE, b.name.encode() + b"\0" + struct.pack("<I", 0))
            assert sent.status == CommandStatus.COMPLETED, sent

            prompt = await b_events.wait_for(lambda e: isinstance(e, events.Prompt) and not e.closed)
            assert isinstance(prompt, events.Prompt)
            assert prompt.prompt_kind == events.PromptKind.GROUP_INVITE and prompt.from_name == a.name

            await b.accept(prompt)
            closed = await b_events.wait_for(lambda e: isinstance(e, events.Prompt) and e.closed)
            assert isinstance(closed, events.Prompt) and closed.close_reason == "ACCEPTED"

            async def grouped() -> bool:
                s = await b.state(StateSection.GROUP)
                return s.in_group and s.group_leader_guid == a.guid and a.guid in s.group_member_guids

            for _ in range(40):
                if await grouped():
                    break
                await asyncio.sleep(0.25)
            assert await grouped()
        finally:
            await b_events.stop()
            await b.send_packet(CMSG_GROUP_DISBAND)  # leave the group again
            await a.send_packet(CMSG_GROUP_DISBAND)


async def test_each_occurrence_is_delivered_once(make_client) -> None:  # type: ignore[no-untyped-def]
    a, _b = await logged_in_pair(make_client, "dedup-test")
    async with a.lease():
        stream = Collector(a.events())
        await asyncio.sleep(0.5)
        try:
            text = f"once {time.monotonic():.3f}"
            say = await a.say(text)
            assert say.ok
            await asyncio.sleep(1.5)

            chats = [e for e in stream.of(events.Chat) if isinstance(e, events.Chat) and e.text == text]
            assert len(chats) == 1, chats
            results = [e for e in stream.of(events.CommandResult) if isinstance(e, events.CommandResult) and e.command_id == say.command_id]
            assert len(results) == 1, results
            assert len({e.sequence for e in stream.events}) == len(stream.events), "sequence numbers must be unique"
        finally:
            await stream.stop()


async def test_spell_and_aura_events_once_each(make_client) -> None:  # type: ignore[no-untyped-def]
    ctl = await make_client("spell-events")
    for c in await ctl.characters.list():
        if c.name == A and c.state.name != "OFFLINE":
            await ctl._plumbing_characters.logout(c.guid)
    await asyncio.sleep(1)
    mage = await ctl.characters.login(MAGE)
    try:
        async with mage.lease():
            stream = Collector(mage.events(events.SpellCastStart, events.SpellCastFinish, events.AuraApplied))
            await asyncio.sleep(0.5)
            r = await mage.cast("Frost Armor")  # self buff: one cast, one aura
            assert r.ok, r
            await asyncio.sleep(1.0)
            finishes = [e for e in stream.of(events.SpellCastFinish) if isinstance(e, events.SpellCastFinish) and e.command_id == r.command_id]
            assert len(finishes) == 1 and finishes[0].succeeded, finishes
            auras = [e for e in stream.of(events.AuraApplied) if isinstance(e, events.AuraApplied) and e.spell_id == r.spell_id]
            assert len(auras) == 1, auras
            await stream.stop()
    finally:
        await ctl._plumbing_characters.logout(mage.guid)
        await asyncio.sleep(1)


async def test_stalled_subscriber_does_not_slow_the_server(make_client) -> None:  # type: ignore[no-untyped-def]
    a, _b = await logged_in_pair(make_client, "stall-test")
    ctl = a._client
    admin = await make_client("stall-stats", ADMIN)  # GetStats is admin-only
    before = await admin.server.stats()

    # A subscriber that never reads: open the stream and leave it.
    # No HTTP/2 window growth on this connection, so its transport stops accepting data quickly (as a slow
    # client on a slow link would) and the server-side queue has to absorb the rest. A second login of the
    # same account would end ctl's session, so this second channel carries ctl's own session token.
    auth = SessionAuth(controller_id="stalled")
    auth.token = ctl._auth.token
    stalled = connect(ctl.target, auth, options=[("grpc.http2.bdp_probe", 0)])
    stalled_iter = EventService(stalled).subscribe([a.guid]).__aiter__()
    # Starting the iterator opens the call; it then takes one event and stops reading.
    first = asyncio.ensure_future(stalled_iter.__anext__())
    await asyncio.sleep(0.5)

    # A healthy subscriber alongside it.
    healthy = Collector(a.events(events.CommandResult))
    await asyncio.sleep(0.3)

    async with a.lease():
        # Flood: 20000 tiny commands, each producing a CommandResult event: more than the HTTP/2 window and
        # transport buffers absorb, so the stalled subscriber's own bounded queue must overflow.
        for _ in range(int(os.environ.get("ACSDK_FLOOD_BATCHES", "200"))):
            await asyncio.gather(*(a.set_target(None) for _ in range(100)))
            await asyncio.sleep(0.05)  # let the healthy reader keep pace

    after = await admin.server.stats()
    assert after.events_dropped > before.events_dropped, "the stalled subscriber's queue should have overflowed"
    assert after.max_tick_dispatch_us < 100_000, f"world-thread dispatch took {after.max_tick_dispatch_us} us"
    assert len(healthy.of(events.CommandResult)) >= 2900, len(healthy.of(events.CommandResult))
    assert not healthy.of(events.Lagged), "the healthy subscriber kept up and must not lag"

    # When the stalled client finally reads, it is told how much it missed.
    await asyncio.wait_for(first, timeout=5)

    async def find_lagged() -> events.Lagged:
        async for e in stalled_iter:
            if isinstance(e, events.Lagged):
                return e
        raise AssertionError("stream ended without Lagged")

    lagged = await asyncio.wait_for(find_lagged(), timeout=20)
    assert lagged.dropped > 0

    await healthy.stop()
    await stalled.close()  # the channel
