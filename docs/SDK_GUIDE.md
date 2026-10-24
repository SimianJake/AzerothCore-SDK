# AzerothCore SDK: quick guide

Drive "headless" characters (logged in with no game client) on an AzerothCore 3.3.5a server from Python.
The server side is the [mod-acsdk](https://github.com/SimianJake/mod-acsdk) AzerothCore module, built into `worldserver`; the client side is
the `azerothcore_sdk` Python package (asyncio) from this repository.

## 1. Setup

**Server.** The server owner installs [mod-acsdk](https://github.com/SimianJake/mod-acsdk) (see its README). The settings that matter to SDK
users, in `configs\modules\mod_acsdk.conf`:

| Setting | Meaning |
|---|---|
| `ACSDK.AuthToken` | Shared secret every controller must send. **Required**: the SDK listener does not start without it. |
| `ACSDK.BindAddress` / `ACSDK.Port` | Where the SDK listens. Default `127.0.0.1:50051` (this machine only). |
| `ACSDK.HeadlessAccountPrefix` | Only characters on accounts starting with this (default `acsdkhc`) can be driven. Those accounts must be player-level, not GM. |
| `ACSDK.Lease.TtlSec` | How long a lease lives without renewal (default 15 s). The SDK renews automatically. |
| `ACSDK.Prompt.TimeoutSec` | Unanswered invites/trades/duels/etc. are declined after this (default 60 s). |
| `ACSDK.RawPackets.Enable` | Allow `send_packet()` and raw packet events (default off). |

**Python** (3.11+):

```bash
pip install azerothcore_sdk-1.0.0-py3-none-any.whl
```

Step-by-step setup for a new user is in [installation.md](installation.md). How to build and host the
package for other people is in [PUBLISHING.md](PUBLISHING.md).

## 2. The basics

```python
import asyncio
from azerothcore_sdk import Client

async def main():
    async with Client("127.0.0.1:50051", token="<ACSDK.AuthToken>", controller_id="my-bot") as ac:
        char = await ac.characters.login("Examplechar")      # no-op if already online
        async with char.lease():                          # claim control; released on exit
            print(await char.state())
            r = await char.say("hello")
            print(r.ok, r.status, r.reason)

asyncio.run(main())
```

Key ideas:

- **Observe freely, command with a lease.** Anyone with the token can read state and events. To *act*, a
  controller must hold the character's **lease** (`async with char.lease():`). Only one controller holds it
  at a time; others get `ALREADY_LEASED` (or can take over with `char.lease(force=True)`).
- **Nothing happens on its own.** A logged-in character only does what a controller tells it. If the
  controller disappears, the lease expires and the character stops where it is.
- **Every action resolves to a result** (`CommandResult`): `status` (COMPLETED / FAILED / TIMEOUT /
  CANCELLED) and a `reason`. Check `r.ok`. Pass `wait=False` to get a handle and `await handle.done()` later.
- **One command per channel.** Movement (move/follow/face/stop), casting, melee and chat are separate
  channels; a new command on a channel cancels the running one (`reason="SUPERSEDED"`).

## 3. What you can do

| Area | Calls |
|---|---|
| Characters | `ac.characters.list()`, `login(name)`, `char.logout()`, `char.state(*sections)` |
| Movement | `char.move_to(x, y, z, tolerance=1)`, `stop()`, `follow(target)`, `face(target)` |
| Combat | `char.set_target(t)`, `attack(t)`, `stop_attack()`, `cast("Fireball", t)` (name or spell id), `cancel_cast()` |
| Chat | `char.say(text)`, `char.whisper(name, text)` |
| Gear and bags | `char.equipment()`, `char.inventory()`, `char.equip(item)` |
| NPCs | `char.talk(npc)` (gossip, quest list, vendor...), `char.select_gossip_option(window, option)` |
| Quests | `char.quests()` (log with objective progress), `accept_quest(npc, id)`, `complete_quest(npc, id, reward=0)`, `abandon_quest(id)` |
| Vendors | `char.open_vendor(npc)`, `buy(npc, item_id, count)`, `sell(npc, item)` |
| Loot | `char.loot(corpse)` takes the money and every item allowed |
| Death | `char.release_spirit()`, `reclaim_corpse()`, `spirit_healer(npc)`; `state(StateSection.CORPSE)` says where the corpse is |
| World | `char.nearby("creature", radius=30, hostile=True)`, `char.find_path(x, y, z)`, `ac.world.height(...)`, `ac.world.line_of_sight(...)`, `ac.world.get_object(...)`, `ac.world.item_info([...])`, `ac.world.creature_info([...])` |
| Events | `async for ev in char.events(events.Chat, events.Prompt): ...` |
| Prompts | `await char.accept(prompt)`, `decline(prompt)`, `roll(prompt, vote)` |
| Helpers | `helpers.kill(char, mob, spells=[...], stop_below_health=20)`, `helpers.recover(char)` (release, run back, resurrect), `helpers.approach(char, npc)`, `helpers.travel(char, (x, y, z))` for long routes, `helpers.paths_svg(...)` |
| Escape hatch | `char.send_packet(opcode, payload)` (needs `ACSDK.RawPackets.Enable = 1`) |

NPC calls need the character within about 5 yards (`await helpers.approach(char, npc)` first). Calls that
open a window return its contents in `r.interaction`: `kind` (GOSSIP, QUEST_LIST, QUEST_DETAILS,
QUEST_REWARD, VENDOR, LOOT, ...), `options`, `quests`, `vendor_items`, `loot`, `loot_money`.

```python
r = await char.talk(npc)
for q in r.interaction.quests:
    await char.accept_quest(npc, q.quest_id)
```

Ready-made scripts in `examples\`: `hello_character.py`, `kill_a_mob.py`, `chat_echo.py`, `travel.py`, `quest_and_loot.py`.

**Events** each come from exactly one source, so you never see the same occurrence twice: `Chat`,
`DamageDealt`, `DamageTaken`, `Death`, `AuraApplied`, `AuraRemoved`, `SpellCastStart`, `SpellCastFinish`,
`LevelUp`, `Loot`, `Prompt`, `LeaseLost`, `CharacterStateChanged`, `CommandResult`, `RawPacket`, and
`Lagged` (you read too slowly and `dropped` events were lost).

## 4. Errors

### Exceptions (Python)

| Exception | Meaning | What to do |
|---|---|---|
| `Unauthenticated` | Wrong or missing server token. | Use the `ACSDK.AuthToken` value from `mod_acsdk.conf`. |
| `ServerUnavailable` | Can't reach the SDK listener. | Is worldserver running? Right address/port? |
| `ServerBusy` | Server's command queue is full. | Back off and retry. |
| `Timeout` | The call's deadline passed. | Usually the server is overloaded or stuck; retry. |
| `LoginFailed(reason)` | `characters.login()` refused; see login reasons below. | |
| `LeaseError(reason)` | Claiming / releasing the lease failed (e.g. `ALREADY_LEASED`). | |
| `LeaseLost` | Set on `char.lease_lost` when your lease ended while you held it. | Re-claim if you still need control. |
| `SpellNotKnown` | `cast("Name")` found no such spell in the character's spellbook. | Check the spelling or use the spell id. |
| `AcsdkError(reason)` | Base class; also raised by `logout()` and prompt answers with the reason. | |
| `RpcError(code)` | Any other gRPC error, e.g. `PERMISSION_DENIED` (raw packets disabled) or `FAILED_PRECONDITION` (world query on a character that isn't online). | |

### Command results (`r.status`, `r.reason`)

| Reason | Meaning |
|---|---|
| *(empty, COMPLETED)* | It worked. |
| `INVALID_LEASE_TOKEN` | You don't hold this character's lease (never claimed, expired, or taken over). |
| `CHARACTER_OFFLINE` | The character isn't logged in (or logged out mid-command). |
| `SUPERSEDED` | Cancelled because you sent a newer command on the same channel. |
| `LEASE_LOST` / `LEASE_RELEASED` | Cancelled because the lease expired / was taken over, or you released it. |
| `NO_RESPONSE` (TIMEOUT) | The game silently ignored the request (e.g. a spell the character doesn't know, a cast during cooldown). |
| `TIMEOUT` | Started but didn't finish in time. |
| `NO_PATH` | No walkable route to that point (or too far for one move; use `helpers.travel`). |
| `STOPPED_SHORT` / `STUCK` | Movement ended before arriving / made no progress for 5 s. |
| `DIED` / `TELEPORTED` | The character died / was teleported during the command. |
| `TARGET_GONE` / `TARGET_DEAD` | The target disappeared / is already dead. |
| `NOT_IN_RANGE` / `BAD_FACING` / `CANT_ATTACK` / `ATTACK_REFUSED` | Melee refused by the game: too far, facing away (call `face()` first), or not attackable. |
| `COMBAT_CANCELLED` | The game ended combat (e.g. the target evaded). |
| `SPELL_FAILED_*` | The game's own spell error, e.g. `SPELL_FAILED_NO_POWER` (not enough mana/rage), `SPELL_FAILED_OUT_OF_RANGE`, `SPELL_FAILED_LINE_OF_SIGHT`, `SPELL_FAILED_NOT_READY` (cooldown), `SPELL_FAILED_MOVING`, `SPELL_FAILED_INTERRUPTED`. |
| `CANCELLED` | You cancelled it (`handle.cancel()` / `cancel_cast()`). |
| `PLAYER_NOT_FOUND` / `WRONG_FACTION` / `CHAT_REJECTED` | Whisper target offline/unknown, other faction, or chat refused (muted, etc.). |
| `RATE_LIMITED` | Too many raw packets per second for this character. |
| `TOO_FAR` / `TARGET_GONE` | NPC or corpse out of interaction range (about 5 yards) / not found. |
| `NOT_DEAD` / `NOT_RELEASED` / `NO_CORPSE` | Death calls: the character is alive / has not released its spirit / has no corpse. |
| `RECLAIM_DELAY` / `TOO_FAR_FROM_CORPSE` | `reclaim_corpse()` too early (see `state().corpse_reclaim_in_s`) / more than 39 yards from the corpse. |
| `NO_SUCH_OPTION` | `select_gossip_option()` on a menu that is no longer open; `talk()` again. |
| `QUEST_NOT_IN_LOG` / `QUEST_ALREADY_IN_LOG` / `QUEST_NOT_OFFERED` / `QUEST_NOT_COMPLETE` | Quest calls: not taken / already taken / this NPC does not give it / objectives not done. |
| `INVALIDREASON_*` | The game refused the quest, e.g. `INVALIDREASON_DONT_HAVE_REQ` (level, class or earlier quest missing), `INVALIDREASON_QUEST_FAILED_LOW_LEVEL`. |
| `BUY_ERR_*` / `SELL_ERR_*` | Vendor refusals, e.g. `BUY_ERR_NOT_ENOUGHT_MONEY` (sic), `BUY_ERR_CANT_FIND_ITEM`, `SELL_ERR_CANT_SELL_ITEM`. |
| `EQUIP_ERR_*` | Bag/equip errors, e.g. `EQUIP_ERR_INVENTORY_FULL`, `EQUIP_ERR_CANT_EQUIP_LEVEL_I`, `EQUIP_ERR_NO_REQUIRED_PROFICIENCY`. |
| `LOOT_ERROR_*` | Loot refusals, e.g. `LOOT_ERROR_DIDNT_KILL` (not yours), `LOOT_ERROR_TOO_FAR`. |
| `ITEM_NOT_FOUND` | `equip()` / `sell()` with an item guid that is not in the bags. |

### Login reasons

| Reason | Meaning |
|---|---|
| `CHARACTER_NOT_FOUND` | No character with that name. |
| `ACCOUNT_NOT_ELIGIBLE` | The account name doesn't start with `ACSDK.HeadlessAccountPrefix`. |
| `ACCOUNT_NOT_PLAYER_LEVEL` | GM accounts can't be driven headless (their chat could run GM commands). |
| `ACCOUNT_ALREADY_ONLINE` / `CHARACTER_ALREADY_ONLINE` | Someone (a game client or another headless character on that account) is already logged in. One character per account. |
| `MAX_HEADLESS_CHARACTERS` | `ACSDK.MaxHeadlessCharacters` reached. |
| `SERVER_SHUTDOWN` / `LOGIN_FAILED` | Server stopping / the game rejected the login (see worldserver log). |

Test provisioning (`plumbing.DebugService`, needs `ACSDK.Debug.Enable = 1`): `create_character()` returns
`(ok, reason, guid, name)`. The name comes back as stored (the game capitalises it: `examplepaladin` ->
`Examplepaladin`). Reasons are `NAME_IN_USE`, `ACCOUNT_ALREADY_ONLINE` (log its character out first),
`ACCOUNT_NOT_ELIGIBLE`, or the game's own code such as `CHAR_NAME_INVALID_CHARACTER`.

### Lease, logout and prompt reasons

| Reason | Meaning |
|---|---|
| `ALREADY_LEASED` | Another controller holds it. Wait, or use `force=True` if takeover is allowed. |
| `NOT_HEADLESS` | Log the character in before claiming it. |
| `TAKEOVER_DISABLED` | `ACSDK.Lease.AllowForceTakeover = 0`. |
| `NOT_LEASED` / `NOT_HEADLESS_ONLINE` | Nothing to release / nothing to log out. |
| `UNKNOWN_PROMPT` / `WRONG_CHARACTER` | The prompt already closed (answered, timed out) or belongs to another character. |

### Helper reasons

- `helpers.travel()`: `NO_PATH` (no route from here), `NO_PROGRESS` (stopped getting closer for several replans), `DIED`, `TIMEOUT`.
- `helpers.kill()`: `LOW_HEALTH` (own health fell to `stop_below_health` percent; flee or rest), `DIED`, `TARGET_GONE`, `TIMEOUT`.
- `helpers.recover()`: `method` is `CORPSE` or `SPIRIT_HEALER` (used when the corpse cannot be reached; `corpse_run` says why);
  failures are `NO_SPIRIT_HEALER`, `CORPSE_UNREACHABLE`, `RELEASE_FAILED:<reason>`, `RECLAIM_FAILED:<reason>`.

## 5. Tips

- Keep one `Client` per controller; many characters can share it.
- Face your target before melee or casting: the game requires it, and headless characters don't turn by themselves.
- Mobs fight back. Low-level characters walking through dangerous zones may die; `char.state().dead` tells you.
- Server diagnostics: `await ac.server.stats()` (queue depth, events dropped, tick time).
