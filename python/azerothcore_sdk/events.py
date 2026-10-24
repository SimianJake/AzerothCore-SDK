"""Typed events from EventService.Subscribe (PLAN.md §7). Each event type has exactly one server source
(docs/events.md), so an occurrence arrives at most once per character stream.

    async for ev in char.events(events.Chat, events.Prompt):
        if isinstance(ev, events.Chat) and not ev.self: ...
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, fields
from typing import Any, ClassVar

from azerothcore_sdk.plumbing import channel as _channel  # noqa: F401  (puts _generated on sys.path)

from acsdk.v1 import event_pb2


@dataclass(frozen=True, slots=True)
class Event:
    """Base of every event: which character's stream it is on, a server-wide sequence and server time."""

    type_id: ClassVar[int] = 0
    payload_field: ClassVar[str] = ""

    character_guid: int = 0
    sequence: int = 0
    server_time_ms: int = 0


class ChatType(enum.IntEnum):
    SAY = 1
    PARTY = 2
    RAID = 3
    GUILD = 4
    OFFICER = 5
    YELL = 6
    WHISPER = 7
    WHISPER_FOREIGN = 8
    WHISPER_INFORM = 9
    EMOTE = 10
    TEXT_EMOTE = 11
    SYSTEM = 0
    MONSTER_SAY = 12
    CHANNEL = 17


@dataclass(frozen=True, slots=True)
class Chat(Event):
    type_id: ClassVar[int] = event_pb2.CHAT
    payload_field: ClassVar[str] = "chat"
    chat_type: int = 0
    language: int = 0
    sender_guid: int = 0
    sender_name: str = ""
    receiver_guid: int = 0
    text: str = ""
    from_self: bool = False  # this character's own say / whisper echo
    channel: str = ""

    @property
    def is_whisper(self) -> bool:
        return self.chat_type == ChatType.WHISPER


@dataclass(frozen=True, slots=True)
class _Damage(Event):
    attacker_guid: int = 0
    victim_guid: int = 0
    amount: int = 0
    overkill: int = 0
    school_mask: int = 0
    spell_id: int = 0  # 0 = melee swing
    absorbed: int = 0
    resisted: int = 0
    blocked: int = 0
    critical: bool = False


@dataclass(frozen=True, slots=True)
class DamageDealt(_Damage):
    type_id: ClassVar[int] = event_pb2.DAMAGE_DEALT
    payload_field: ClassVar[str] = "damage_dealt"


@dataclass(frozen=True, slots=True)
class DamageTaken(_Damage):
    type_id: ClassVar[int] = event_pb2.DAMAGE_TAKEN
    payload_field: ClassVar[str] = "damage_taken"


@dataclass(frozen=True, slots=True)
class Death(Event):
    type_id: ClassVar[int] = event_pb2.DEATH
    payload_field: ClassVar[str] = "death"
    role: int = 0  # 1 = this character died, 2 = it killed victim_guid
    victim_guid: int = 0
    killer_guid: int = 0

    @property
    def died(self) -> bool:
        return bool(self.role == event_pb2.DeathEvent.DIED)


@dataclass(frozen=True, slots=True)
class _Aura(Event):
    target_guid: int = 0
    caster_guid: int = 0
    spell_id: int = 0
    stacks: int = 0
    duration_ms: int = 0
    is_refresh: bool = False
    remove_mode: int = 0


@dataclass(frozen=True, slots=True)
class AuraApplied(_Aura):
    type_id: ClassVar[int] = event_pb2.AURA_APPLIED
    payload_field: ClassVar[str] = "aura_applied"


@dataclass(frozen=True, slots=True)
class AuraRemoved(_Aura):
    type_id: ClassVar[int] = event_pb2.AURA_REMOVED
    payload_field: ClassVar[str] = "aura_removed"


@dataclass(frozen=True, slots=True)
class _SpellCast(Event):
    caster_guid: int = 0
    spell_id: int = 0
    cast_count: int = 0
    command_id: int = 0
    outcome: int = 0  # SpellCastEvent.Outcome
    reason: str = ""
    cast_time_ms: int = 0


@dataclass(frozen=True, slots=True)
class SpellCastStart(_SpellCast):
    type_id: ClassVar[int] = event_pb2.SPELL_CAST_START
    payload_field: ClassVar[str] = "spell_cast_start"


@dataclass(frozen=True, slots=True)
class SpellCastFinish(_SpellCast):
    type_id: ClassVar[int] = event_pb2.SPELL_CAST_FINISH
    payload_field: ClassVar[str] = "spell_cast_finish"

    @property
    def succeeded(self) -> bool:
        return bool(self.outcome in (event_pb2.SpellCastEvent.SUCCEEDED, event_pb2.SpellCastEvent.CHANNEL_ENDED))


@dataclass(frozen=True, slots=True)
class LevelUp(Event):
    type_id: ClassVar[int] = event_pb2.LEVEL_UP
    payload_field: ClassVar[str] = "level_up"
    old_level: int = 0
    new_level: int = 0


@dataclass(frozen=True, slots=True)
class Loot(Event):
    type_id: ClassVar[int] = event_pb2.LOOT
    payload_field: ClassVar[str] = "loot"
    kind: int = 0  # LootEvent.Kind
    loot_guid: int = 0
    error: int = 0
    item_id: int = 0
    count: int = 0
    money_copper: int = 0


class PromptKind(enum.IntEnum):
    GROUP_INVITE = event_pb2.GROUP_INVITE
    TRADE_REQUEST = event_pb2.TRADE_REQUEST
    DUEL_REQUEST = event_pb2.DUEL_REQUEST
    RESURRECT = event_pb2.RESURRECT
    SUMMON = event_pb2.SUMMON
    LOOT_ROLL = event_pb2.LOOT_ROLL
    READY_CHECK = event_pb2.READY_CHECK


@dataclass(frozen=True, slots=True)
class Prompt(Event):
    """A server prompt the controller must answer (char.accept(prompt) / char.decline(prompt))."""

    type_id: ClassVar[int] = event_pb2.PROMPT
    payload_field: ClassVar[str] = "prompt"
    prompt_id: int = 0
    kind: int = 0
    from_guid: int = 0
    from_name: str = ""
    timeout_ms: int = 0
    loot_guid: int = 0
    loot_slot: int = 0
    item_id: int = 0
    zone_id: int = 0
    closed: bool = False  # the prompt ended; no answer possible
    close_reason: str = ""

    @property
    def prompt_kind(self) -> PromptKind:
        return PromptKind(self.kind)


@dataclass(frozen=True, slots=True)
class LeaseLost(Event):
    type_id: ClassVar[int] = event_pb2.LEASE_LOST
    payload_field: ClassVar[str] = "lease_lost"
    reason: str = ""
    new_holder: str = ""


@dataclass(frozen=True, slots=True)
class CharacterStateChanged(Event):
    type_id: ClassVar[int] = event_pb2.CHARACTER_STATE_CHANGED
    payload_field: ClassVar[str] = "character_state_changed"
    state: int = 0  # LifecycleState
    dead: bool = False
    ghost: bool = False
    in_combat: bool = False
    teleporting: bool = False
    map_id: int = 0
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CommandResult(Event):
    type_id: ClassVar[int] = event_pb2.COMMAND_RESULT
    payload_field: ClassVar[str] = "command_result"
    command_id: int = 0
    status: int = 0
    reason: str = ""
    spell_id: int = 0


@dataclass(frozen=True, slots=True)
class RawPacket(Event):
    type_id: ClassVar[int] = event_pb2.RAW_PACKET
    payload_field: ClassVar[str] = "raw_packet"
    opcode: int = 0
    payload: bytes = b""


@dataclass(frozen=True, slots=True)
class Lagged(Event):
    """This subscriber fell behind; `dropped` events were lost (oldest first)."""

    type_id: ClassVar[int] = event_pb2.LAGGED
    payload_field: ClassVar[str] = "lagged"
    dropped: int = 0


EVENT_TYPES: tuple[type[Event], ...] = (
    Chat, DamageDealt, DamageTaken, Death, AuraApplied, AuraRemoved, SpellCastStart, SpellCastFinish, LevelUp,
    Loot, Prompt, LeaseLost, CharacterStateChanged, CommandResult, RawPacket, Lagged,
)
_BY_FIELD = {cls.payload_field: cls for cls in EVENT_TYPES}


def from_proto(msg: Any) -> Event:
    """Convert an acsdk.v1.Event into its typed dataclass."""
    which = msg.WhichOneof("payload")
    cls = _BY_FIELD.get(which or "", Event)
    payload = getattr(msg, which) if which else None
    values: dict[str, Any] = {"character_guid": msg.character_guid, "sequence": msg.sequence, "server_time_ms": msg.server_time_ms}
    if payload is not None:
        for f in fields(cls):
            if f.name not in values and hasattr(payload, f.name):
                values[f.name] = getattr(payload, f.name)
        if cls is Chat:
            values["from_self"] = payload.self
    return cls(**values)


__all__ = [cls.__name__ for cls in EVENT_TYPES] + ["ChatType", "Event", "PromptKind", "from_proto"]
