"""SDK exceptions and the mapping from gRPC status codes (PLAN.md §7)."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TypeVar

import grpc

T = TypeVar("T")


class AcsdkError(Exception):
    """Base class for every error raised by azerothcore_sdk."""


class Unauthenticated(AcsdkError):
    """The server token was missing or wrong."""


class ServerBusy(AcsdkError):
    """The server's command queue was full (RESOURCE_EXHAUSTED); retry later."""


class ServerUnavailable(AcsdkError):
    """worldserver is not reachable or is shutting down."""


class Timeout(AcsdkError):
    """The call's deadline passed."""


class RpcError(AcsdkError):
    """Any other gRPC failure."""

    def __init__(self, code: grpc.StatusCode, details: str) -> None:
        super().__init__(f"{code.name}: {details}")
        self.code = code
        self.details = details


_BY_CODE: dict[grpc.StatusCode, type[AcsdkError]] = {
    grpc.StatusCode.UNAUTHENTICATED: Unauthenticated,
    grpc.StatusCode.RESOURCE_EXHAUSTED: ServerBusy,
    grpc.StatusCode.UNAVAILABLE: ServerUnavailable,
    grpc.StatusCode.DEADLINE_EXCEEDED: Timeout,
}


def from_rpc_error(error: grpc.aio.AioRpcError) -> AcsdkError:
    code = error.code()
    details = error.details() or ""
    cls = _BY_CODE.get(code)
    return cls(details) if cls else RpcError(code, details)


async def translate(call: Awaitable[T]) -> T:
    """Await a stub call, converting gRPC errors into SDK exceptions."""
    try:
        return await call
    except grpc.aio.AioRpcError as e:
        raise from_rpc_error(e) from e
