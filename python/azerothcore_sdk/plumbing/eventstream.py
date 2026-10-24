"""1:1 wrapper over EventService (PLAN.md §7): one Subscribe stream per controller, RespondToPrompt."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable

import grpc

from azerothcore_sdk import events as ev
from azerothcore_sdk.plumbing import channel as _channel  # noqa: F401  (puts _generated on sys.path)
from azerothcore_sdk.plumbing.errors import from_rpc_error, translate

from acsdk.v1 import event_pb2, event_pb2_grpc


class EventService:
    def __init__(self, channel: grpc.aio.Channel) -> None:
        self._stub = event_pb2_grpc.EventServiceStub(channel)

    async def subscribe(
        self,
        character_guids: Iterable[int] = (),
        types: Iterable[type[ev.Event]] = (),
        raw_opcodes: Iterable[int] = (),
    ) -> AsyncIterator[ev.Event]:
        """Yield typed events until the stream ends. Lagged events tell you when events were dropped."""
        type_ids = [t.type_id for t in types]
        raw = list(raw_opcodes)
        if raw and event_pb2.RAW_PACKET not in type_ids and type_ids:
            type_ids.append(event_pb2.RAW_PACKET)
        req = event_pb2.SubscribeRequest(character_guids=list(character_guids), types=type_ids, raw_opcodes=raw)
        call = self._stub.Subscribe(req)
        try:
            async for msg in call:
                yield ev.from_proto(msg)
        except grpc.aio.AioRpcError as e:
            if e.code() != grpc.StatusCode.CANCELLED:
                raise from_rpc_error(e) from e
        finally:
            call.cancel()

    async def respond_to_prompt(self, guid: int, token: str, prompt_id: int, *, accept: bool = True,
                                roll_vote: int = 0) -> tuple[bool, str]:
        req = event_pb2.RespondToPromptRequest(character_guid=guid, lease_token=token, prompt_id=prompt_id,
                                               accept=accept, roll_vote=roll_vote)
        r = await translate(self._stub.RespondToPrompt(req, timeout=10))
        return bool(r.ok), r.reason
