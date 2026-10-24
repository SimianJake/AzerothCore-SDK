"""1:1 async wrappers over the generated service stubs. Translation only, no composition."""

from __future__ import annotations

import base64
import builtins

import grpc

from azerothcore_sdk.plumbing import channel as _channel  # noqa: F401  (puts _generated on sys.path)
from azerothcore_sdk.plumbing.errors import AccountRefused, NotYourCharacter, translate
from azerothcore_sdk.plumbing.models import (
    CharacterState,
    CharacterSummary,
    InventoryItem,
    Lease,
    LeaseEntry,
    Lifecycle,
    LoginResult,
    QuestLogEntry,
    QuestObjective,
    ServerInfo,
    ServerStats,
    Session,
    StateSection,
)

from acsdk.v1 import (
    auth_pb2,
    auth_pb2_grpc,
    character_pb2,
    character_pb2_grpc,
    debug_pb2,
    debug_pb2_grpc,
    lease_pb2,
    lease_pb2_grpc,
    server_pb2,
    server_pb2_grpc,
)

DEFAULT_TIMEOUT = 10.0


def _item(i: character_pb2.InventoryItem) -> InventoryItem:
    return InventoryItem(i.item_guid, i.item_id, i.name, i.count, i.bag, i.slot, i.durability, i.max_durability,
                         i.quality, i.item_level, i.required_level, i.inventory_type, i.sell_price_copper)


class AuthService:
    """Account sessions (mod-acsdk 1.1). Login sends the password only in 'authorization: Basic' metadata."""

    def __init__(self, channel: grpc.aio.Channel) -> None:
        self._stub = auth_pb2_grpc.AuthServiceStub(channel)

    async def login(self, username: str, password: str, label: str = "",
                    timeout: float = DEFAULT_TIMEOUT) -> tuple[str, Session]:
        """Returns (session token, session). Raises Unauthenticated for a wrong username or password."""
        basic = base64.b64encode(f"{username}:{password}".encode()).decode()
        try:
            r = await translate(self._stub.Login(auth_pb2.AuthLoginRequest(label=label), timeout=timeout,
                                                 metadata=[("authorization", f"Basic {basic}")]))
        except NotYourCharacter as e:
            raise AccountRefused(str(e)) from e
        return r.session_token, Session(account_id=r.account_id, username=r.username, admin=r.admin,
                                        idle_timeout_s=r.idle_timeout_s, max_lifetime_s=r.max_lifetime_s, label=label)

    async def logout(self, timeout: float = DEFAULT_TIMEOUT) -> bool:
        r = await translate(self._stub.Logout(auth_pb2.AuthLogoutRequest(), timeout=timeout))
        return bool(r.ok)

    async def who_am_i(self, timeout: float = DEFAULT_TIMEOUT) -> Session:
        r = await translate(self._stub.WhoAmI(auth_pb2.WhoAmIRequest(), timeout=timeout))
        return Session(account_id=r.account_id, username=r.username, admin=r.admin, label=r.label)


class DebugService:
    """Test provisioning (served only when ACSDK.Debug.Enable = 1). Results are (ok, reason, ...) as on the wire."""

    def __init__(self, channel: grpc.aio.Channel) -> None:
        self._stub = debug_pb2_grpc.DebugServiceStub(channel)

    async def create_account(self, username: str, password: str, timeout: float = 30.0) -> tuple[bool, str]:
        r = await translate(self._stub.CreateAccount(debug_pb2.CreateAccountRequest(username=username, password=password),
                                                     timeout=timeout))
        return bool(r.ok), r.reason

    async def create_character(self, account: str, name: str, *, race: int, class_id: int, gender: int = 0,
                               timeout: float = 30.0) -> tuple[bool, str, int, str]:
        """Returns (ok, reason, character guid, name as stored; AzerothCore capitalises it)."""
        req = debug_pb2.CreateCharacterRequest(account_username=account, name=name, race=race, class_id=class_id,
                                               gender=gender)
        r = await translate(self._stub.CreateCharacter(req, timeout=timeout))
        return bool(r.ok), r.reason, r.character_guid, r.name


class ServerService:
    def __init__(self, channel: grpc.aio.Channel) -> None:
        self._stub = server_pb2_grpc.ServerServiceStub(channel)

    async def health(self, timeout: float = DEFAULT_TIMEOUT) -> bool:
        r = await translate(self._stub.Health(server_pb2.HealthRequest(), timeout=timeout))
        return bool(r.serving)

    async def get_info(self, timeout: float = DEFAULT_TIMEOUT) -> ServerInfo:
        r = await translate(self._stub.GetInfo(server_pb2.GetInfoRequest(), timeout=timeout))
        return ServerInfo(
            api_version=r.api_version,
            sdk_version=r.sdk_version,
            core_revision=r.core_revision,
            realm_name=r.realm_name,
            uptime_ms=r.uptime_ms,
            world_ticks=r.world_ticks,
            last_tick_diff_ms=r.last_tick_diff_ms,
            api_minor=r.api_minor,
            auth_modes=tuple(r.auth_modes),
        )

    async def get_stats(self, timeout: float = DEFAULT_TIMEOUT) -> ServerStats:
        r = await translate(self._stub.GetStats(server_pb2.GetStatsRequest(), timeout=timeout))
        return ServerStats(
            rpcs_total=r.rpcs_total,
            rpcs_unauthenticated=r.rpcs_unauthenticated,
            commands_enqueued=r.commands_enqueued,
            commands_executed=r.commands_executed,
            commands_rejected_queue_full=r.commands_rejected_queue_full,
            command_queue_depth=r.command_queue_depth,
            last_tick_commands=r.last_tick_commands,
            last_tick_dispatch_us=r.last_tick_dispatch_us,
            max_tick_dispatch_us=r.max_tick_dispatch_us,
            events_published=r.events_published,
            events_dropped=r.events_dropped,
            event_subscribers=r.event_subscribers,
        )


class CharacterService:
    def __init__(self, channel: grpc.aio.Channel) -> None:
        self._stub = character_pb2_grpc.CharacterServiceStub(channel)

    async def list(self, timeout: float = DEFAULT_TIMEOUT) -> tuple[list[CharacterSummary], bool]:
        """Returns (the session account's characters, truncated)."""
        r = await translate(self._stub.List(character_pb2.ListCharactersRequest(), timeout=timeout))
        characters = [
            CharacterSummary(
                guid=c.character_guid,
                name=c.name,
                account_id=c.account_id,
                account_name=c.account_name,
                race=c.race,
                class_id=c.class_id,
                level=c.level,
                online=c.online,
                state=Lifecycle(c.state),
            )
            for c in r.characters
        ]
        return characters, bool(r.truncated)

    async def login(self, name: str, timeout: float = 60.0) -> LoginResult:
        r = await translate(self._stub.Login(character_pb2.LoginRequest(name=name), timeout=timeout))
        return LoginResult(ok=r.ok, reason=r.reason, guid=r.character_guid, name=r.name, state=Lifecycle(r.state))

    async def logout(self, guid: int, lease_token: str = "", timeout: float = 30.0) -> tuple[bool, str]:
        request = character_pb2.LogoutRequest(character_guid=guid, lease_token=lease_token)
        r = await translate(self._stub.Logout(request, timeout=timeout))
        return bool(r.ok), r.reason

    async def get_state(
        self, guid: int, sections: builtins.list[StateSection] | None = None, timeout: float = DEFAULT_TIMEOUT
    ) -> CharacterState:
        request = character_pb2.GetStateRequest(character_guid=guid, sections=[int(s) for s in sections or []])
        r = await translate(self._stub.GetState(request, timeout=timeout))
        return CharacterState(
            guid=r.character_guid,
            name=r.name,
            state=Lifecycle(r.state),
            lease_holder=r.lease_holder,
            in_world=r.in_world,
            race=r.race,
            class_id=r.class_id,
            level=r.level,
            gender=r.gender,
            map_id=r.map_id,
            zone_id=r.zone_id,
            area_id=r.area_id,
            position=(r.position.x, r.position.y, r.position.z),
            orientation=r.orientation,
            moving=r.moving,
            health=r.health,
            max_health=r.max_health,
            power_type=r.power_type,
            power=r.power,
            max_power=r.max_power,
            dead=r.dead,
            ghost=r.ghost,
            in_combat=r.in_combat,
            teleporting=r.teleporting,
            target_guid=r.target_guid,
            money_copper=r.money_copper,
            known_spells=tuple(r.known_spells),
            in_group=r.in_group,
            group_leader_guid=r.group_leader_guid,
            group_member_guids=tuple(r.group_member_guids),
            has_corpse=r.has_corpse,
            corpse_map_id=r.corpse_map_id,
            corpse_position=(r.corpse_position.x, r.corpse_position.y, r.corpse_position.z),
            corpse_reclaim_in_s=r.corpse_reclaim_in_s,
            equipment=tuple(_item(i) for i in r.equipment),
            inventory=tuple(_item(i) for i in r.inventory),
            free_bag_slots=r.free_bag_slots,
            quests=tuple(
                QuestLogEntry(q.quest_id, q.title, q.status, q.level,
                              tuple(QuestObjective(o.kind, o.id, o.name, o.current, o.required) for o in q.objectives))
                for q in r.quests
            ),
        )


class LeaseService:
    """Lease RPCs. Failures come back as (ok=False, reason) rather than exceptions, as on the wire."""

    def __init__(self, channel: grpc.aio.Channel) -> None:
        self._stub = lease_pb2_grpc.LeaseServiceStub(channel)

    async def claim(self, guid: int, force: bool = False, timeout: float = DEFAULT_TIMEOUT) -> tuple[Lease | None, str]:
        r = await translate(self._stub.ClaimCharacter(lease_pb2.ClaimCharacterRequest(character_guid=guid, force=force), timeout=timeout))
        if not r.ok:
            return None, r.reason
        return Lease(guid=guid, token=r.lease_token, ttl_ms=r.ttl_ms, previous_holder=r.previous_holder), ""

    async def release(self, guid: int, token: str, timeout: float = DEFAULT_TIMEOUT) -> tuple[bool, str]:
        r = await translate(self._stub.ReleaseCharacter(lease_pb2.ReleaseCharacterRequest(character_guid=guid, lease_token=token), timeout=timeout))
        return bool(r.ok), r.reason

    async def renew(self, guid: int, token: str, timeout: float = DEFAULT_TIMEOUT) -> tuple[int | None, str]:
        """Returns (new ttl_ms or None, reason)."""
        r = await translate(self._stub.RenewLease(lease_pb2.RenewLeaseRequest(character_guid=guid, lease_token=token), timeout=timeout))
        return (r.ttl_ms if r.ok else None), r.reason

    async def list(self, timeout: float = DEFAULT_TIMEOUT) -> list[LeaseEntry]:
        r = await translate(self._stub.ListLeases(lease_pb2.ListLeasesRequest(), timeout=timeout))
        return [LeaseEntry(guid=e.character_guid, holder_label=e.holder_label, expires_in_ms=e.expires_in_ms) for e in r.leases]
