"""Porcelain Character object: state reads, leases and actions, built only from plumbing calls."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Union

from azerothcore_sdk import events as ev
from azerothcore_sdk.plumbing import (
    CharacterState,
    CommandResult,
    GossipOption,
    Interaction,
    InventoryItem,
    Lease,
    ObjectKind,
    PathResult,
    QuestLogEntry,
    StateSection,
    WorldObject,
)
from azerothcore_sdk.plumbing.errors import AcsdkError

if TYPE_CHECKING:
    from azerothcore_sdk.client import Client

log = logging.getLogger(__name__)

# Anything that names a unit: a WorldObject from nearby(), another Character, or a raw object guid.
Target = Union[WorldObject, "Character", int]


def target_guid(target: Target) -> int:
    if isinstance(target, int):
        return target
    return target.guid


class LeaseError(AcsdkError):
    """ClaimCharacter / RenewLease / ReleaseCharacter failed; `reason` is the server's reason code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class LeaseLost(LeaseError):
    """The lease ended while held (expired, taken over, or the character went away)."""


class CommandHandle:
    """A command started with wait=False."""

    def __init__(self, character: Character, started: CommandResult) -> None:
        self._character = character
        self.command_id = started.command_id
        self._result: CommandResult | None = started if started.done else None

    async def done(self, poll_ms: int = 30000) -> CommandResult:
        """Wait for the terminal result."""
        while self._result is None:
            r = await self._character._client._plumbing_actions.wait_command(self.command_id, poll_ms)
            if r.done:
                self._result = r
        return self._result

    async def cancel(self) -> CommandResult:
        return await self._character._client._plumbing_actions.cancel(self._character.guid, self._character.lease_token, self.command_id)


class Character:
    """A character on an eligible account. Observing needs no lease; commanding needs one (`async with char.lease()`).

    Action methods return the CommandResult (check `.ok` / `.reason`), or a CommandHandle with wait=False.
    """

    def __init__(self, client: Client, guid: int, name: str) -> None:
        self._client = client
        self.guid = guid
        self.name = name
        self._lease: Lease | None = None
        self._renewer: asyncio.Task[None] | None = None
        self.lease_lost: LeaseLost | None = None  # set if the lease was lost while held

    def __repr__(self) -> str:
        return f"Character({self.name!r}, guid={self.guid})"

    # ---- state -------------------------------------------------------------------------------------

    @property
    def lease_token(self) -> str:
        """The current lease token, or "" when not leased. Keep it secret: it is a bearer capability."""
        return self._lease.token if self._lease else ""

    async def state(self, *sections: StateSection) -> CharacterState:
        return await self._client._plumbing_characters.get_state(self.guid, list(sections) or None)

    async def nearby(self, kind: str | ObjectKind = ObjectKind.CREATURE, *, radius: float = 30.0, hostile: bool = False,
                     alive: bool = True, limit: int = 50) -> list[WorldObject]:
        """Nearest units around this character (no lease needed)."""
        k = ObjectKind[kind.upper()] if isinstance(kind, str) else kind
        return await self._client._plumbing_world.nearby_objects(self.guid, radius=radius, kind=k, alive_only=alive,
                                                                 hostile_only=hostile, limit=limit)

    async def find_path(self, x: float, y: float, z: float, *, straight: bool = False) -> PathResult:
        """Server-side path from here to (x, y, z) over the navmesh (one query covers ~296 yards)."""
        return await self._client._plumbing_world.find_path(self.guid, (x, y, z), straight=straight)

    async def equipment(self) -> tuple[InventoryItem, ...]:
        """Equipped items."""
        return (await self.state(StateSection.EQUIPMENT)).equipment

    async def inventory(self) -> tuple[InventoryItem, ...]:
        """Items in the backpack and bags (and the bags themselves)."""
        return (await self.state(StateSection.INVENTORY)).inventory

    async def quests(self) -> tuple[QuestLogEntry, ...]:
        """The quest log with objective progress."""
        return (await self.state(StateSection.QUESTS)).quests

    async def logout(self) -> None:
        ok, reason = await self._client._plumbing_characters.logout(self.guid, self.lease_token)
        if not ok:
            raise AcsdkError(reason)

    # ---- actions -----------------------------------------------------------------------------------

    def _handle(self, result: CommandResult, wait: bool) -> CommandResult | CommandHandle:
        return result if wait else CommandHandle(self, result)

    async def move_to(self, x: float, y: float, z: float, *, tolerance: float = 1.0, timeout: float = 30.0,
                      wait: bool = True) -> CommandResult | CommandHandle:
        r = await self._client._plumbing_actions.move_to(self.guid, self.lease_token, x, y, z, tolerance=tolerance,
                                                          timeout_ms=int(timeout * 1000), wait=wait)
        return self._handle(r, wait)

    async def stop(self) -> CommandResult:
        return await self._client._plumbing_actions.stop(self.guid, self.lease_token)

    async def follow(self, target: Target, *, distance: float = 2.0, angle: float = 0.0) -> CommandHandle:
        r = await self._client._plumbing_actions.follow(self.guid, self.lease_token, target_guid(target),
                                                         distance=distance, angle=angle, wait=False)
        return CommandHandle(self, r)

    async def face(self, target: Target | None = None, *, orientation: float | None = None) -> CommandResult:
        if target is not None:
            return await self._client._plumbing_actions.face(self.guid, self.lease_token, target_guid=target_guid(target))
        return await self._client._plumbing_actions.face(self.guid, self.lease_token, orientation=orientation or 0.0)

    async def set_target(self, target: Target | None) -> CommandResult:
        guid = 0 if target is None else target_guid(target)
        return await self._client._plumbing_actions.set_target(self.guid, self.lease_token, guid)

    async def attack(self, target: Target, *, until_stopped: bool = False, timeout: float = 120.0,
                     wait: bool = True) -> CommandResult | CommandHandle:
        r = await self._client._plumbing_actions.attack(self.guid, self.lease_token, target_guid(target),
                                                         until_stopped=until_stopped, timeout_ms=int(timeout * 1000), wait=wait)
        return self._handle(r, wait)

    async def stop_attack(self) -> CommandResult:
        return await self._client._plumbing_actions.stop_attack(self.guid, self.lease_token)

    async def cast(self, spell: str | int, target: Target | None = None, *,
                   destination: tuple[float, float, float] | None = None, timeout: float = 30.0,
                   wait: bool = True) -> CommandResult | CommandHandle:
        """Cast by id, or by name (resolved to the highest known rank from cached spell data)."""
        spell_id = spell if isinstance(spell, int) else (await self._client.spells.resolve(self, spell)).id
        r = await self._client._plumbing_actions.cast_spell(
            self.guid, self.lease_token, spell_id, target_guid=None if target is None else target_guid(target),
            destination=destination, timeout_ms=int(timeout * 1000), wait=wait)
        return self._handle(r, wait)

    async def cancel_cast(self, spell_id: int = 0) -> CommandResult:
        return await self._client._plumbing_actions.cancel_cast(self.guid, self.lease_token, spell_id)

    async def say(self, text: str) -> CommandResult:
        return await self._client._plumbing_actions.say(self.guid, self.lease_token, text)

    async def whisper(self, to: str | Character, text: str) -> CommandResult:
        name = to if isinstance(to, str) else to.name
        return await self._client._plumbing_actions.whisper(self.guid, self.lease_token, name, text)

    # ---- death recovery ----------------------------------------------------------------------------
    # helpers.recover(char) does the whole run back; these are the single steps.

    async def release_spirit(self) -> CommandResult:
        """Dead -> ghost at the graveyard. Fails NOT_DEAD if alive."""
        return await self._client._plumbing_actions.release_spirit(self.guid, self.lease_token)

    async def reclaim_corpse(self) -> CommandResult:
        """Resurrect at the corpse: be a ghost within 39 yards of it, after state().corpse_reclaim_in_s.
        Fails NOT_RELEASED, NO_CORPSE, RECLAIM_DELAY or TOO_FAR_FROM_CORPSE."""
        return await self._client._plumbing_actions.reclaim_corpse(self.guid, self.lease_token)

    async def spirit_healer(self, healer: Target) -> CommandResult:
        """Resurrect at a spirit healer (with resurrection sickness)."""
        return await self._client._plumbing_actions.spirit_healer_resurrect(self.guid, self.lease_token, target_guid(healer))

    # ---- NPCs, quests, vendors, loot, equipment -----------------------------------------------------
    # Stand within about 5 yards of the NPC first (helpers.approach). Window contents are in r.interaction.

    async def talk(self, npc: Target) -> CommandResult:
        """Right-click an NPC. r.interaction.kind is GOSSIP, QUEST_LIST, QUEST_DETAILS, QUEST_REWARD, VENDOR, ...
        with its options, quests or vendor items."""
        return await self._client._plumbing_actions.talk(self.guid, self.lease_token, target_guid(npc))

    async def select_gossip_option(self, window: Interaction, option: GossipOption | int, *, code: str = "") -> CommandResult:
        """Pick an option of a GOSSIP window from talk(); returns the window that opens next."""
        index = option if isinstance(option, int) else option.index
        return await self._client._plumbing_actions.select_gossip_option(self.guid, self.lease_token, window.npc_guid,
                                                                         window.menu_id, index, code=code)

    async def accept_quest(self, npc: Target, quest_id: int) -> CommandResult:
        return await self._client._plumbing_actions.accept_quest(self.guid, self.lease_token, target_guid(npc), quest_id)

    async def complete_quest(self, npc: Target, quest_id: int, *, reward: int = 0) -> CommandResult:
        """Turn in a finished quest; `reward` picks the choice reward (0-based). Fails QUEST_NOT_COMPLETE."""
        return await self._client._plumbing_actions.complete_quest(self.guid, self.lease_token, target_guid(npc), quest_id,
                                                                   reward_index=reward)

    async def abandon_quest(self, quest_id: int) -> CommandResult:
        return await self._client._plumbing_actions.abandon_quest(self.guid, self.lease_token, quest_id)

    async def open_vendor(self, npc: Target) -> CommandResult:
        """The vendor's items in r.interaction.vendor_items."""
        return await self._client._plumbing_actions.open_vendor(self.guid, self.lease_token, target_guid(npc))

    async def buy(self, npc: Target, item_id: int, count: int = 1) -> CommandResult:
        return await self._client._plumbing_actions.buy_item(self.guid, self.lease_token, target_guid(npc), item_id, count)

    async def sell(self, npc: Target, item: InventoryItem | int, count: int = 0) -> CommandResult:
        """Sell an item from inventory() (count 0 = the whole stack)."""
        item_guid = item if isinstance(item, int) else item.item_guid
        return await self._client._plumbing_actions.sell_item(self.guid, self.lease_token, target_guid(npc), item_guid, count)

    async def equip(self, item: InventoryItem | int) -> CommandResult:
        """Equip an item from inventory() into its slot. Fails with the game's EQUIP_ERR_* reason."""
        item_guid = item if isinstance(item, int) else item.item_guid
        return await self._client._plumbing_actions.equip_item(self.guid, self.lease_token, item_guid)

    async def loot(self, corpse: Target) -> CommandResult:
        """Loot a dead creature: money and every item allowed; r.interaction.loot says what was taken."""
        return await self._client._plumbing_actions.loot(self.guid, self.lease_token, target_guid(corpse))

    # ---- events and prompts ------------------------------------------------------------------------

    def events(self, *types: type[ev.Event], raw_opcodes: tuple[int, ...] = ()) -> AsyncIterator[ev.Event]:
        """Events on this character's stream (all types except RawPacket if none given). No lease needed."""
        return self._client._plumbing_events.subscribe([self.guid], types, raw_opcodes)

    async def accept(self, prompt: ev.Prompt) -> None:
        await self._respond(prompt, True, 0)

    async def decline(self, prompt: ev.Prompt) -> None:
        await self._respond(prompt, False, 0)

    async def roll(self, prompt: ev.Prompt, vote: int) -> None:
        """Answer a LOOT_ROLL prompt: 0 pass, 1 need, 2 greed, 3 disenchant."""
        await self._respond(prompt, True, vote)

    async def _respond(self, prompt: ev.Prompt, accept: bool, vote: int) -> None:
        ok, reason = await self._client._plumbing_events.respond_to_prompt(self.guid, self.lease_token, prompt.prompt_id,
                                                                           accept=accept, roll_vote=vote)
        if not ok:
            raise AcsdkError(reason)

    async def send_packet(self, opcode: int, payload: bytes = b"") -> CommandResult:
        """Escape hatch: send a raw client opcode (needs ACSDK.RawPackets.Enable on the server)."""
        return await self._client._plumbing_actions.send_client_packet(self.guid, self.lease_token, opcode, payload)

    # ---- leases ------------------------------------------------------------------------------------

    async def claim(self, force: bool = False) -> Lease:
        """Claim the lease and keep it renewed in the background until release()."""
        lease, reason = await self._client._plumbing_leases.claim(self.guid, force=force)
        if lease is None:
            raise LeaseError(reason)
        self._lease = lease
        self.lease_lost = None
        self._renewer = asyncio.create_task(self._renew_loop(lease), name=f"lease-renew-{self.guid}")
        return lease

    async def release(self) -> None:
        lease, self._lease = self._lease, None
        if self._renewer:
            self._renewer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._renewer
            self._renewer = None
        if lease is not None:
            ok, reason = await self._client._plumbing_leases.release(self.guid, lease.token)
            if not ok and reason != "NOT_LEASED":
                log.warning("release of %s failed: %s", self.name, reason)

    @contextlib.asynccontextmanager
    async def lease(self, force: bool = False) -> AsyncIterator[Lease]:
        """async with char.lease(): ... — claim on entry, renew in the background, release on exit."""
        lease = await self.claim(force=force)
        try:
            yield lease
        finally:
            await self.release()

    async def _renew_loop(self, lease: Lease) -> None:
        ttl_ms = lease.ttl_ms
        while True:
            await asyncio.sleep(max(ttl_ms / 3000.0, 0.5))
            try:
                new_ttl, reason = await self._client._plumbing_leases.renew(self.guid, lease.token)
            except AcsdkError as e:  # transient transport trouble: keep trying until the TTL runs out
                log.warning("lease renew for %s failed: %s", self.name, e)
                continue
            if new_ttl is None:
                self.lease_lost = LeaseLost(reason)
                self._lease = None
                log.warning("lease on %s lost: %s", self.name, reason)
                return
            ttl_ms = new_ttl
