"""gRPC channel setup: session token and controller label on every call (ARCHITECTURE.md §4.3, PLAN.md §4.4)."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import grpc

# protoc emits absolute imports ("from acsdk.v1 import ..."), so the generated root must be importable.
_GENERATED = Path(__file__).resolve().parent.parent / "_generated"
if str(_GENERATED) not in sys.path:
    sys.path.insert(0, str(_GENERATED))

# Calls that must not carry the session token or trigger a re-login.
_NO_SESSION = ("/acsdk.v1.AuthService/Login",)


class SessionAuth:
    """Holds the current session token and knows how to get a new one.

    `relogin` is an async callable that logs in again and stores the new token here; None disables
    automatic re-login.
    """

    def __init__(self, controller_id: str = "") -> None:
        self.token = ""
        self.controller_id = controller_id
        self.relogin: Callable[[], Awaitable[None]] | None = None
        self._lock = asyncio.Lock()

    async def refresh(self, stale_token: str) -> None:
        """Log in again unless another call already replaced `stale_token`. One login at a time: the server keeps
        one session per account, so parallel logins would end each other's sessions."""
        async with self._lock:
            if self.token == stale_token and self.relogin is not None:
                await self.relogin()

    def metadata(self, method: str) -> list[tuple[str, str]]:
        md: list[tuple[str, str]] = []
        if self.controller_id:
            md.append(("x-acsdk-controller-id", self.controller_id))
        if self.token and method not in _NO_SESSION:
            md.append(("authorization", f"Bearer {self.token}"))
        return md


def _method(details: Any) -> str:
    m = details.method
    return m.decode() if isinstance(m, bytes) else str(m)


class _MetadataInterceptor(grpc.aio.UnaryUnaryClientInterceptor):  # type: ignore[misc]
    """Adds the session token; on UNAUTHENTICATED (session expired or replaced) logs in once more and retries."""

    def __init__(self, auth: SessionAuth) -> None:
        self._auth = auth

    async def intercept_unary_unary(self, continuation: Any, client_call_details: Any, request: Any) -> Any:
        method = _method(client_call_details)
        token_used = self._auth.token

        def details() -> Any:
            metadata = [(k, v) for k, v in (client_call_details.metadata or []) if k != "authorization" or method in _NO_SESSION]
            return client_call_details._replace(metadata=metadata + self._auth.metadata(method))

        call = await continuation(details(), request)
        if method in _NO_SESSION or self._auth.relogin is None or not token_used:
            return call
        try:
            await call
            return call
        except grpc.aio.AioRpcError as e:
            if e.code() != grpc.StatusCode.UNAUTHENTICATED:
                return call  # the caller sees the error when it awaits the call
        await self._auth.refresh(token_used)
        return await continuation(details(), request)


class _StreamMetadataInterceptor(grpc.aio.UnaryStreamClientInterceptor):  # type: ignore[misc]
    """Same headers on server-streaming calls (EventService.Subscribe). Streams are not retried: one ends with
    UNAUTHENTICATED when its session ends, and the caller subscribes again."""

    def __init__(self, auth: SessionAuth) -> None:
        self._auth = auth

    async def intercept_unary_stream(self, continuation: Any, client_call_details: Any, request: Any) -> Any:
        method = _method(client_call_details)
        metadata = list(client_call_details.metadata or []) + self._auth.metadata(method)
        return await continuation(client_call_details._replace(metadata=metadata), request)


def connect(target: str, auth: SessionAuth, options: list[tuple[str, Any]] | None = None) -> grpc.aio.Channel:
    """Open one insecure (localhost) HTTP/2 channel; many characters share it (PLAN.md §4.5)."""

    options = [
        ("grpc.keepalive_time_ms", 30000),
        ("grpc.keepalive_timeout_ms", 10000),
    ] + list(options or [])
    return grpc.aio.insecure_channel(target, options=options,
                                     interceptors=[_MetadataInterceptor(auth), _StreamMetadataInterceptor(auth)])
