"""The Python client's login plumbing against an in-process fake server: no worldserver needed.

Covers Basic credentials on Login only, the Bearer session token on other calls, one automatic re-login (also
when several calls fail at once), relogin=False, error mapping, the version check and logout on close.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from typing import Any

import grpc
import pytest
import pytest_asyncio

from azerothcore_sdk import AccountRefused, Client, IncompatibleServer, NotYourCharacter, Unauthenticated
from azerothcore_sdk.plumbing import channel as _channel  # noqa: F401  (puts _generated on sys.path)

from acsdk.v1 import auth_pb2, auth_pb2_grpc, character_pb2, character_pb2_grpc, server_pb2, server_pb2_grpc


class Fake:
    def __init__(self) -> None:
        self.valid: str | None = None
        self.logins = 0
        self.logouts = 0
        self.api_minor = 1
        self.seen: list[str] = []


def _metadata(ctx: Any) -> dict[str, str]:
    return dict(ctx.invocation_metadata())


class _Auth(auth_pb2_grpc.AuthServiceServicer):  # type: ignore[misc]
    def __init__(self, fake: Fake) -> None:
        self.fake = fake

    async def Login(self, request: Any, ctx: Any) -> Any:
        auth = _metadata(ctx).get("authorization", "")
        assert auth.startswith("Basic "), auth
        user, _, password = base64.b64decode(auth[6:]).decode().partition(":")
        if password == "banned":
            await ctx.abort(grpc.StatusCode.PERMISSION_DENIED, "account may not use the SDK: BANNED")
        if password != "pw":
            await ctx.abort(grpc.StatusCode.UNAUTHENTICATED, "invalid username or password")
        self.fake.logins += 1
        self.fake.valid = f"tok{self.fake.logins}"
        return auth_pb2.AuthLoginResponse(session_token=self.fake.valid, account_id=7, username=user.upper())

    async def Logout(self, request: Any, ctx: Any) -> Any:
        self.fake.logouts += 1
        self.fake.valid = None
        return auth_pb2.AuthLogoutResponse(ok=True)


class _Server(server_pb2_grpc.ServerServiceServicer):  # type: ignore[misc]
    def __init__(self, fake: Fake) -> None:
        self.fake = fake

    async def GetInfo(self, request: Any, ctx: Any) -> Any:
        assert "authorization" not in _metadata(ctx)  # asked before logging in
        return server_pb2.GetInfoResponse(api_version=1, api_minor=self.fake.api_minor, auth_modes=["basic"],
                                          sdk_version="1.1.0")


class _Characters(character_pb2_grpc.CharacterServiceServicer):  # type: ignore[misc]
    def __init__(self, fake: Fake) -> None:
        self.fake = fake

    async def List(self, request: Any, ctx: Any) -> Any:
        auth = _metadata(ctx).get("authorization", "")
        self.fake.seen.append(auth)
        if auth != f"Bearer {self.fake.valid}":
            await ctx.abort(grpc.StatusCode.UNAUTHENTICATED, "session ended")
        return character_pb2.ListCharactersResponse()

    async def GetState(self, request: Any, ctx: Any) -> Any:
        await ctx.abort(grpc.StatusCode.PERMISSION_DENIED, "character is not on this session's account")


@pytest_asyncio.fixture
async def fake() -> AsyncIterator[tuple[Fake, str]]:
    f = Fake()
    server = grpc.aio.server()
    auth_pb2_grpc.add_AuthServiceServicer_to_server(_Auth(f), server)
    server_pb2_grpc.add_ServerServiceServicer_to_server(_Server(f), server)
    character_pb2_grpc.add_CharacterServiceServicer_to_server(_Characters(f), server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    yield f, f"127.0.0.1:{port}"
    await server.stop(None)


async def test_session_token_and_relogin(fake: tuple[Fake, str]) -> None:
    f, target = fake
    async with Client(target, username="bob", password="pw", label="t") as c:
        assert c.session.username == "BOB" and c.session.account_id == 7
        await c.characters.list()
        assert f.seen[-1] == "Bearer tok1"

        f.valid = "gone"                 # the server ended the session
        await c.characters.list()        # UNAUTHENTICATED -> log in again -> retry
        assert f.logins == 2 and f.seen[-2:] == ["Bearer tok1", "Bearer tok2"]

        f.valid = "gone"                 # several calls fail at once: still one login
        await asyncio.gather(*(c.characters.list() for _ in range(5)))
        assert f.logins == 3

        with pytest.raises(NotYourCharacter):
            await c.characters.get(1).state()
    assert f.logouts == 1                # close() logs out


async def test_relogin_can_be_turned_off(fake: tuple[Fake, str]) -> None:
    f, target = fake
    c = Client(target, username="bob", password="pw", relogin=False)
    await c.connect()
    f.valid = "gone"
    with pytest.raises(Unauthenticated):
        await c.characters.list()
    assert f.logins == 1
    await c.close()


async def test_login_errors(fake: tuple[Fake, str]) -> None:
    _f, target = fake
    with pytest.raises(Unauthenticated):
        await Client(target, username="bob", password="wrong").connect()
    with pytest.raises(AccountRefused, match="BANNED"):
        await Client(target, username="bob", password="banned").connect()


async def test_old_server_is_reported(fake: tuple[Fake, str]) -> None:
    f, target = fake
    f.api_minor = 0
    with pytest.raises(IncompatibleServer):
        await Client(target, username="bob", password="pw").connect()


def test_credentials_are_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACSDK_USERNAME", raising=False)
    monkeypatch.delenv("ACSDK_PASSWORD", raising=False)
    with pytest.raises(ValueError):
        Client("127.0.0.1:1")
    monkeypatch.setenv("ACSDK_USERNAME", "bob")
    monkeypatch.setenv("ACSDK_PASSWORD", "pw")
    assert Client("127.0.0.1:1").username == "bob"
