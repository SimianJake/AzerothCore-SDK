"""1:1 wrappers over ActionService and WorldService (PLAN.md §7). Translation only."""

from __future__ import annotations

import builtins
import enum
from dataclasses import dataclass

import grpc

from azerothcore_sdk.plumbing import channel as _channel  # noqa: F401  (puts _generated on sys.path)
from azerothcore_sdk.plumbing.errors import translate

from acsdk.v1 import action_pb2, action_pb2_grpc, common_pb2, world_pb2, world_pb2_grpc

# Long-running commands answer when they finish, so the RPC deadline must outlast the command's own timeout.
_GRACE_S = 5.0


class CommandStatus(enum.Enum):
    UNSPECIFIED = 0
    STARTED = 1
    COMPLETED = 2
    FAILED = 3
    TIMEOUT = 4
    CANCELLED = 5


@dataclass(frozen=True, slots=True)
class GossipOption:
    index: int
    icon: int  # 0 chat, 1 vendor, 2 taxi, 3 trainer, 5 binder, 6 bank, ...
    coded: bool
    box_money: int
    text: str


@dataclass(frozen=True, slots=True)
class QuestOffer:
    quest_id: int
    icon: int
    level: int
    title: str


@dataclass(frozen=True, slots=True)
class VendorItem:
    slot: int
    item_id: int
    name: str
    available: int  # -1 = unlimited
    price_copper: int
    buy_count: int
    extended_cost: int


@dataclass(frozen=True, slots=True)
class LootedItem:
    slot: int
    item_id: int
    name: str
    count: int
    taken: bool  # False: not allowed (roll, locked) or did not fit


@dataclass(frozen=True, slots=True)
class Interaction:
    """What an NPC window or loot window showed. `kind`: GOSSIP, QUEST_LIST, QUEST_DETAILS, QUEST_REQUEST_ITEMS,
    QUEST_REWARD, VENDOR, TRAINER, TAXI, BANK, BINDER, CLOSED or LOOT."""

    kind: str
    npc_guid: int
    menu_id: int
    options: tuple[GossipOption, ...]
    quests: tuple[QuestOffer, ...]
    vendor_items: tuple[VendorItem, ...]
    loot_money: int
    loot: tuple[LootedItem, ...]


@dataclass(frozen=True, slots=True)
class CommandResult:
    command_id: int
    status: CommandStatus
    reason: str
    spell_id: int = 0
    interaction: Interaction | None = None

    @property
    def ok(self) -> bool:
        return self.status == CommandStatus.COMPLETED

    @property
    def done(self) -> bool:
        return self.status not in (CommandStatus.STARTED, CommandStatus.UNSPECIFIED)


class ObjectKind(enum.IntEnum):
    ANY = 0
    CREATURE = 1
    PLAYER = 2
    GAMEOBJECT = 3


class PathFlags(enum.IntFlag):
    """PathGenerator path type bits."""

    NORMAL = 0x01
    SHORTCUT = 0x02
    INCOMPLETE = 0x04
    NOPATH = 0x08
    NOT_USING_PATH = 0x10
    SHORT = 0x20
    FARFROMPOLY_START = 0x40
    FARFROMPOLY_END = 0x80


@dataclass(frozen=True, slots=True)
class PathResult:
    points: tuple[tuple[float, float, float], ...]
    flags: PathFlags
    length: float
    actual_end: tuple[float, float, float]
    reachable: bool  # the whole way to the requested end can be walked


@dataclass(frozen=True, slots=True)
class Height:
    found: bool
    height: float
    water_level: float


@dataclass(frozen=True, slots=True)
class ItemInfo:
    id: int
    name: str
    quality: int
    item_level: int
    required_level: int
    item_class: int
    item_subclass: int
    inventory_type: int
    sell_price: int


@dataclass(frozen=True, slots=True)
class CreatureInfo:
    entry: int
    name: str
    subname: str
    min_level: int
    max_level: int
    faction: int
    rank: int
    creature_type: int


@dataclass(frozen=True, slots=True)
class WorldObject:
    guid: int
    kind: ObjectKind
    entry: int
    name: str
    level: int
    position: tuple[float, float, float]
    orientation: float
    distance: float
    alive: bool
    hostile: bool
    attackable: bool
    health: int
    max_health: int


@dataclass(frozen=True, slots=True)
class SpellInfo:
    id: int
    name: str
    rank: str
    cast_time_ms: int
    channeled: bool
    passive: bool
    max_range: float
    power_type: int
    power_cost: int
    recovery_ms: int


def _interaction(w: action_pb2.Interaction) -> Interaction:
    return Interaction(
        kind=w.kind, npc_guid=w.npc_guid, menu_id=w.menu_id,
        options=tuple(GossipOption(o.index, o.icon, o.coded, o.box_money, o.text) for o in w.options),
        quests=tuple(QuestOffer(q.quest_id, q.icon, q.level, q.title) for q in w.quests),
        vendor_items=tuple(VendorItem(v.slot, v.item_id, v.name, v.available, v.price_copper, v.buy_count, v.extended_cost)
                           for v in w.vendor_items),
        loot_money=w.loot_money,
        loot=tuple(LootedItem(i.slot, i.item_id, i.name, i.count, i.taken) for i in w.loot),
    )


def _result(r: action_pb2.CommandResult) -> CommandResult:
    return CommandResult(command_id=r.command_id, status=CommandStatus(r.status), reason=r.reason, spell_id=r.spell_id,
                         interaction=_interaction(r.interaction) if r.HasField("interaction") else None)


def _ref(guid: int, token: str) -> action_pb2.CharacterRef:
    return action_pb2.CharacterRef(character_guid=guid, lease_token=token)


def _vec(x: float, y: float, z: float) -> common_pb2.Vector3:
    return common_pb2.Vector3(x=x, y=y, z=z)


def _world_object(o: world_pb2.WorldObject) -> WorldObject:
    return WorldObject(guid=o.guid, kind=ObjectKind(o.kind), entry=o.entry, name=o.name, level=o.level,
                       position=(o.position.x, o.position.y, o.position.z), orientation=o.orientation,
                       distance=o.distance, alive=o.alive, hostile=o.hostile, attackable=o.attackable,
                       health=o.health, max_health=o.max_health)


def _deadline(wait: bool, timeout_ms: int) -> float:
    return (timeout_ms / 1000.0 + _GRACE_S) if wait else 10.0


class ActionService:
    def __init__(self, channel: grpc.aio.Channel) -> None:
        self._stub = action_pb2_grpc.ActionServiceStub(channel)

    async def move_to(self, guid: int, token: str, x: float, y: float, z: float, *, tolerance: float = 1.0,
                      timeout_ms: int = 30000, wait: bool = True) -> CommandResult:
        req = action_pb2.MoveToRequest(character=_ref(guid, token), target=_vec(x, y, z), tolerance=tolerance,
                                       timeout_ms=timeout_ms, wait=wait)
        return _result(await translate(self._stub.MoveTo(req, timeout=_deadline(wait, timeout_ms))))

    async def stop(self, guid: int, token: str) -> CommandResult:
        return _result(await translate(self._stub.Stop(action_pb2.StopRequest(character=_ref(guid, token)), timeout=10)))

    async def follow(self, guid: int, token: str, target_guid: int, *, distance: float = 2.0, angle: float = 0.0,
                     wait: bool = False) -> CommandResult:
        req = action_pb2.FollowRequest(character=_ref(guid, token), target_guid=target_guid, distance=distance,
                                       angle=angle, wait=wait)
        return _result(await translate(self._stub.Follow(req, timeout=None if wait else 10)))

    async def face(self, guid: int, token: str, *, target_guid: int | None = None, orientation: float | None = None,
                   wait: bool = True) -> CommandResult:
        req = action_pb2.FaceRequest(character=_ref(guid, token), wait=wait)
        if target_guid is not None:
            req.target_guid = target_guid
        else:
            req.orientation = float(orientation or 0.0)
        return _result(await translate(self._stub.Face(req, timeout=10)))

    async def set_target(self, guid: int, token: str, target_guid: int) -> CommandResult:
        req = action_pb2.SetTargetRequest(character=_ref(guid, token), target_guid=target_guid)
        return _result(await translate(self._stub.SetTarget(req, timeout=10)))

    async def attack(self, guid: int, token: str, target_guid: int, *, until_stopped: bool = False,
                     timeout_ms: int = 120000, wait: bool = True) -> CommandResult:
        req = action_pb2.AttackRequest(character=_ref(guid, token), target_guid=target_guid, until_stopped=until_stopped,
                                       timeout_ms=timeout_ms, wait=wait)
        return _result(await translate(self._stub.Attack(req, timeout=_deadline(wait, timeout_ms))))

    async def stop_attack(self, guid: int, token: str) -> CommandResult:
        req = action_pb2.StopAttackRequest(character=_ref(guid, token))
        return _result(await translate(self._stub.StopAttack(req, timeout=10)))

    async def cast_spell(self, guid: int, token: str, spell_id: int, *, target_guid: int | None = None,
                         destination: tuple[float, float, float] | None = None, timeout_ms: int = 30000,
                         wait: bool = True) -> CommandResult:
        req = action_pb2.CastSpellRequest(character=_ref(guid, token), spell_id=spell_id, timeout_ms=timeout_ms, wait=wait)
        if target_guid is not None:
            req.target_guid = target_guid
        elif destination is not None:
            req.destination.CopyFrom(_vec(*destination))
        return _result(await translate(self._stub.CastSpell(req, timeout=_deadline(wait, timeout_ms))))

    async def cancel_cast(self, guid: int, token: str, spell_id: int = 0) -> CommandResult:
        req = action_pb2.CancelCastRequest(character=_ref(guid, token), spell_id=spell_id)
        return _result(await translate(self._stub.CancelCast(req, timeout=10)))

    async def say(self, guid: int, token: str, text: str, *, wait: bool = True) -> CommandResult:
        req = action_pb2.SayRequest(character=_ref(guid, token), text=text, wait=wait)
        return _result(await translate(self._stub.Say(req, timeout=15)))

    async def whisper(self, guid: int, token: str, to: str, text: str, *, wait: bool = True) -> CommandResult:
        req = action_pb2.WhisperRequest(character=_ref(guid, token), to=to, text=text, wait=wait)
        return _result(await translate(self._stub.Whisper(req, timeout=15)))

    async def cancel(self, guid: int, token: str, command_id: int) -> CommandResult:
        req = action_pb2.CancelRequest(character=_ref(guid, token), command_id=command_id)
        return _result(await translate(self._stub.Cancel(req, timeout=10)))

    async def send_client_packet(self, guid: int, token: str, opcode: int, payload: bytes = b"") -> CommandResult:
        req = action_pb2.SendClientPacketRequest(character=_ref(guid, token), opcode=opcode, payload=payload)
        return _result(await translate(self._stub.SendClientPacket(req, timeout=10)))

    # ---- interact channel (death recovery, NPCs, quests, vendors, loot, equipment) ----

    async def _interact(self, rpc, req, timeout_ms: int, wait: bool) -> CommandResult:  # type: ignore[no-untyped-def]
        return _result(await translate(rpc(req, timeout=_deadline(wait, timeout_ms))))

    async def release_spirit(self, guid: int, token: str, *, timeout_ms: int = 5000, wait: bool = True) -> CommandResult:
        req = action_pb2.InteractRequest(character=_ref(guid, token), timeout_ms=timeout_ms, wait=wait)
        return await self._interact(self._stub.ReleaseSpirit, req, timeout_ms, wait)

    async def reclaim_corpse(self, guid: int, token: str, *, timeout_ms: int = 5000, wait: bool = True) -> CommandResult:
        req = action_pb2.InteractRequest(character=_ref(guid, token), timeout_ms=timeout_ms, wait=wait)
        return await self._interact(self._stub.ReclaimCorpse, req, timeout_ms, wait)

    async def spirit_healer_resurrect(self, guid: int, token: str, npc_guid: int, *, timeout_ms: int = 5000,
                                      wait: bool = True) -> CommandResult:
        req = action_pb2.InteractRequest(character=_ref(guid, token), npc_guid=npc_guid, timeout_ms=timeout_ms, wait=wait)
        return await self._interact(self._stub.SpiritHealerResurrect, req, timeout_ms, wait)

    async def talk(self, guid: int, token: str, npc_guid: int, *, timeout_ms: int = 5000, wait: bool = True) -> CommandResult:
        req = action_pb2.InteractRequest(character=_ref(guid, token), npc_guid=npc_guid, timeout_ms=timeout_ms, wait=wait)
        return await self._interact(self._stub.Talk, req, timeout_ms, wait)

    async def open_vendor(self, guid: int, token: str, npc_guid: int, *, timeout_ms: int = 5000,
                          wait: bool = True) -> CommandResult:
        req = action_pb2.InteractRequest(character=_ref(guid, token), npc_guid=npc_guid, timeout_ms=timeout_ms, wait=wait)
        return await self._interact(self._stub.OpenVendor, req, timeout_ms, wait)

    async def select_gossip_option(self, guid: int, token: str, npc_guid: int, menu_id: int, option_index: int, *,
                                   code: str = "", timeout_ms: int = 5000, wait: bool = True) -> CommandResult:
        req = action_pb2.GossipOptionRequest(character=_ref(guid, token), npc_guid=npc_guid, menu_id=menu_id,
                                             option_index=option_index, code=code, timeout_ms=timeout_ms, wait=wait)
        return await self._interact(self._stub.SelectGossipOption, req, timeout_ms, wait)

    async def accept_quest(self, guid: int, token: str, npc_guid: int, quest_id: int, *, timeout_ms: int = 5000,
                           wait: bool = True) -> CommandResult:
        req = action_pb2.QuestRequest(character=_ref(guid, token), npc_guid=npc_guid, quest_id=quest_id,
                                      timeout_ms=timeout_ms, wait=wait)
        return await self._interact(self._stub.AcceptQuest, req, timeout_ms, wait)

    async def complete_quest(self, guid: int, token: str, npc_guid: int, quest_id: int, *, reward_index: int = 0,
                             timeout_ms: int = 5000, wait: bool = True) -> CommandResult:
        req = action_pb2.QuestRequest(character=_ref(guid, token), npc_guid=npc_guid, quest_id=quest_id,
                                      reward_index=reward_index, timeout_ms=timeout_ms, wait=wait)
        return await self._interact(self._stub.CompleteQuest, req, timeout_ms, wait)

    async def abandon_quest(self, guid: int, token: str, quest_id: int, *, timeout_ms: int = 5000,
                            wait: bool = True) -> CommandResult:
        req = action_pb2.QuestRequest(character=_ref(guid, token), quest_id=quest_id, timeout_ms=timeout_ms, wait=wait)
        return await self._interact(self._stub.AbandonQuest, req, timeout_ms, wait)

    async def buy_item(self, guid: int, token: str, npc_guid: int, item_id: int, count: int = 1, *,
                       timeout_ms: int = 5000, wait: bool = True) -> CommandResult:
        req = action_pb2.BuyItemRequest(character=_ref(guid, token), npc_guid=npc_guid, item_id=item_id, count=count,
                                        timeout_ms=timeout_ms, wait=wait)
        return await self._interact(self._stub.BuyItem, req, timeout_ms, wait)

    async def sell_item(self, guid: int, token: str, npc_guid: int, item_guid: int, count: int = 0, *,
                        timeout_ms: int = 5000, wait: bool = True) -> CommandResult:
        req = action_pb2.SellItemRequest(character=_ref(guid, token), npc_guid=npc_guid, item_guid=item_guid, count=count,
                                         timeout_ms=timeout_ms, wait=wait)
        return await self._interact(self._stub.SellItem, req, timeout_ms, wait)

    async def equip_item(self, guid: int, token: str, item_guid: int, *, timeout_ms: int = 5000,
                         wait: bool = True) -> CommandResult:
        req = action_pb2.EquipItemRequest(character=_ref(guid, token), item_guid=item_guid, timeout_ms=timeout_ms, wait=wait)
        return await self._interact(self._stub.EquipItem, req, timeout_ms, wait)

    async def loot(self, guid: int, token: str, target_guid: int, *, timeout_ms: int = 5000, wait: bool = True) -> CommandResult:
        req = action_pb2.LootRequest(character=_ref(guid, token), target_guid=target_guid, timeout_ms=timeout_ms, wait=wait)
        return await self._interact(self._stub.Loot, req, timeout_ms, wait)

    async def wait_command(self, command_id: int, timeout_ms: int = 30000) -> CommandResult:
        req = action_pb2.WaitCommandRequest(command_id=command_id, timeout_ms=timeout_ms)
        return _result(await translate(self._stub.WaitCommand(req, timeout=timeout_ms / 1000.0 + _GRACE_S)))


class WorldService:
    def __init__(self, channel: grpc.aio.Channel) -> None:
        self._stub = world_pb2_grpc.WorldServiceStub(channel)

    async def nearby_objects(self, guid: int, *, radius: float = 30.0, kind: ObjectKind = ObjectKind.ANY,
                             alive_only: bool = False, hostile_only: bool = False, limit: int = 50) -> builtins.list[WorldObject]:
        req = world_pb2.NearbyObjectsRequest(character_guid=guid, radius=radius, kind=int(kind), alive_only=alive_only,
                                             hostile_only=hostile_only, limit=limit)
        r = await translate(self._stub.NearbyObjects(req, timeout=10))
        return [_world_object(o) for o in r.objects]

    async def find_path(self, guid: int, end: tuple[float, float, float], *, start: tuple[float, float, float] | None = None,
                        straight: bool = False) -> PathResult:
        req = world_pb2.FindPathRequest(character_guid=guid, end=_vec(*end), straight=straight)
        if start is not None:
            req.start.CopyFrom(_vec(*start))
        r = await translate(self._stub.FindPath(req, timeout=10))
        return PathResult(points=tuple((p.x, p.y, p.z) for p in r.points), flags=PathFlags(r.path_flags), length=r.length,
                          actual_end=(r.actual_end.x, r.actual_end.y, r.actual_end.z), reachable=r.reachable)

    async def height(self, guid: int, x: float, y: float, z: float, *, include_vmaps: bool = True) -> Height:
        req = world_pb2.GetHeightRequest(character_guid=guid, x=x, y=y, z=z, include_vmaps=include_vmaps)
        r = await translate(self._stub.GetHeight(req, timeout=10))
        return Height(found=r.found, height=r.height, water_level=r.water_level)

    async def line_of_sight(self, guid: int, a: tuple[float, float, float], b: tuple[float, float, float]) -> bool:
        req = world_pb2.LineOfSightRequest(character_guid=guid)
        getattr(req, "from").CopyFrom(_vec(*a))
        req.to.CopyFrom(_vec(*b))
        r = await translate(self._stub.LineOfSight(req, timeout=10))
        return bool(r.clear)

    async def get_object(self, guid: int, object_guid: int) -> tuple[WorldObject | None, bool]:
        """(object or None, in line of sight from the character)."""
        r = await translate(self._stub.GetObject(world_pb2.GetObjectRequest(character_guid=guid, guid=object_guid), timeout=10))
        if not r.found:
            return None, False
        return _world_object(r.object), bool(r.in_line_of_sight)

    async def item_info(self, item_ids: builtins.list[int]) -> builtins.list[ItemInfo]:
        r = await translate(self._stub.GetItemInfo(world_pb2.GetItemInfoRequest(item_ids=item_ids), timeout=10))
        return [ItemInfo(id=i.id, name=i.name, quality=i.quality, item_level=i.item_level, required_level=i.required_level,
                         item_class=i.item_class, item_subclass=i.item_subclass, inventory_type=i.inventory_type,
                         sell_price=i.sell_price) for i in r.items]

    async def creature_info(self, entries: builtins.list[int]) -> builtins.list[CreatureInfo]:
        r = await translate(self._stub.GetCreatureInfo(world_pb2.GetCreatureInfoRequest(entries=entries), timeout=10))
        return [CreatureInfo(entry=c.entry, name=c.name, subname=c.subname, min_level=c.min_level, max_level=c.max_level,
                             faction=c.faction, rank=c.rank, creature_type=c.creature_type) for c in r.creatures]

    async def spell_info(self, spell_ids: builtins.list[int]) -> builtins.list[SpellInfo]:
        out: builtins.list[SpellInfo] = []
        for i in range(0, len(spell_ids), 1000):
            req = world_pb2.GetSpellInfoRequest(spell_ids=spell_ids[i:i + 1000])
            r = await translate(self._stub.GetSpellInfo(req, timeout=10))
            out += [SpellInfo(id=s.id, name=s.name, rank=s.rank, cast_time_ms=s.cast_time_ms, channeled=s.channeled,
                              passive=s.passive, max_range=s.max_range, power_type=s.power_type,
                              power_cost=s.power_cost, recovery_ms=s.recovery_ms) for s in r.spells]
        return out
