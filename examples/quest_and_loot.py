"""Take a quest, fight and loot, sell the loot, recover from death if it happens.

    python examples/quest_and_loot.py <character>

Log in with an AzerothCore account: set ACSDK_USERNAME and ACSDK_PASSWORD (environment variables).

Run it with a low-level human in Northshire Valley.
"""

import asyncio
import sys

from azerothcore_sdk import Client, StateSection
from azerothcore_sdk.helpers import approach, kill, recover


async def find(char, name):
    return next((o for o in await char.nearby("creature", radius=250, limit=400) if o.name == name), None)


async def main(name: str) -> None:
    async with Client(label="quest-and-loot") as ac:
        char = await ac.characters.login(name)
        async with char.lease():
            await recover(char)  # no-op when alive

            # Ask the Marshal what he has, take what he offers.
            mcbride = await find(char, "Marshal McBride")
            if mcbride and await approach(char, mcbride):
                r = await char.talk(mcbride)
                print("McBride:", r.interaction.kind, [q.title for q in r.interaction.quests])
                for q in r.interaction.quests:
                    print(" accept", q.title, (await char.accept_quest(mcbride, q.quest_id)).reason or "ok")
            for q in await char.quests():
                print("quest", q.title, q.status, [(o.name, o.current, o.required) for o in q.objectives])

            # Fight three wolves, stopping if health runs low, and loot them.
            for _ in range(3):
                wolf = await find(char, "Diseased Young Wolf")
                if wolf is None:
                    break
                k = await kill(char, wolf, stop_below_health=30)
                print("fight:", "won" if k.killed else k.reason)
                if (await char.state(StateSection.STATUS)).dead:
                    print("died; recovering:", await recover(char))
                    continue
                if k.killed:
                    corpse, _ = await ac.world.get_object(char.guid, wolf.guid)
                    if corpse and await approach(char, corpse, within=2.0):
                        r = await char.loot(corpse)
                        if r.interaction:
                            print(" loot:", r.interaction.loot_money, "copper,", [i.name for i in r.interaction.loot if i.taken])

            # Sell anything worth money to the general goods vendor.
            danil = await find(char, "Brother Danil")
            if danil and await approach(char, danil):
                for item in await char.inventory():
                    if item.sell_price_copper:
                        print("sell", item.name, (await char.sell(danil, item)).reason or "ok")
            print("money:", (await char.state(StateSection.MONEY)).money_copper, "copper")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
