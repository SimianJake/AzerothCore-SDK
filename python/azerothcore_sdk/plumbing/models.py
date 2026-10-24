"""Typed results returned by the plumbing layer, so callers never handle raw protobuf (PLAN.md §7)."""

from __future__ import annotations

import enum
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ServerInfo:
    api_version: int
    sdk_version: str
    core_revision: str
    realm_name: str
    uptime_ms: int
    world_ticks: int
    last_tick_diff_ms: int
    api_minor: int = 0                      # 1 = account sessions (mod-acsdk 1.1)
    auth_modes: tuple[str, ...] = ()        # e.g. ("basic",)


@dataclass(frozen=True, slots=True)
class Session:
    """The account session a Client is logged in as."""
    account_id: int
    username: str
    admin: bool
    idle_timeout_s: int = 0
    max_lifetime_s: int = 0
    label: str = ""


@dataclass(frozen=True, slots=True)
class ServerStats:
    rpcs_total: int
    rpcs_unauthenticated: int
    commands_enqueued: int
    commands_executed: int
    commands_rejected_queue_full: int
    command_queue_depth: int
    last_tick_commands: int
    last_tick_dispatch_us: int
    max_tick_dispatch_us: int
    events_published: int
    events_dropped: int
    event_subscribers: int


class Lifecycle(enum.Enum):
    """Headless lifecycle (PLAN.md §4.3)."""

    UNSPECIFIED = 0
    OFFLINE = 1
    LOGGING_IN = 2
    ONLINE = 3
    LOGGING_OUT = 4


@dataclass(frozen=True, slots=True)
class CharacterSummary:
    guid: int
    name: str
    account_id: int
    account_name: str
    race: int
    class_id: int
    level: int
    online: bool  # in the world via any session (headless or a real client)
    state: Lifecycle  # headless lifecycle; OFFLINE when not driven by mod-acsdk


@dataclass(frozen=True, slots=True)
class Lease:
    guid: int
    token: str
    ttl_ms: int
    previous_holder: str  # set on a forced takeover


@dataclass(frozen=True, slots=True)
class LeaseEntry:
    guid: int
    holder_label: str
    expires_in_ms: int


class StateSection(enum.IntEnum):
    IDENTITY = 1
    POSITION = 2
    VITALS = 3
    STATUS = 4
    TARGET = 5
    MONEY = 6
    SPELLS = 7
    GROUP = 8
    CORPSE = 9
    EQUIPMENT = 10
    INVENTORY = 11
    QUESTS = 12


@dataclass(frozen=True, slots=True)
class InventoryItem:
    item_guid: int  # for sell() / equip()
    item_id: int
    name: str
    count: int
    bag: int  # 255 = equipped or backpack, else the bag's slot (19-22)
    slot: int  # equipped: 0 head ... 18 tabard; bags 19-22; backpack 23-38; inside a bag 0-35
    durability: int
    max_durability: int
    quality: int  # 0 poor, 1 common, 2 uncommon, 3 rare, 4 epic
    item_level: int
    required_level: int
    inventory_type: int  # 0 = not equippable
    sell_price_copper: int


@dataclass(frozen=True, slots=True)
class QuestObjective:
    kind: str  # CREATURE, GAMEOBJECT or ITEM
    id: int
    name: str
    current: int
    required: int

    @property
    def done(self) -> bool:
        return self.current >= self.required


@dataclass(frozen=True, slots=True)
class QuestLogEntry:
    quest_id: int
    title: str
    status: str  # INCOMPLETE, COMPLETE (ready to turn in) or FAILED
    level: int
    objectives: tuple[QuestObjective, ...]


@dataclass(frozen=True, slots=True)
class CharacterState:
    guid: int
    name: str
    state: Lifecycle
    lease_holder: str
    in_world: bool
    race: int
    class_id: int
    level: int
    gender: int
    map_id: int
    zone_id: int
    area_id: int
    position: tuple[float, float, float]
    orientation: float
    moving: bool
    health: int
    max_health: int
    power_type: int
    power: int
    max_power: int
    dead: bool
    ghost: bool
    in_combat: bool
    teleporting: bool
    target_guid: int
    money_copper: int
    known_spells: tuple[int, ...]
    in_group: bool
    group_leader_guid: int
    group_member_guids: tuple[int, ...]
    has_corpse: bool = False
    corpse_map_id: int = 0
    corpse_position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    corpse_reclaim_in_s: int = 0  # seconds until reclaim_corpse() is allowed
    equipment: tuple[InventoryItem, ...] = ()
    inventory: tuple[InventoryItem, ...] = ()
    free_bag_slots: int = 0
    quests: tuple[QuestLogEntry, ...] = ()

    def quest(self, quest_id: int) -> QuestLogEntry | None:
        return next((q for q in self.quests if q.quest_id == quest_id), None)


@dataclass(frozen=True, slots=True)
class LoginResult:
    ok: bool
    reason: str
    guid: int
    name: str
    state: Lifecycle
