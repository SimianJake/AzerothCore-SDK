"""Fixes from consumer testing: CreateCharacter result, state sections, death calls, NPCs, quests, vendor, loot."""

from __future__ import annotations

import random
import string

from conftest import ADMIN, CASTERS, CHARACTERS, KOBOLD_FIELD

from azerothcore_sdk import StateSection
from azerothcore_sdk.helpers import approach, kill
from azerothcore_sdk.plumbing import CommandStatus, DebugService

WARRIOR = CHARACTERS[0]
ACCOUNT = CASTERS[1][0].upper()  # the second test account


async def npc(char, name: str):  # type: ignore[no-untyped-def]
    for o in await char.nearby("creature", radius=250, limit=400):
        if o.name == name:
            return o
    raise AssertionError(f"{name} not found near {char.name}")


async def test_create_character_returns_stored_name(make_client, realm) -> None:  # type: ignore[no-untyped-def]
    owner = await make_client("create-test", 1)
    for c in await owner.characters.list():  # the account must have nobody online
        if c.state.name != "OFFLINE":
            await owner._plumbing_characters.logout(c.guid)
    admin = await make_client("create-test-admin", ADMIN)  # DebugService is admin-only
    dbg = DebugService(admin.channel)
    name = "zz" + "".join(random.choice(string.ascii_lowercase) for _ in range(6))
    try:
        ok, reason, guid, stored = await dbg.create_character(ACCOUNT, name, race=1, class_id=1)
        assert ok and guid and reason == "", reason
        assert stored == name.capitalize()
        ok, reason, _, _ = await dbg.create_character(ACCOUNT, name, race=1, class_id=1)
        assert not ok and reason == "NAME_IN_USE"
        ok, reason, _, _ = await dbg.create_character(ACCOUNT, "Bad1name", race=1, class_id=1)
        assert not ok and reason.startswith("CHAR_NAME_"), reason
    finally:
        realm.console(f"character erase {name.capitalize()}", settle=1.0)


async def test_state_has_equipment_inventory_quests_corpse(make_client) -> None:  # type: ignore[no-untyped-def]
    ac = await make_client("state-sections-test")
    w = await ac.characters.login(WARRIOR)
    s = await w.state(StateSection.EQUIPMENT, StateSection.INVENTORY, StateSection.QUESTS, StateSection.CORPSE)
    assert s.equipment and all(i.bag == 255 and i.slot < 19 and i.name for i in s.equipment)
    assert any(i.name == "Hearthstone" for i in s.inventory)
    assert s.free_bag_slots > 0
    assert not s.has_corpse
    for q in s.quests:
        assert q.title and q.status in ("INCOMPLETE", "COMPLETE", "FAILED")


async def test_death_calls_refuse_when_alive(make_client) -> None:  # type: ignore[no-untyped-def]
    ac = await make_client("death-test")
    w = await ac.characters.login(WARRIOR)
    async with w.lease():
        r = await w.release_spirit()
        assert r.status == CommandStatus.TIMEOUT and r.reason == "NOT_DEAD", r
        r = await w.reclaim_corpse()
        assert r.reason == "NOT_DEAD", r


async def test_talk_vendor_and_buy_refusal(make_client) -> None:  # type: ignore[no-untyped-def]
    ac = await make_client("vendor-test")
    w = await ac.characters.login(WARRIOR)
    async with w.lease():
        danil = await npc(w, "Brother Danil")
        assert await approach(w, danil, within=3.0)
        r = await w.talk(danil)
        assert r.ok and r.interaction and r.interaction.kind == "VENDOR", r
        assert any(v.name == "Refreshing Spring Water" for v in r.interaction.vendor_items)

        money = (await w.state(StateSection.MONEY)).money_copper
        dear = max(r.interaction.vendor_items, key=lambda v: v.price_copper)
        if dear.price_copper > money:
            b = await w.buy(danil, dear.item_id)
            assert b.reason == "BUY_ERR_NOT_ENOUGHT_MONEY", b
        b = await w.buy(danil, 999999)
        assert b.reason == "BUY_ERR_CANT_FIND_ITEM", b
        e = await w.equip(12345)
        assert e.reason == "ITEM_NOT_FOUND", e


async def test_quest_dialog_and_refusals(make_client) -> None:  # type: ignore[no-untyped-def]
    ac = await make_client("quest-test")
    w = await ac.characters.login(WARRIOR)
    async with w.lease():
        mcbride = await npc(w, "Marshal McBride")
        assert await approach(w, mcbride, within=3.0)
        r = await w.talk(mcbride)
        assert r.ok and r.interaction and r.interaction.kind.startswith(("GOSSIP", "QUEST")), r
        r = await w.accept_quest(mcbride, 999999)
        assert not r.ok and r.reason == "QUEST_NOT_OFFERED", r
        r = await w.complete_quest(mcbride, 999999)
        assert r.reason == "QUEST_NOT_IN_LOG", r
        r = await w.abandon_quest(999999)
        assert r.reason == "QUEST_NOT_IN_LOG", r


async def test_kill_then_loot(make_client) -> None:  # type: ignore[no-untyped-def]
    ac = await make_client("loot-test")
    w = await ac.characters.login(WARRIOR)
    async with w.lease():
        x, y, z = KOBOLD_FIELD
        await w.move_to(x, y, z, tolerance=3.0, timeout=40)
        mobs = [m for m in await w.nearby("creature", radius=45, limit=50) if m.level <= 2 and m.attackable]
        assert mobs, "no level 1-2 creature near the kobold field"
        k = await kill(w, mobs[0], timeout=90)
        assert k.killed, k
        corpse, _ = await ac.world.get_object(w.guid, mobs[0].guid)
        assert corpse is not None and not corpse.alive
        assert await approach(w, corpse, within=2.0)
        r = await w.loot(corpse)
        assert r.interaction and r.interaction.kind == "LOOT" or r.reason.startswith("LOOT_ERROR_"), r
        if r.ok:
            assert all(i.name for i in r.interaction.loot)
