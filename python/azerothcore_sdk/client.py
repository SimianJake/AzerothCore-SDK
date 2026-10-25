"""Porcelain entry point: one Client per controller, one gRPC channel shared by all its characters."""

from __future__ import annotations

import builtins
import contextlib
import os
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any, Self

import grpc

from azerothcore_sdk import events as ev
from azerothcore_sdk import plumbing
from azerothcore_sdk.character import Character
from azerothcore_sdk.plumbing import (
    CharacterSummary,
    LeaseEntry,
    ServerInfo,
    ServerStats,
    Session,
    SessionAuth,
)
from azerothcore_sdk.plumbing.errors import AcsdkError, IncompatibleServer, Unauthenticated
from azerothcore_sdk.plumbing.eventstream import EventService
from azerothcore_sdk.spells import SpellCache


class LoginFailed(AcsdkError):
    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"login of {name} failed: {reason}")
        self.name = name
        self.reason = reason


# mod-acsdk API this SDK speaks: acsdk.v1 with account sessions (api_minor 1, mod-acsdk 1.1.0).
API_VERSION = 1
MIN_API_MINOR = 1


class ServerApi:
    def __init__(self, channel: grpc.aio.Channel) -> None:
        self._svc = plumbing.ServerService(channel)

    async def health(self) -> bool:
        return await self._svc.health()

    async def info(self) -> ServerInfo:
        return await self._svc.get_info()

    async def stats(self) -> ServerStats:
        """Diagnostic counters. Needs an admin session (ACSDK.Auth.AdminGmLevel); NotYourCharacter otherwise."""
        return await self._svc.get_stats()


class CharactersApi:
    def __init__(self, client: Client) -> None:
        self._client = client

    async def list(self) -> list[CharacterSummary]:
        """Your account's characters (a session only ever sees its own account's)."""
        characters, _truncated = await self._client._plumbing_characters.list()
        return characters

    async def login(self, name: str) -> Character:
        """Log the character in headless (no-op if it already is) and return it. Does not claim a lease."""
        r = await self._client._plumbing_characters.login(name)
        if not r.ok:
            raise LoginFailed(name, r.reason)
        return Character(self._client, r.guid, r.name)

    def get(self, guid: int, name: str = "") -> Character:
        """A handle to one of your characters by guid. Another account's character raises NotYourCharacter on use."""
        return Character(self._client, guid, name)

    async def leases(self) -> builtins.list[LeaseEntry]:
        return await self._client._plumbing_leases.list()


class Client:
    """Connection to mod-acsdk, logged in as one AzerothCore account.

        async with Client("127.0.0.1:50051", username="acsdkhcbot", password="...", label="grinder-1") as ac:
            char = await ac.characters.login("Thrall")
            async with char.lease():
                print(await char.state())

    username/password default to the ACSDK_USERNAME and ACSDK_PASSWORD environment variables. A session sees
    and drives only its own account's characters. With relogin=True (default) a call that fails because the
    session ended (idle timeout, server restart) logs in once more and is retried; leases do not survive that,
    since they belong to the session that claimed them.
    """

    def __init__(self, target: str = "127.0.0.1:50051", *, username: str | None = None, password: str | None = None,
                 label: str = "", relogin: bool = True, channel_options: list[tuple[str, Any]] | None = None) -> None:
        self.target = target
        self.username = username if username is not None else os.environ.get("ACSDK_USERNAME", "")
        self._password = password if password is not None else os.environ.get("ACSDK_PASSWORD", "")
        if not self.username or not self._password:
            raise ValueError("Client needs username and password (or the ACSDK_USERNAME / ACSDK_PASSWORD environment variables)")
        self.label = label
        self.relogin = relogin
        self._channel_options = channel_options
        self._channel: grpc.aio.Channel | None = None
        self._auth = SessionAuth(controller_id=label)
        self._session: Session | None = None

    async def connect(self, verify: bool = True) -> Self:
        """Open the channel and log in. With verify=True, first check the server speaks this SDK's API
        (IncompatibleServer otherwise). Raises Unauthenticated for a wrong username or password."""
        if self._channel is None:
            self._channel = plumbing.connect(self.target, self._auth, self._channel_options)
            self._server = ServerApi(self._channel)
            self._plumbing_auth = plumbing.AuthService(self._channel)
            self._plumbing_characters = plumbing.CharacterService(self._channel)
            self._plumbing_leases = plumbing.LeaseService(self._channel)
            self._plumbing_actions = plumbing.ActionService(self._channel)
            self._plumbing_world = plumbing.WorldService(self._channel)
            self._plumbing_events = EventService(self._channel)
            self.spells = SpellCache(self)
            self._characters = CharactersApi(self)
        try:
            if verify:
                await self._check_version()
            if self._session is None:
                await self._login()
                self._auth.relogin = self._login if self.relogin else None
        except BaseException:
            await self.close()
            raise
        return self

    async def _check_version(self) -> None:
        try:
            info = await self._server.info()
        except Unauthenticated as e:
            # mod-acsdk 1.0 answered GetInfo only with its shared server token.
            raise IncompatibleServer("the server's mod-acsdk predates account logins (1.0); "
                                     f"this SDK needs mod-acsdk 1.1 or newer ({e})") from e
        if info.api_version != API_VERSION or info.api_minor < MIN_API_MINOR or "basic" not in info.auth_modes:
            raise IncompatibleServer(f"server runs mod-acsdk {info.sdk_version} (API {info.api_version}.{info.api_minor}); "
                                     f"this SDK needs API {API_VERSION}.{MIN_API_MINOR} or newer with password login")

    async def _login(self) -> None:
        token, self._session = await self._plumbing_auth.login(self.username, self._password, self.label)
        self._auth.token = token

    @property
    def session(self) -> Session:
        """The account this client is logged in as."""
        self._require()
        assert self._session is not None
        return self._session

    async def whoami(self) -> Session:
        """Ask the server which session this client holds (also refreshes its idle timer)."""
        self._require()
        return await self._plumbing_auth.who_am_i()

    async def close(self) -> None:
        """Log out (ending the session's leases and event streams) and close the channel."""
        if self._channel is not None and self._session is not None:
            self._auth.relogin = None
            with contextlib.suppress(AcsdkError):
                await self._plumbing_auth.logout(timeout=5.0)
        self._session = None
        self._auth.token = ""
        if self._channel is not None:
            await self._channel.close()
        self._channel = None

    def events(self, *types: type[ev.Event], characters: tuple[int, ...] = (),
               raw_opcodes: tuple[int, ...] = ()) -> AsyncIterator[ev.Event]:
        """One event stream for this client (filter by characters and types; all your characters if none). It ends
        with Unauthenticated when the session ends, and NotYourCharacter if a listed character is another account's."""
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
