"""gRPC channel setup: server token and controller label on every call (PLAN.md §4.4, Phase 1)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import grpc

# protoc emits absolute imports ("from acsdk.v1 import ..."), so the generated root must be importable.
_GENERATED = Path(__file__).resolve().parent.parent / "_generated"
if str(_GENERATED) not in sys.path:
    sys.path.insert(0, str(_GENERATED))


class _MetadataInterceptor(grpc.aio.UnaryUnaryClientInterceptor):  # type: ignore[misc]
    def __init__(self, metadata: list[tuple[str, str]]) -> None:
        self._metadata = metadata

    async def intercept_unary_unary(self, continuation: Any, client_call_details: Any, request: Any) -> Any:
        metadata = list(client_call_details.metadata or []) + self._metadata
        return await continuation(client_call_details._replace(metadata=metadata), request)


class _StreamMetadataInterceptor(grpc.aio.UnaryStreamClientInterceptor):  # type: ignore[misc]
    """Same headers on server-streaming calls (EventService.Subscribe)."""

    def __init__(self, metadata: list[tuple[str, str]]) -> None:
        self._metadata = metadata

    async def intercept_unary_stream(self, continuation: Any, client_call_details: Any, request: Any) -> Any:
        metadata = list(client_call_details.metadata or []) + self._metadata
        return await continuation(client_call_details._replace(metadata=metadata), request)


def connect(target: str, token: str, controller_id: str = "",
            options: list[tuple[str, Any]] | None = None) -> grpc.aio.Channel:
    """Open one insecure (localhost) HTTP/2 channel; many characters share it (PLAN.md §4.5)."""

    metadata = [("authorization", f"Bearer {token}")]
    if controller_id:
        metadata.append(("x-acsdk-controller-id", controller_id))

    options = [
        ("grpc.keepalive_time_ms", 30000),
        ("grpc.keepalive_timeout_ms", 10000),
    ] + list(options or [])
    return grpc.aio.insecure_channel(target, options=options, interceptors=[_MetadataInterceptor(metadata), _StreamMetadataInterceptor(metadata)])
