"""Porcelain entry point: one Client per controller, one gRPC channel shared by all its characters."""

from __future__ import annotations

import builtins
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any, Self

import grpc

from azerothcore_sdk import events as ev
from azerothcore_sdk import plumbing
from azerothcore_sdk.character import Character
from azerothcore_sdk.plumbing import CharacterSummary, LeaseEntry, ServerInfo, ServerStats
from azerothcore_sdk.plumbing.errors import AcsdkError
from azerothcore_sdk.plumbing.eventstream import EventService
from azerothcore_sdk.spells import SpellCache


class LoginFailed(AcsdkError):
    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"login of {name} failed: {reason}")
        self.name = name
        self.reason = reason


class ServerApi:
    def __init__(self, channel: grpc.aio.Channel) -> None:
        self._svc = plumbing.ServerService(channel)

    async def health(self) -> bool:
        return await self._svc.health()

    async def info(self) -> ServerInfo:
        return await self._svc.get_info()

    async def stats(self) -> ServerStats:
        return await self._svc.get_stats()


class CharactersApi:
    def __init__(self, client: Client) -> None:
        self._client = client

    async def list(self) -> list[CharacterSummary]:
        """Characters that may be driven headless (accounts matching ACSDK.HeadlessAccountPrefix)."""
        characters, _truncated = await self._client._plumbing_characters.list()
        return characters

    async def login(self, name: str) -> Character:
        """Log the character in headless (no-op if it already is) and return it. Does not claim a lease."""
        r = await self._client._plumbing_characters.login(name)
        if not r.ok:
            raise LoginFailed(name, r.reason)
        return Character(self._client, r.guid, r.name)

    def get(self, guid: int, name: str = "") -> Character:
        """A handle to a character by guid, e.g. to observe one another controller drives."""
        return Character(self._client, guid, name)

    async def leases(self) -> builtins.list[LeaseEntry]:
        return await self._client._plumbing_leases.list()


class Client:
    """Connection to mod-acsdk.

        async with Client("127.0.0.1:50051", token="...", controller_id="grinder-1") as ac:
            char = await ac.characters.login("Thrall")
            async with char.lease():
                print(await char.state())
    """

    def __init__(self, target: str = "127.0.0.1:50051", *, token: str, controller_id: str = "",
                 channel_options: list[tuple[str, Any]] | None = None) -> None:
        self.target = target
        self.controller_id = controller_id
        self._token = token
        self._channel_options = channel_options
        self._channel: grpc.aio.Channel | None = None

    async def connect(self, verify: bool = True) -> Self:
        """Open the channel. With verify=True, fail fast (Unauthenticated / ServerUnavailable) via Health."""
        if self._channel is None:
            self._channel = plumbing.connect(self.target, self._token, self.controller_id, self._channel_options)
            self._server = ServerApi(self._channel)
            self._plumbing_characters = plumbing.CharacterService(self._channel)
            self._plumbing_leases = plumbing.LeaseService(self._channel)
            self._plumbing_actions = plumbing.ActionService(self._channel)
            self._plumbing_world = plumbing.WorldService(self._channel)
            self._plumbing_events = EventService(self._channel)
            self.spells = SpellCache(self)
            self._characters = CharactersApi(self)
        if verify:
            try:
                await self.server.health()
            except BaseException:
                await self.close()
                raise
        return self

    async def close(self) -> None:
        if self._channel is not None:
            await self._channel.close()
        self._channel = None

    def events(self, *types: type[ev.Event], characters: tuple[int, ...] = (),
               raw_opcodes: tuple[int, ...] = ()) -> AsyncIterator[ev.Event]:
        """One event stream for this controller (filter by characters and types; all headless characters if none)."""
        self._require()
        return self._plumbing_events.subscribe(characters, types, raw_opcodes)

    @property
    def world(self) -> plumbing.WorldService:
        """World queries (paths, heights, line of sight, objects, static item/creature/spell data)."""
        self._require()
        return self._plumbing_world

    @property
    def channel(self) -> grpc.aio.Channel:
        """The underlying gRPC channel (for plumbing calls not yet wrapped by porcelain)."""
        self._require()
        assert self._channel is not None
        return self._channel

    @property
    def server(self) -> ServerApi:
        self._require()
        return self._server

    @property
    def characters(self) -> CharactersApi:
        self._require()
        return self._characters

    def _require(self) -> None:
        if self._channel is None:
            raise RuntimeError("Client is not connected; use 'async with Client(...)' or 'await client.connect()'")

    async def __aenter__(self) -> Self:
        return await self.connect()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()
