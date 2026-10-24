# AzerothCore-SDK — Project Plan

> **Audience:** an engineering agent (or human) picking up this project with **no prior context**.
> Read this whole document before writing code. It is the source of truth for scope, architecture,
> and sequencing. When a decision changes, update this file (see §13 Decision Log).

- **Status:** Planning — no code written yet. Repository contains only this file.
- **Owner:** Jacob (project owner; makes final calls on open decisions in §12)
- **Last updated:** 2026-10-21 (rev 8 — removed historical context not needed for implementation; see §13)
- **Use:** personal project, not for distribution.
- **Build target:** **Windows 10/11 x64, native (Visual Studio 2022 + CMake + vcpkg). DECIDED — do not switch platforms without the owner's approval.**

---

## 1. What we are building (one paragraph)

A **general-purpose Python SDK for controlling headless characters inside an AzerothCore World of
Warcraft (3.3.5a / WotLK) server**. A *headless character* is an ordinary player character that is
logged into the world with no game client attached. The SDK provides
the means to log such characters in, act through them, and observe the world; what a controller
does with that (scripts, tools, tests, automation, AI agents, anything else) is up to the controller.
Inside the server process (`worldserver`), a new C++ AzerothCore module (`mod-acsdk`) provides
**its own headless-character runtime** and hosts a **gRPC server**. Headless characters have **no
built-in behavior whatsoever**: they do nothing unless an external controller tells them to.
Multiple Python **controller scripts** connect
over gRPC, each claiming and driving one or more characters by sending the same opcode packets a game
client would (move, cast, attack, chat — §4.6), and reading game state (positions, health, auras, nearby units, pathfinding). The
Python package (`azerothcore_sdk`) wraps the generated gRPC stubs in an ergonomic **asyncio** API,
e.g. `await char.move_to(x, y, z)`, `await char.cast("Fireball", target=mob)`, plus async event streams.

## 2. Background for a newcomer

| Term | Meaning |
|---|---|
| **AzerothCore (AC)** | Open-source C++ server emulator for WoW 3.3.5a. Processes: `authserver` (login) and `worldserver` (the game world). Site: https://www.azerothcore.org/ · Repo: https://github.com/azerothcore/azerothcore-wotlk |
| **Module** | AC's plugin mechanism. A folder in `azerothcore-wotlk/modules/` that is compiled into the server and registers **script hooks** (`WorldScript`, `PlayerScript`, `UnitScript`, …). |
| **Headless character** | A normal player character, logged in by `mod-acsdk` with no game client and no network socket, driven entirely by an external controller. |
| **WorldSession** | Server-side object representing one connected client: owns the `Player`, receives client packets, sends server packets. A headless character is a `WorldSession` **with no network socket**. |
| **maps / vmaps / mmaps** | Data extracted from the game client. `maps` = terrain height, `vmaps` = line-of-sight geometry, `mmaps` = Recast/Detour navmeshes used for **server-side pathfinding** (`PathGenerator`). All three are required. |
| **World thread / map threads** | `World::Update` runs on the main thread each tick; maps (and the players in them) update on worker threads (`MapUpdate.Threads`). Game objects are **not thread-safe**. |
| **Controller** | An external program (normally a Python script using our SDK) that connects over gRPC and drives characters. |
| **gRPC** | RPC framework over HTTP/2 using Protocol Buffers (`.proto`). C++ server side; Python `grpcio` (`grpc.aio`) client side. |

## 3. Goals and non-goals

**Goals**
1. **Character state is 100% controller-owned.** The server never makes gameplay decisions for a character,
   never overrides a command, and never "resets" a character to a default state.
2. **Multiple controllers, multiple characters:** any number of controller scripts can connect at once;
   each character is driven by at most one controller at a time (§4.4); any controller may observe.
3. Every action is **async and awaitable**, resolving when it actually completes or fails, with a reason.
4. Expose game state (self, nearby world, static data), **server-side pathfinding**, and **chat**.
5. Event streams (chat, combat, death, auras, casts, loot, level-up, command results, raw packets opt-in).
6. Server stability: SDK misuse must never crash or stall `worldserver`.

**The runtime must NEVER act on its own**
- No AI, strategy, or decision-making of any kind in the server (no auto-combat, auto-follow,
  auto-loot, auto-heal, auto-rez, no "return to a default state").
- No logging characters in or out without an explicit controller (or config-declared) request.

**Other non-goals (for now)**
- **Creating accounts or characters.** Provisioning server accounts/characters is done separately,
  outside this project. The SDK only logs in and drives characters that already exist.
- Fixed performance/scale targets and performance tuning (see §4.5).
- Latency targets (see §4.2).
- Implementing the WoW client network protocol in Python (rejected — §14).
- Controlling **real human players'** characters.
- Public/internet exposure. Default is localhost only.

## 4. Architecture

```
Controller A (Python)   Controller B (Python)   Observer (Python)
        └───────── azerothcore_sdk (asyncio, grpc.aio) ─────────┘
                     ⇅ HTTP/2 127.0.0.1:50051 (server auth token; per-character lease tokens §4.4)
worldserver process
  └─ mod-acsdk  (our module — the only custom module)
       ├─ grpc/            gRPC server threads: validate, enqueue, await future
       ├─ dispatch/        CommandQueue (MPSC) → drained on WORLD THREAD in WorldScript::OnUpdate
       ├─ runtime/         Headless character runtime (our own; see §5)
       │    ├─ CharacterRegistry       guid → CharacterHandle (session, owner lease, mode, watchers)
       │    ├─ HeadlessSession        WorldSession subclass/wrapper, no socket
       │    ├─ Login/Logout      async DB load → Player creation → add to map
       │    ├─ PacketSink        captures server→"client" packets; opcode-first filter, discards unsubscribed (§4.5)
       │    └─ ProtocolReflexes  mechanical acks a real client would send (§5.2) — NOT decisions
       ├─ leases/          controller ownership & observers (§4.4)
       ├─ packets/         typed request → WorldPacket builders (CMSG_*) and SMSG_* decoders (§4.6)
       ├─ executors/       inject CMSG packets into the character's WorldSession; whitelisted direct calls only (§4.6)
       ├─ watchers/        per-tick completion checks for long-running commands
       ├─ snapshot/        state → plain structs/protos, built on the world thread
       └─ events/          EventBus ← exactly one source per event type (hook OR PacketSink, §4.7) → bounded queues
  └─ AzerothCore core      (+ a minimal, documented patch set only if hooks are missing — §12 D2)
```

### 4.0 Design principle: Plumbing & Porcelain (DECIDED)

The system is split like git's plumbing/porcelain:

| Layer | Lives in | Responsibility | Must NOT contain |
|---|---|---|---|
| **Plumbing** | C++ `mod-acsdk` + `.proto` API | A thin, mechanical **translation layer** between AzerothCore internals and the wire API: expose primitives that map ~1:1 onto AC concepts (move to point, cast spell id on guid, query nearby objects, emit event). Validation, thread marshalling, completion tracking, leases, protocol reflexes. | Convenience logic, gameplay policy, multi-step routines, name lookups that can be done client-side, retries, "smart" defaults |
| **Python plumbing client** | `azerothcore_sdk.plumbing` | 1:1 async wrappers over the generated stubs: typed models, error mapping, connection/lease mechanics. No logic beyond translation. | Composition of multiple calls |
| **Porcelain** | `azerothcore_sdk` (top level) + `azerothcore_sdk.helpers` | Ergonomic, composable helpers built **only from plumbing calls**: `Character` objects, `kill(target)` (path → approach → attack → cast loop), `wait_until(pred)`, spell-name resolution and caching, rotations, kiting, auto-accept-resurrect, multi-character coordination. | Anything that requires changing the server |

**Placement rule** (apply to every new feature):
1. If it can be built from existing plumbing calls with acceptable latency → **porcelain** (Python).
2. Put it in plumbing only if it (a) needs access to AC internals not otherwise exposed, (b) must
   react within a server tick (e.g. completion watchers, server-side follow via MotionMaster), or
   (c) would otherwise cost an unacceptable number of round trips / bytes for many controllers.
3. Plumbing primitives are **mechanism, not policy**: small, orthogonal, stable, versioned. Prefer
   adding a field or a primitive over adding a "do-everything" RPC.
4. Porcelain may change freely; plumbing changes follow the proto versioning rules (§7).

### 4.1 Hard invariants (do not violate)

1. **No gRPC thread ever dereferences a game object** (`Player*`, `Unit*`, `Map*`, `WorldSession*`…)
   or calls AC APIs that touch game state. gRPC threads only validate, enqueue, wait on a future,
   read immutable snapshot data, and write responses.
2. **All game mutations and live reads happen on the world thread** inside our
   `WorldScript::OnUpdate(diff)` hook (runs between map updates — **verify in Phase 0** by reading `World::Update`).
   The one deliberate hand-off: after the world thread has checked the lease and built a packet, it
   **queues the packet into the character's `WorldSession`**; AzerothCore then processes it on whichever
   thread its opcode table specifies (§4.6.1). We never process it ourselves on another thread.
3. **Bounded work per tick** (`ACSDK.MaxCommandsPerTick`, `ACSDK.MaxTickBudgetMs`). Excess stays queued.
4. **Bounded memory.** Every subscriber queue is bounded; on overflow drop oldest and emit `Lagged{dropped=N}`.
5. **GUIDs across the API, never pointers.** Re-resolve on the world thread each time; "gone" is a normal failure.
6. **Every RPC has a deadline; every long-running command has a server-side timeout.**
7. **No autonomy:** nothing in the runtime may issue a gameplay action that a controller did not request.
   The only server-initiated behaviors allowed are the protocol reflexes listed in §5.2, each
   individually configurable. If you're tempted to add "helpful" automatic behavior, put it in the
   Python SDK as an opt-in helper instead.
8. **Plumbing stays plumbing** (§4.0). Every C++ feature must justify itself under the placement rule.
9. **Lightweight by default** (§4.5): no per-controller threads, no work done for events nobody
   subscribed to, no unbounded buffers, and every per-controller resource has a configured cap.
10. **Never reinvent game logic** (§4.6). Character actions are performed by sending the same opcode /
    `WorldPacket` a real client would send, so AzerothCore's own handlers do all validation and
    game rules. Direct core calls are allowed only from the whitelist in §4.6.3.

### 4.2 Command lifecycle

```
Python call → RPC → Command{id, character_guid, lease_token, payload} enqueued
  → world thread: verify lease token (§4.4) → validate → execute
      → instant command: COMPLETED / FAILED immediately
      → long command:    STARTED, register CompletionWatcher
  → watcher (each tick): COMPLETED | FAILED(reason) | TIMEOUT | CANCELLED (superseded/explicit)
  → result returned to the unary RPC and also published as CommandResultEvent
```

- `await char.move_to(...)` resolves on the **terminal** state by default; `wait=False` returns a
  `CommandHandle` (`await handle.done()`, `handle.cancel()`).
- Commands live on **channels** per character. A new command on a channel supersedes the running one
  (old → `CANCELLED`). Only the controller does this — the server never does. Channels are defined
  by **how replies can be correlated** (§4.6.5, research R3), not merely by command type: two
  commands whose SMSG replies can't be told apart must share a channel, so at most one is in flight.
- **Latency:** there is **no latency target**. Expect a command to take effect roughly one to two
  server ticks after it arrives (our dispatcher tick, then the session's packet processing). This is
  inherent to running inside the server's update loop and is accepted.
- When a command finishes, the character simply **stays in the resulting state** (standing where it
  arrived, keeping its target, etc.). There is no idle/reset behavior.

| Command | Completed when | Failed when |
|---|---|---|
| MoveTo | within `tolerance` of target AND MotionMaster idle | no path, stuck (no progress N s), timeout, died, teleported |
| Follow | never auto-completes (until Stop / superseded) | target gone |
| CastSpell | `SMSG_SPELL_GO` / channel finished (§4.6.2) | `SMSG_CAST_FAILED` with AC's `SpellCastResult` (return enum name), interrupt |
| Attack | target dead (or until Stop if `until_stopped`) | `SMSG_ATTACKSWING_*` error packets, target gone |
| Say/Whisper | immediately after send | chat error SMSGs |

Results and reasons always come from AzerothCore's own responses (§4.6.2), never from our own checks.

### 4.3 Character lifecycle states

`OFFLINE → LOGGING_IN → ONLINE → LOGGING_OUT → OFFLINE`, plus `ONLINE` sub-flags (`dead`, `ghost`,
`in_combat`, `teleporting`). Transitions only happen on controller request, config autostart, or
genuine game events (death, server shutdown, GM kick). A character whose controller disconnects stays
`ONLINE` and frozen in place (see §4.4) — it is **not** logged out unless
`ACSDK.Lease.LogoutOnOrphanSec` is set (default 0 = never).

### 4.4 Multiple controllers: leases and observers

**Controllers must not be able to impersonate each other (DECIDED).**

- Connecting requires the server auth token (`ACSDK.AuthToken`) — this only proves "allowed to connect".
- `controller_id` (metadata) is a **human-readable label** for logs and `ListLeases`. It grants nothing.
- **Lease token:** `ClaimCharacter(character)` returns an **unguessable lease token** — ≥128 bits from a
  cryptographically secure RNG (e.g. OpenSSL `RAND_bytes`), encoded as a string — plus a TTL.
  **Every command and every lease operation for that character must carry the token**
  (`lease_token` field on every character-targeting request message).
- The server compares tokens in constant time, never logs them, and never returns another holder's
  token (`ListLeases` shows holder label and expiry only).
- A command with a missing, wrong, or expired token fails with `INVALID_LEASE_TOKEN` and has no effect.
- Tokens are bearer capabilities: valid until `ReleaseCharacter`, TTL expiry, or takeover. A controller that
  reconnects within the TTL may keep using its token. Renewal via `RenewLease(token)` or event-stream keepalive.
- `ClaimCharacter(force=true)` (if `ACSDK.Lease.AllowForceTakeover`) issues a new token and **invalidates
  the old one**; the previous holder gets `LeaseLost` and its in-flight commands are `CANCELLED`.
- Lease expiry (controller died): in-flight commands are cancelled, movement stops, character stays put.
  The character becomes **orphaned** and claimable. No other behavior.
- **Observers:** any authenticated connection may `GetState`/`Subscribe` on any character without a token.

### 4.5 Lightweight by design

**Aspiration, not a requirement:** the design should not *preclude* supporting up to roughly
1,000 characters and hundreds of concurrent controllers on one worldserver. There are **no fixed scale
or performance targets**, and **performance tuning is not a goal at this time**. The aim is simply
to avoid building in bloat. (Note: each character is a full player to the server, so the game's own cost
of many characters is outside the SDK's control.)

**Design defaults (cheap to do from the start; no tuning implied)**
| Area | Default |
|---|---|
| gRPC threading | gRPC C++ **callback (reactor) API**, small fixed thread pool. No thread per controller, stream, or character. |
| Connections | One HTTP/2 connection per controller; keepalive/idle timeouts; dead connections reaped. |
| Event streams | **One `Subscribe` stream per controller** carrying all its filters. |
| Zero cost when unused | Hooks check an "anyone subscribed to (event type, character)?" index first and return immediately if not. |
| Packet capture | **PacketSink looks only at the opcode first** and discards the packet immediately unless that opcode is needed by a subscription, a watcher, or a protocol reflex. No decoding of unwanted packets. |
| Fan-out | Each event is serialized once and the buffer shared by all matching subscribers. |
| Backpressure | Bounded per-subscriber queues (drop-oldest + `Lagged`); caps on in-flight commands per controller; excess → `RESOURCE_EXHAUSTED`. |
| Payload size | IDs/enums on the wire; `GetState` takes a section list; world queries require a radius and cap results. |
| Static data | Spell/item/creature info fetched once and cached client-side (porcelain). |
| Diagnostics | Simple counters (connections, streams, queue depths, dropped events) via `ServerService.GetStats`. |

**Client (porcelain) defaults**
- One `grpc.aio` channel per `Client`; many characters share it.
- Prefer event-driven waits over polling `GetState`.
- `uvloop` does not support Windows; use the default asyncio Proactor loop.

### 4.6 How plumbing performs actions: opcodes & WorldPackets (DECIDED)

**Principle:** the plumbing does not implement game behavior. A headless character is driven exactly the
way a game client drives a player — by **client→server opcodes (`CMSG_*` / `MSG_*`) delivered as
`WorldPacket`s to the character's `WorldSession`** — and it learns what happened from the
**server→client packets (`SMSG_*`)** AzerothCore sends back. AzerothCore's existing handlers
(`WorldSession::Handle…Opcode`) perform all checks and effects: range, line of sight, cooldowns,
reagents, power costs, facing, GCD, loot rules, quest rules, vendor rules, etc.

Background: opcodes are defined in `src/server/game/Server/Protocol/Opcodes.h`
(`enum Opcodes : uint16`, e.g. `CMSG_CAST_SPELL = 0x12E`, `CMSG_ATTACKSWING = 0x141`); the
opcode → handler table (with required session status and processing/thread type) is in
`Opcodes.cpp`. These are the 3.3.5a client protocol, which AC implements.

#### 4.6.1 Action path
```
Plumbing RPC (typed proto, e.g. CastSpellRequest{spell_id, target_guid})
  → world thread: lease/validate request shape only (character exists, lease held, fields sane)
  → packets/: build the exact WorldPacket the 3.3.5a client would send (CMSG_CAST_SPELL …)
  → enqueue into the character's WorldSession packet queue (same path as a socket-received packet),
    so AC's normal opcode routing applies its session-status and thread-safety rules
  → AC handler runs (all game logic lives there)
  → resulting SMSG_* packets captured by PacketSink → decoded → completion watcher + events
```
- **Prefer queueing into the session** over calling `Handle…Opcode` functions directly, so AC's
  opcode table decides status checks and which thread processes the packet. Calling a handler
  directly is allowed only if Phase 0 research shows queueing is impossible for socketless sessions,
  and then only on the thread the opcode table specifies.
- **Reuse AC's own serializers** where they exist (e.g. `SpellCastTargets` read/write, `MovementInfo`,
  `ObjectGuid` packing) instead of hand-rolling byte layouts. The packet format for each opcode is
  taken from the corresponding handler's read code — that is the specification.
- The plumbing **does not pre-check game rules** (no "is target in range?" before casting).
  It sends the packet and reports AC's answer. Only validate what's needed to build a well-formed
  packet and to protect the server (lease, rate limits, guid exists).

#### 4.6.2 Results come from SMSG packets
Completion and failure are derived from what AC sends back, decoded into proto events with the
server's own reason codes. Examples (exact opcodes to be confirmed in research R3):

| Primitive | Sent | Completed on | Failed on |
|---|---|---|---|
| CastSpell | `CMSG_CAST_SPELL` | `SMSG_SPELL_GO` (and channel end for channels) | `SMSG_CAST_FAILED` (SpellCastResult), `SMSG_SPELL_FAILURE` / interrupt |
| CancelCast | `CMSG_CANCEL_CAST` / `CMSG_CANCEL_CHANNELLING` | cast state cleared | — |
| Attack / StopAttack | `CMSG_SET_SELECTION` + `CMSG_ATTACKSWING` / `CMSG_ATTACKSTOP` | `SMSG_ATTACKSTART`; target death | `SMSG_ATTACKSWING_NOTINRANGE`, `_BADFACING`, `_DEADTARGET`, `_CANT_ATTACK` |
| SetTarget | `CMSG_SET_SELECTION` | immediate | — |
| Say/Whisper/Party | `CMSG_MESSAGECHAT` | immediate / echo | `SMSG_CHAT_PLAYER_NOT_FOUND`, etc. |
| UseItem | `CMSG_USE_ITEM` | resulting spell/effect packets | `SMSG_INVENTORY_CHANGE_FAILURE`, `SMSG_CAST_FAILED` |
| Loot | `CMSG_LOOT`, `CMSG_AUTOSTORE_LOOT_ITEM`, `CMSG_LOOT_MONEY`, `CMSG_LOOT_RELEASE` | `SMSG_LOOT_RESPONSE`, item push | `SMSG_LOOT_RESPONSE` with error |
| Interact / gossip | `CMSG_GOSSIP_HELLO`, `CMSG_GOSSIP_SELECT_OPTION` | `SMSG_GOSSIP_MESSAGE` | — |
| Quests | `CMSG_QUESTGIVER_*` | `SMSG_QUESTGIVER_*` | `SMSG_QUESTGIVER_QUEST_INVALID` |
| Vendors | `CMSG_LIST_INVENTORY`, `CMSG_BUY_ITEM`, `CMSG_SELL_ITEM` | `SMSG_LIST_INVENTORY`, `SMSG_BUY_ITEM` | `SMSG_BUY_FAILED`, `SMSG_SELL_ITEM` error |
| Equip / inventory | `CMSG_AUTOEQUIP_ITEM`, `CMSG_SWAP_INV_ITEM`, `CMSG_DESTROYITEM` | item update packets | `SMSG_INVENTORY_CHANGE_FAILURE` |
| Group / trade / duel / rez prompts | `CMSG_GROUP_ACCEPT`/`DECLINE`, `CMSG_*TRADE*`, `CMSG_DUEL_ACCEPTED`/`CANCELLED`, `CMSG_RESURRECT_RESPONSE` | respective SMSGs | respective error SMSGs |
| Release spirit / reclaim corpse | `CMSG_REPOP_REQUEST`, `CMSG_RECLAIM_CORPSE` | state packets | — |
| Protocol reflexes (§5.2) | `MSG_MOVE_WORLDPORT_ACK`, `MSG_MOVE_TELEPORT_ACK`, movement acks | — | — |

#### 4.6.3 Whitelist: where direct core calls are allowed
Only where **no client opcode exists for the job**, and always calling AC's existing logic, never
re-implementing it. Each entry needs a one-line justification in code.

| Area | Allowed direct use | Why |
|---|---|---|
| Character session lifecycle | Session creation without socket, character load/login, logout | The client equivalent is a network connection, not an opcode we can inject |
| Server-side movement | `MotionMaster` (`MovePoint`, `MoveFollow`, …) with `PathGenerator` | A real client simulates movement locally and *reports* positions via `MSG_MOVE_*`; we have no client simulation, and synthesizing MSG_MOVE streams would mean reinventing client movement physics. Using AC's own server-side movement is the non-reinventing option. (Research R3 confirms; facing may be either `MSG_MOVE_SET_FACING` or AC's facing API.) |
| Read-only queries | `PathGenerator`, `Map::GetHeight`, line-of-sight, grid searchers, `ObjectAccessor`, spell/item/creature stores, reading Player/Unit fields for snapshots | Pure reads of existing state; no game logic invented |
| Packet capture | Hooking/intercepting packets sent to character sessions | Required for results/events |

Anything not on this list requires a §13 log entry and owner approval. **Forbidden examples:**
calling `Unit::CastSpell`/`Player::CastSpell` for a player-initiated cast, writing our own range/
cooldown/reagent checks, granting loot or quest credit directly, modifying inventory or money
directly, setting health/power/auras directly.

#### 4.6.4 Raw packet escape hatch
`ActionService.SendClientPacket(character, opcode, payload_bytes)` — sends an arbitrary client opcode
for which no typed primitive exists yet. Restricted to `CMSG_*`/`MSG_*` opcodes, gated by config
(`ACSDK.RawPackets.Enable`, default 0) and an allowlist/denylist, rate-limited, and still subject to
AC's handlers and session status rules. Paired with an opt-in raw `SMSG` event stream. Porcelain
can prototype new features on this before a typed primitive is added (§12 D10).

#### 4.6.5 Correlating replies with commands (DECIDED approach; details from R3)
Most `SMSG` replies carry no request id, and a character can receive similar packets caused by others
(e.g. someone else's spell on it). Each primitive therefore needs a documented **correlation
strategy**, recorded per opcode in research R3 (`docs/research/opcode-map.md`). Strategies, in order of preference:
1. **Echoed field:** use a value the client sends that the server echoes back (e.g. the cast-count
   byte in `CMSG_CAST_SPELL`, returned in `SMSG_CAST_FAILED`; verify in R3).
2. **Matching keys:** match on caster guid + spell id / target guid / item guid, etc.
3. **Single in flight:** if neither is possible, the primitive's channel allows only one command in
   flight per character, so the next matching reply is unambiguous.
Watchers must ignore replies that don't match their command. Commands whose replies can't be
correlated at all must be documented as fire-and-forget (`COMPLETED` = packet accepted).

### 4.7 State and events: where data comes from (DECIDED)

- **Character state (`GetState`, snapshots) is read directly from AzerothCore objects** (`Player`,
  `Unit`, inventory, quest log…) on the world thread — a whitelisted read (§4.6.3).
- **We never rebuild state by decoding `SMSG_UPDATE_OBJECT`** (or other object-update / visibility
  packets). Those are by far the highest-volume packets a character receives; decoding them across many
  characters would be very expensive. PacketSink discards them unless a controller opts into raw packets.
- **Exactly one official source per event type.** Each event type in `event.proto` is produced by
  **either** an AzerothCore script hook **or** a captured SMSG packet — never both — so controllers
  never see duplicates. The mapping lives in a single table (`docs/events.md`, mirrored as a
  comment in `event.proto`). Rule of thumb: use the SMSG packet when the event is "what the
  player was told" (chat, cast failures, prompts, loot window), and a script hook when no packet
  describes it cleanly or the hook is cheaper. Changing an event's source is a §13 log entry.

## 5. The headless-character runtime

This is the main new engineering work and must be designed carefully: logging characters in with
no socket, keeping their sessions alive, injecting and capturing packets, and logging them out
safely, all using AzerothCore's own code paths.

**Licensing:** this is a personal-use project that will not be distributed, so no license
compliance work is required. Revisit if the project is ever distributed (AzerothCore is GPL-2.0;
gRPC/Abseil are Apache-2.0).

### 5.1 Character identity and persistence
- Headless characters are **normal, pre-existing characters** on designated accounts (config: `ACSDK.HeadlessAccountPrefix`, or an explicit list).
- **Accounts and characters are created outside this project.** The SDK never creates accounts or
  characters; it logs in existing characters by name/guid.
- Protocol constraint: one online character per account, so each concurrently online character needs its
  own account. Whoever provisions accounts must account for this; the SDK just reports a clear
  error (`ACCOUNT_ALREADY_ONLINE`) if it's violated.
- Characters save via the normal AC save cycle; a character's world state persists across logins like a player's.
- Optional `ACSDK.Autostart.Characters = "Name1,Name2"` to log specific characters in at server start
  (config-declared, therefore not autonomy).

### 5.2 Protocol reflexes (the ONLY automatic behaviors allowed)
A real client automatically answers some server packets; without these a headless character gets stuck.
These are mechanical and do not decide gameplay. Each is a config toggle; where a choice exists it
is surfaced as an event so the controller decides.

| Situation | Default | Notes |
|---|---|---|
| Far teleport (`SMSG_NEW_WORLD` → client sends `MSG_MOVE_WORLDPORT_ACK`) | auto-ack | Without this the character is stuck mid-teleport. |
| Near teleport (`MSG_MOVE_TELEPORT` → `MSG_MOVE_TELEPORT_ACK`) | auto-ack | |
| Movement/speed-change acks the server expects | auto-ack | Identify which ones AC actually checks. |
| Time sync / keepalive the session needs to stay valid | auto | Whatever prevents the session being considered dead. |
| Group invite, trade request, duel, resurrect offer, summon, loot roll, ready check | **event only** | Controller responds via `RespondToPrompt`. Configurable timeout → decline. |

Phase 0/2 task: build the definitive list by reading how AC handles a socketless session and which
client responses its handlers wait for.

### 5.3 Core patches
Goal: **stock AzerothCore + our module**, with zero core edits if possible. Phase 0 research R1
lists every core capability the runtime needs (socketless session creation and ticking, packet
injection, outgoing-packet capture, login/logout paths) and classifies each as (a) already possible
through existing AC APIs/script hooks or (b) requiring a core change. Keep (b) as a small patch
series in `server/core-patches/` with a script to apply it, and consider upstreaming the hooks to
AzerothCore. Decision tracked as §12 D2.

### 5.4 Execution model for character updates
- Character `WorldSession`s must be updated by the world each tick like real sessions (verify how a
  socketless session is ticked and that AC does not drop it as disconnected).
- Executors run on the world thread from the dispatcher. If a future feature requires acting
  during the character's own map update (on a map thread), do it through a per-character inbox processed in a
  `PlayerScript` update hook — never by touching the character from another thread.

## 6. Repository layout (target)

```
AzerothCore-sdk/
├─ PLAN.md                         ← this file
├─ README.md
├─ proto/acsdk/v1/                 ← single source of truth for the API
│   ├─ common.proto  server.proto  character.proto  lease.proto  action.proto  world.proto  event.proto
├─ server/
│   ├─ mod-acsdk/                  ← AzerothCore module (symlink/junction into azerothcore-wotlk/modules/)
│   │   ├─ src/ (loader, grpc/, dispatch/, runtime/, leases/, executors/, watchers/, snapshot/, events/, util/)
│   │   ├─ conf/mod_acsdk.conf.dist
│   │   └─ CMake integration (see Phase 0 Spike A)
│   ├─ vcpkg.json                 ← gRPC/protobuf deps (manifest mode, x64-windows)
│   └─ core-patches/               ← only if §12 D2 requires; numbered .patch files + apply script
├─ python/
│   ├─ pyproject.toml
│   └─ azerothcore_sdk/
│       ├─ plumbing/   (1:1 async wrappers over stubs: channel.py, services.py, models.py, errors.py)
│       ├─ _generated/ (protoc output — never hand-edit)
│       ├─ client.py, character.py, world.py, events.py   (porcelain core objects)
│       └─ helpers/    (porcelain routines: combat.py, movement.py, spells.py, prompts.py …)
├─ examples/   (hello_character.py, kill_a_mob.py, chat_echo.py, two_controllers.py)
├─ tests/      (python integration tests against a running test realm)
├─ scripts/    (PowerShell: gen_proto.ps1, build.ps1, link_module.ps1, run_realm.ps1)
└─ docs/       (windows-build.md, events.md, research/)
```

`_generated/` is produced by `scripts\gen_proto.ps1` using `grpcio-tools`; never hand-edit.

## 7. API surface (v1 draft)

Package `acsdk.v1`. Add fields, never renumber or reuse tags; breaking changes → `v2`.

| Service | RPCs |
|---|---|
| `ServerService` | `GetInfo` (versions, realm, uptime, tick rate), `Health`, `GetStats` (diagnostic counters, §4.5) |
| `CharacterService` | `List` (all characters + state + lease holder label), `Login`, `Logout` (token required if leased), `GetState` |
| `LeaseService` | `ClaimCharacter` → lease token + TTL, `ReleaseCharacter(token)`, `RenewLease(token)` (or implicit via keepalive), `ListLeases` (labels/expiry only, never tokens) |
| `ActionService` (every request carries `lease_token`) | `MoveTo`, `Stop`, `Follow`, `Face`, `SetTarget`, `Attack`, `CastSpell` (**spell id only**; names resolved in porcelain), `UseItem`, `Interact`, `Loot`, `Say`/`Whisper`/`PartySay`, `RespondToPrompt` (invite/trade/duel/rez/summon/roll), `Cancel(command_id)`, `Execute(oneof Command)` batching |
| `WorldService` | `FindPath` (mmaps), `GetHeight`, `LineOfSight`, `NearbyObjects` (type, radius, hostile, alive), `GetObject`, `GetSpellInfo` / `GetItemInfo` / `GetCreatureInfo` |
| `EventService` | `Subscribe(filter)` → stream of `Event` (oneof: Chat, DamageDealt/Taken, Death, AuraApplied/Removed, SpellCastStart/Finish, LevelUp, Loot, Prompt, LeaseLost, CharacterStateChanged, CommandResult, RawPacket (opt-in), Lagged) |

`GetState(character, sections=[...])` returns only the requested sections of: guid, name, class/race/level, lifecycle state, lease holder, map/zone,
position+orientation, health/power, auras, cooldowns, target, combat flag,
inventory/equipment, money, quest log, group.

Illustrative proto fragment:

```proto
syntax = "proto3";
package acsdk.v1;

message Vector3 { float x = 1; float y = 2; float z = 3; }

message CharacterRef {
  uint64 character_guid = 1;
  string lease_token = 2;     // from ClaimCharacter; required on every command
}

message MoveToRequest {
  CharacterRef character = 1;
  uint32 map_id   = 2;
  Vector3 target  = 3;
  float tolerance = 4;        // default 1.0
  uint32 timeout_ms = 5;      // default 30000
  bool wait = 6;              // true: return on terminal state
}

enum CommandStatus { COMMAND_STATUS_UNSPECIFIED = 0; STARTED = 1; COMPLETED = 2;
                     FAILED = 3; TIMEOUT = 4; CANCELLED = 5; }

message CommandResult {
  uint64 command_id = 1;
  CommandStatus status = 2;
  string reason = 3;          // e.g. "NO_PATH", "INVALID_LEASE_TOKEN", "SPELL_FAILED_OUT_OF_RANGE"
}

service ActionService {
  rpc MoveTo(MoveToRequest) returns (CommandResult);
  // ...
}
```

Target Python ergonomics:

```python
import asyncio
from azerothcore_sdk import Client, events

async def main():
    async with Client("127.0.0.1:50051", token="...", controller_id="grinder-1") as ac:
        char = await ac.characters.login("Thrall")    # no-op if already online
        async with char.lease():                      # ClaimCharacter; token kept internally,
                                                      # attached to every command; released on exit
            mob = (await char.nearby(kind="creature", hostile=True, radius=30))[0]
            await char.move_to(*mob.position, tolerance=5)
            await char.attack(mob)
            res = await char.cast("Fireball", target=mob)
            if not res.ok:
                print("cast failed:", res.reason)
            async for ev in char.events(events.Chat, events.Death, events.Prompt):
                print(ev)

asyncio.run(main())
```

Wire format: **protobuf binary over gRPC** on the hot path (smallest/cheapest). JSON is available for
debugging, logging and tooling via protobuf's canonical JSON mapping (`MessageToJson`), not as a
second transport — see §12 D8.

In the example above, `char.cast("Fireball", ...)` is porcelain: it resolves the name to a spell id
via a locally cached `GetSpellInfo` table, then calls the plumbing `CastSpell(spell_id)`.

SDK conventions: everything async (`grpc.aio`); typed dataclasses in `models.py` (don't leak raw
protobuf); exceptions in `errors.py` (`CommandFailed(reason)`, `CharacterNotFound`, `InvalidLeaseToken`,
`LeaseLost`, `Timeout`); Python ≥ 3.11; fully type-hinted. Convenience behaviors (e.g.
"auto-accept rez", "kite", "rotation runner") live in `azerothcore_sdk.helpers` as **opt-in** code
running in the controller — never in the server.

## 8. Configuration (`mod_acsdk.conf.dist`)

```
ACSDK.Enable = 1
ACSDK.BindAddress = "127.0.0.1"
ACSDK.Port = 50051
ACSDK.AuthToken = ""                 # required; gRPC listener refuses to start if empty
ACSDK.MaxCommandsPerTick = 200
ACSDK.MaxTickBudgetMs = 5
ACSDK.EventQueueSize = 4096
ACSDK.CommandQueueSize = 4096        # queued world-thread commands; full → RESOURCE_EXHAUSTED
ACSDK.MaxListResults = 1000          # cap on CharacterService.List
ACSDK.HeadlessAccountPrefix = "acsdkhc"  # accounts whose characters may be logged in headless
ACSDK.MaxHeadlessCharacters = 50
ACSDK.Autostart.Characters = ""      # comma-separated names logged in at startup
ACSDK.Lease.TtlSec = 15
ACSDK.Lease.AllowForceTakeover = 1
ACSDK.Lease.LogoutOnOrphanSec = 0    # 0 = never log out orphaned characters
ACSDK.Reflex.TeleportAck = 1
ACSDK.Reflex.MovementAcks = 1
ACSDK.Prompt.TimeoutSec = 60         # unanswered invite/trade/etc. → decline
ACSDK.RawPackets.Enable = 0          # §4.6.4
```

## 9. Phased plan with acceptance criteria

Work in order; each phase ends with its acceptance criteria demonstrably met and a note in §13.

### 9.0 Build environment (DECIDED: native Windows x64)

All server builds, scripts and docs target **native Windows x64**. Linux/WSL2/Docker are out of
scope for now; keep code portable (no Win32-only APIs in module code, use std/boost) so Linux can be
added later, but do not spend effort on it.

| Component | Requirement / notes |
|---|---|
| OS | Windows 10/11 x64 |
| Compiler | Visual Studio 2022, "Desktop development with C++" workload (MSVC v143), x64 only |
| Build system | CMake (version per AzerothCore's Windows install guide), Visual Studio generator or Ninja from a VS Developer prompt |
| AzerothCore deps | MySQL 8.x (server + dev libs), Boost, OpenSSL 3.x Win64 — follow AzerothCore's official **Windows** installation guide for exact versions and env vars (e.g. `BOOST_ROOT`) |
| gRPC / protobuf | **vcpkg**, triplet `x64-windows`, installed in manifest mode (`vcpkg.json` in `server/`) and wired in via `CMAKE_TOOLCHAIN_FILE` (chain with any toolchain AC sets via `VCPKG_CHAINLOAD_TOOLCHAIN_FILE` if needed) |
| Runtime library | Everything must use the **same CRT** as worldserver (expected dynamic `/MD` — verify in AC's CMake). Use the vcpkg triplet that matches |
| OpenSSL conflict | gRPC via vcpkg may pull its own OpenSSL. Ensure exactly **one** OpenSSL is linked into worldserver — preferably point AC at vcpkg's OpenSSL, or gRPC at AC's. Spike A must resolve and document this |
| Module install | Repo lives at `<projects>\AzerothCore-sdk`; link the module into the AC source tree with a directory junction: `mklink /J <ac>\modules\mod-acsdk <repo>\server\mod-acsdk` |
| Scripts | PowerShell (`scripts\*.ps1`): `gen_proto.ps1`, `build.ps1` (configure + build RelWithDebInfo), `link_module.ps1`, `run_realm.ps1` |
| Python | CPython ≥ 3.11 for Windows, venv in `python\.venv`; `grpcio`, `grpcio-tools`, `protobuf` from PyPI wheels |
| Debugging | Build `RelWithDebInfo` for daily work, `Debug` when chasing crashes; attach VS debugger to `worldserver.exe`; keep `.pdb`s |
| Data | Extract maps/vmaps/mmaps with AC's Windows extractor tools from a 3.3.5a client |

### Phase 0 — Environment, research & de-risking spikes
1. Clone `azerothcore/azerothcore-wotlk` (master). Record the SHA in §13.
2. Build AC natively on Windows per §9.0 (VS 2022, x64); set up MySQL, DBs, `authserver`, `worldserver`. Write the exact reproducible steps to `docs/windows-build.md`.
3. Extract **maps, vmaps, mmaps**; enable in `worldserver.conf`.
4. **Research R1 — core capability gaps:** for each capability in §5.3, find the AC code path
   (file/function) and whether existing APIs/script hooks suffice → `docs/research/core-gaps.md`.
5. **Research R2 — headless session mechanics:** from AC source, document how a `WorldSession`
   without a socket can be created, ticked and kept alive; how a character is loaded, added to the
   world and safely removed; how outgoing packets to a session can be intercepted; and how Players
   can be moved server-side with `MotionMaster`/`PathGenerator` → `docs/research/headless-sessions.md`.
   Include file/function references.
   *Optional prior art:* mod-playerbots (https://github.com/mod-playerbots/mod-playerbots) already implements socketless sessions and packet injection/capture; consult it only for these mechanics, not as a dependency or design model.
6. **Spike A — gRPC in worldserver:** gRPC C++ + protobuf linked into `worldserver` via our module,
   serving `Ping`, built with MSVC + vcpkg (`x64-windows`). Investigate how AC modules add external
   link deps (module CMake hooks / AC cmake macros). Resolve /MD vs /MT, duplicate OpenSSL, and
   Abseil/protobuf DLL deployment (copy vcpkg DLLs next to `worldserver.exe` or link statically
   with a matching-CRT triplet). Document the recipe in `docs/windows-build.md`.
7. **Research R3 — opcode map:** for every planned primitive, record the client opcode(s), the
   AC handler (file/function), the exact packet layout read by the handler, required session
   status/processing thread, the SMSG responses for success and each failure, and the
   **correlation strategy** (§4.6.5): which echoed field or matching keys tie a reply to the
   command that caused it, or that the primitive must be single-in-flight / fire-and-forget. From
   this, define the per-character command **channels**. Also propose the **official source** (hook or
   SMSG) for each planned event type → `docs/events.md` (§4.7). Output → `docs/research/opcode-map.md`.
   Also confirm a socketless session's packet queue can be fed (injection path in §4.6.1).
8. **Spike B — minimal headless login:** from module code on stock AC, log in one existing
   character with no socket, keep it online for 10 minutes, move it with `MotionMaster`, log it out
   cleanly. Also inject one `CMSG_MESSAGECHAT` (say "hello") and capture the resulting `SMSG_MESSAGECHAT`.
   Note every core obstacle hit.
9. Verify the threading claim in §4.1 #2.

Acceptance: `Ping` works from Python; Spike B character stays online, moves, and speaks via an injected
packet whose SMSG echo is captured; research docs (R1–R3) and `docs/events.md` written; decision D2 made.

### Phase 1 — Skeleton
Module loader + config; CommandQueue + world-thread Dispatcher with budget; auth interceptor
(`authorization: Bearer <server token>`, `x-acsdk-controller-id` label); proto codegen; Python `Client`
(connect/close/async context manager); `GetInfo`, `Character.List`, `GetStats`.
Use the gRPC callback API from the start (§4.5).
Acceptance: `examples/hello_character.py` connects and lists eligible headless characters; connection without
the server token is rejected.

### Phase 2 — Headless runtime & leases
Production-quality HeadlessSession, Login/Logout (async DB load, never blocking the world thread on DB),
PacketSink, ProtocolReflexes (teleport acks first), CharacterRegistry, lifecycle states, leases &
observers, `GetState`, autostart, safe behavior on server shutdown.
Lease tokens per §4.4 (CSPRNG, constant-time compare, never logged).
Acceptance: two controllers each lease a different character; a third observes both; **a controller
using another's `controller_id` but without the token cannot command, release, or renew that character
(`INVALID_LEASE_TOKEN`)**; a forced takeover invalidates the old token; killing a controller
orphans its character, which stays put and is re-claimable; a GM `.tele`/summon on a character completes via
the reflex; characters never move or act when unclaimed (verified over 30 minutes).

### Phase 3 — Core actions
`packets/` builder/decoder library with golden-byte unit tests per opcode; MoveTo (MotionMaster +
PathGenerator), Stop, Follow, Face, SetTarget, Attack, CastSpell, Say/Whisper — all player actions
via injected CMSG packets (§4.6); SMSG-driven watchers using the R3 correlation strategies
(§4.6.5); channels/supersede/cancel; timeouts.
Acceptance: `examples/kill_a_mob.py` finds, paths to, attacks, casts on, and kills a mob with every
await resolving to the correct terminal status; failure cases return meaningful reasons; a spell
cast *on* the character by another player does not resolve the character's own pending cast.

### Phase 4 — Events & prompts
EventBus with exactly one source per event type per `docs/events.md` (§4.7) — AC script hooks
(exact names: check `src/server/game/Scripting/`; names have changed over time) or PacketSink;
filtered `Subscribe`; bounded queues + `Lagged`; Prompt events + `RespondToPrompt`.
Acceptance: `examples/chat_echo.py` echoes whispers; a group invite to a character arrives as a Prompt
and is accepted via RPC; no event type is ever delivered twice for one occurrence; a stalled
client doesn't affect server tick time.

### Phase 5 — World queries
`FindPath`, `GetHeight`, `LineOfSight`, `NearbyObjects`, `GetObject`, static info lookups.
Acceptance: Python can plot a `FindPath` result; LoS/height agree with in-game behavior.

### Out of scope for now
Broader gameplay primitives (vendors, quests, groups, mounts, …), hardening/release work, and any
agent/RL mode are **not current requirements** and have been removed from the plan. Add new phases
here only with the owner's approval.

## 10. Testing strategy
- **C++:** keep pure logic (queue, budgeting, lease state machine, watcher state machines, filters)
  free of game types so it's unit-testable.
- **Integration:** pytest + `pytest-asyncio` against a local test realm; each test logs in/claims
  its own character and cleans up.
- **No-autonomy test:** log in N characters, claim none, run 30+ minutes; assert zero position/state
  changes not explained by external game events.
- **Security:** lease-token tests (missing/wrong/expired/stolen-label/forced-takeover) in Phase 2.
- **Load/soak testing and performance tuning:** deferred (not a current goal, §4.5).

## 11. Coding conventions
- C++: follow AzerothCore style; namespace `acsdk`; debug-assert world-thread id in executors and runtime.
- Python: `ruff` + `mypy --strict`; public API documented.
- Don't commit generated stubs (revisit if it hurts usability).

## 12. Open decisions (owner decides; record outcome in §13)

| ID | Question | Options | Recommendation |
|---|---|---|---|
| ~~D1~~ | Dev/build platform | **DECIDED 2026-10-21: native Windows x64 (VS 2022 + CMake + vcpkg).** See §9.0 | — |
| ~~D2~~ | Core base | **DECIDED 2026-10-22: (a) stock AC, zero core patches** (Phase 0 R1/R2/Spike B; minimum AC revision `92fed92ea`) | — |
| ~~D3~~ | Name-based spell lookup | **DECIDED 2026-10-21: porcelain (Python) resolves names → ids from cached `GetSpellInfo`; plumbing accepts ids only** (follows §4.0) | — |
| D4 | Snapshot strategy | On-demand via queue / periodic published snapshots | On-demand first |
| D5 | Commit generated stubs | Yes / No | No |
| D6 | Force takeover of leases | Always allowed / admin-only / disabled | Allowed by config, default on for dev |
| D7 | Which accounts' characters may be logged in headless | Prefix-matched accounts / explicit list / dedicated DB flag | Prefix-matched for now (accounts themselves are provisioned outside this project) |
| D8 | JSON on the wire? | Protobuf-only (JSON for debug/logging) / also expose a JSON gateway (e.g. HTTP+JSON) for non-Python controllers | Protobuf-only for now; a JSON gateway can be added later as a separate process so it never burdens worldserver |
| D9 | Command transport for chatty controllers | Unary RPCs + `Execute(batch)` / add bidirectional `CommandStream` | Start unary + batch; revisit later |
| D10 | Raw `SendClientPacket` escape hatch | Off by default + allowlist / not provided | Provide, off by default (§4.6.4) |
| D11 | Transport | gRPC (default) / custom length-prefixed protobuf over Boost.Asio TCP (fallback) | Keep gRPC behind a thin transport layer so protobuf messages stay the contract; switch to the Asio fallback only if Spike A fails on Windows |

## 13. Decision & progress log
_Append entries as `YYYY-MM-DD — <decision or milestone> — <rationale/notes>`._

- 2026-10-21 — Adopted in-process gRPC module + Python asyncio SDK architecture.
- 2026-10-21 — Added multi-controller lease model (one driver per character, unlimited observers; orphaned characters freeze in place).
- 2026-10-21 — **Adopted Plumbing & Porcelain architecture** (§4.0): C++ plumbing is a thin translation layer between AzerothCore and the gRPC/protobuf API; all helper/convenience logic lives in the Python SDK porcelain. D3 resolved accordingly (spell names resolved in Python).
- 2026-10-21 — **Lightweight design** (§4.5): avoid memory/CPU bloat with many controllers (scale later made aspirational, see rev 6 entry); callback gRPC API, single stream per controller, zero-cost hooks, serialize-once fan-out, bounded everything.
- 2026-10-21 — **Plumbing performs actions via opcodes/WorldPackets, never reimplemented game logic** (§4.6): player actions are injected as the client's `CMSG_*` packets into the character's WorldSession so AC's handlers do all validation/effects; results come from `SMSG_*` packets; direct core calls limited to a whitelist (session lifecycle, server-side movement, read-only queries, packet capture).
- 2026-10-21 — **D1 decided: build target is native Windows x64** (Visual Studio 2022, CMake, vcpkg for gRPC/protobuf, PowerShell scripts). Linux/WSL2/Docker out of scope for now; keep module code portable.
- 2026-10-21 — **Phase 0 review resolutions (rev 6):**
  - No latency target; ~1–2 tick command latency accepted (§4.2).
  - Packet injection hand-off made explicit in invariant 2 (§4.1).
  - Exactly one official source (hook or SMSG) per event type (§4.7).
  - Character state is read directly from AC objects; `SMSG_UPDATE_OBJECT` is never decoded for state (§4.7).
  - Reply↔command correlation strategy per opcode added to research R3; channels defined from it (§4.6.5).
  - Scale (~1,000 characters / hundreds of controllers) is an aspiration, not a requirement; fixed targets, load harness and tuning removed (§4.5).
  - Account and character creation are out of scope (done separately); `CreateCharacter` removed (§5.1).
  - Controllers cannot impersonate each other: `ClaimCharacter` returns an unguessable lease token required on every command (§4.4).
  - PacketSink filters on opcode first and discards anything unneeded (§4.5).
  - Personal use only, not distributed: no license compliance work (§5).
  - Scope limited to Phases 0–5.
- 2026-10-21 — Plan consolidated (rev 8): historical context not needed for implementation removed.
- 2026-10-22 — **Phase 0 started.** AzerothCore cloned at `0540ae8013bc92d93f1f0d3346ccbc1a1a4d6f85` (master) to `<projects>\azerothcore-wotlk`. Minimum supported AC revision: `92fed92ea` (adds `WorldSession::IsHeadless`, `NextQueuedPacket`, `ServerScript::OnPacketSent`; see `docs/research/core-gaps.md`).
- 2026-10-22 — **Toolchain:** Visual Studio Community 2026 (MSVC 14.51) accepted by owner in place of VS 2022. CMake 4.4, MySQL 8.4, Python 3.12 via winget.
- 2026-10-22 — **Deps:** Boost and OpenSSL come from vcpkg too (not separate installers), triplet `x64-windows-static-md` (static libs, /MD CRT) instead of `x64-windows`, so worldserver links exactly one OpenSSL and needs no extra DLLs. Build tree at `C:\acsdk-build` (short, space-free path).
- 2026-10-22 — **Research R1–R3 findings (proposed, owner to confirm):** D2 → (a) zero core patches is feasible. Consequences: mod-acsdk owns headless sessions in its own registry (null-socket sessions registered with `WorldSessionMgr` are deleted on first update); AC never drains a null-socket session's receive queue, so the module drains `NextQueuedPacket()` and invokes the opcode handler itself, replicating AC's status checks and honoring each opcode's processing place (amends §4.6.1). §4.1 #2 wording: `WorldScript::OnUpdate` runs *after* map updates complete (safe to touch Players). Fallback if the glue proves fragile: one-line core patch at `WorldSession.cpp:414`.
- 2026-10-22 — **Spike A passed.** gRPC (vcpkg 1.81.1) + protobuf statically linked into worldserver via `server/mod-acsdk/mod-acsdk.cmake` (AC's per-module `<name>.cmake` hook); callback API `ServerService.Health/GetInfo` called from Python `examples/ping.py`; wrong token → `UNAUTHENTICATED`. worldserver.exe depends on no gRPC/OpenSSL DLLs (only `libmysql.dll`, which carries its own private OpenSSL DLLs). AC's startup asserts `legacy.dll` exists next to the exe; our static OpenSSL has the legacy provider built in (`no-module`), so `run_realm.ps1` ships vcpkg's matching `legacy.dll` only to satisfy the check (verified it is never loaded). Graceful-shutdown path (`OnShutdown` → listener stop) not yet exercised: the console ignores redirected stdin; test via GM `.server shutdown` once accounts exist.
- 2026-10-22 — **Phase 1 skeleton working (pending Spike B for Phase 0 sign-off).** `CommandQueue` (bounded MPSC) drained in `WorldScript::OnUpdate` under `MaxCommandsPerTick`/`MaxTickBudgetMs`; DB work via async queries on a world-thread `QueryCallbackProcessor`; `ServerService.GetStats`; `CharacterService.List` (accounts matching `HeadlessAccountPrefix`); Python `Client` + `examples/hello_character.py`. Server-token check is a per-handler `Authorize()` call rather than an interceptor, because gRPC C++ server interceptors cannot fail a call. Clean shutdown verified (CTRL_BREAK → listener stopped → "Halting process", exit 0); shutdown runs `grpc::Server::Shutdown` on a helper thread while the world thread keeps pumping so world-thread RPCs can finish. Note: worldserver shuts down when its console stdin hits EOF, so scripted runs must keep stdin open.
- 2026-10-22 — **Spike B passed on stock AC (zero core patches).** Test account `acsdkhcexample1` / character Examplechar (Human Warrior) logged in headless, stayed ONLINE for 10 minutes without moving on its own, moved 8 yd server-side via `MotionMaster::MovePoint` + PathGenerator, injected `CMSG_MESSAGECHAT` say "hello" whose `SMSG_MESSAGECHAT` echo was captured via `ServerScript::OnPacketSent`, logged out cleanly (position persisted across logins), and worldserver shut down cleanly. Obstacles hit: none in the core; MSVC needs gRPC headers included before AC's (`ByteBuffer` friend lookup). Not yet exercised: teleport reflexes (`.tele`/summon) and GM `.kick` — need a GM account; covered by Phase 2 acceptance. Test accounts `acsdkhcexample1`/`acsdkhcexample2` were created with owner approval through the dev-only `DebugService` (AccountMgr + injected `CMSG_CHAR_CREATE`); account/character creation stays out of the SDK proper (§5.1).
- 2026-10-22 — **D2 decided by owner: (a) stock AzerothCore, zero core patches.** mod-acsdk owns headless sessions and drains injected packets itself (amends §4.6.1 as logged above).
- 2026-10-22 — **Phase 2 acceptance met.** `tests/` (pytest; `tests/harness.py` starts worldserver and drives its console) passes: two controllers each lease a character and a third observes both; same-label impersonation without the token cannot command/release/renew/logout (`INVALID_LEASE_TOKEN`); forced takeover invalidates the old token; a dead controller's lease expires, movement stops, the character stays put and is re-claimable; GM `.tele` far (map 0→1) and near complete via the teleport-ack reflex; GM `.kick` logs the character out cleanly; 30-minute no-autonomy run with two unclaimed characters showed zero state changes (60 samples). Lease tokens: 256-bit `RAND_bytes`, hex, `CRYPTO_memcmp`. `GetState` sections so far: identity, position, vitals, status, target, money (auras, cooldowns, inventory, quests, group pending). Bug fixed on the way: `ByteBuffer::contents()` throws on empty packets, which crashed worldserver from `OnPacketSent`. Movement-change acks (root/hover/etc., §5.2) not yet implemented.
- 2026-10-22 — **Owner decisions for Phases 3–4:** (1) knockback: whitelist `MotionMaster::MoveKnockbackFrom(..., allowClientControlled=true)` as a protocol reflex on `SMSG_MOVE_KNOCK_BACK` (no client exists to simulate the arc; this is AC's own server-side knockback); (2) damage events come from combat-log SMSGs (`SMSG_SPELLNONMELEEDAMAGELOG`, `SMSG_ATTACKERSTATEUPDATE`, periodic logs), which carry spell id and school, not from `UnitScript::OnDamage`; (3) superseding a cast only drops the old watcher (→ CANCELLED); no `CMSG_CANCEL_CAST` is injected — the controller sends CancelCast if it wants one. Facing uses the whitelisted `Unit::SetFacingTo`/`SetFacingToObject` on the `move` channel (stops movement first), avoiding hand-built MovementInfo.
- 2026-10-22 — **Phase 3 acceptance met.** `ActionService` (MoveTo, Stop, Follow, Face, SetTarget, Attack, StopAttack, CastSpell, CancelCast, Say, Whisper, Cancel, WaitCommand) on per-character channels move/cast/melee/chat with supersede (→ `CANCELLED/SUPERSEDED`), server-side timeouts (silent AC paths → `TIMEOUT/NO_RESPONSE`), and SMSG-driven watchers: casts correlated by echoed cast count (1–255) + caster guid; melee by attacker/victim keys plus single-in-flight for payload-less swing errors; chat by echo keys. `packets/` builders/decoders with golden-byte tests (`acsdk-packet-tests`). Minimal `WorldService` (NearbyObjects, GetSpellInfo) and `GetState` SPELLS section pulled forward from Phase 5 because kill_a_mob and spell-name resolution need them. Python porcelain: `Character.move_to/face/attack/cast(name|id)/say/whisper/nearby`, `CommandHandle`, `helpers.kill()`. `examples/kill_a_mob.py`: warrior killed a Kobold Vermin; Heroic Strike first failed `SPELL_FAILED_NO_POWER`, then succeeded once rage built. Test: a priest's Lesser Heal landing on a mage mid-Fireball did not resolve the mage's cast (Fireball completed on its own `SMSG_SPELL_GO`). 15 tests pass. Spike B DebugService helpers removed (DebugService keeps only test provisioning). Lesson: tests that `.tele` an Alliance character into Orgrimmar get it killed by guards; the harness now revives and returns test characters to Northshire each run.
- 2026-10-22 — **Phase 4 acceptance met.** `EventService.Subscribe` (one callback-API stream per controller, filters by character and type) + `RespondToPrompt`. `EventBus`: events built on the source thread (POD copies only), published into a bounded ingress, dispatched once per world tick to bounded per-subscriber queues (drop-oldest + `Lagged`); the event object is shared by all subscribers; event-source opcodes are copied by the PacketSink only while someone is subscribed. Sources exactly as `docs/events.md` (damage switched to combat-log SMSGs per owner decision). Prompts: group invite, trade, duel, resurrect, summon, loot roll, ready check, with runtime prompt ids and decline-on-timeout (`ACSDK.Prompt.TimeoutSec`). Raw escape hatch `ActionService.SendClientPacket` + RAW_PACKET events behind `ACSDK.RawPackets.Enable` (denylist, optional allowlist, rate limit). `GetState` GROUP section. Tests (20 pass): `examples/chat_echo.py` echoes whispers (also run end to end); a group invite sent by one headless character reaches the other as a Prompt, is accepted via RPC, and both end up grouped; a Say yields exactly one Chat and one CommandResult event, Frost Armor exactly one SpellCastFinish and one AuraApplied; a subscriber that never reads, flooded with 20000 events, overflows only its own queue (`events_dropped` > 0, later `Lagged`) while a second subscriber receives everything and world-thread dispatch stays under 100 ms. Found on the way: the Python client sent the server token only on unary calls (streams were rejected). Not done: periodic/environmental damage logs, `is_refresh` on AuraApplied (the hook cannot distinguish), lease renewal via the event stream (use RenewLease).
- 2026-10-22 — **Phase 5 acceptance met.** `WorldService.FindPath` (PathGenerator over mmaps; smoothed or straight/corner paths; one query reaches ~296 yd, AC's MAX_PATH_LENGTH), `GetHeight`, `LineOfSight` (AC's own `isInLineOfSight`), `GetObject`, `GetItemInfo`, `GetCreatureInfo`. Python: wrappers, `helpers.paths_svg` (dependency-free SVG plot), `helpers.travel()` for long routes (straight path → MoveTo each corner → re-plan from wherever it stops). Tests (24 pass in total): a FindPath result is plotted to SVG; ground height matches where the character stands (±1.5 yd) and path points sit on the reported ground (±4 yd, navmesh detail); LineOfSight equals the game's `IsWithinLOSInMap` for every nearby creature; item 25 = "Worn Shortsword", creature 6 = "Kobold Vermin". Windows note: `<windows.h>` renames `GetObject` → `#undef` in WorldService.cpp.
- 2026-10-22 — **Long pathfinding test (owner request): Elwynn Forest → Westfall.** Examplechar2 raised to level 30 by GM command (so road mobs don't end the run), started in Northshire Valley (-8921.09, -119.14, 82.19, zone 12 Elwynn), destination Sentinel Hill (-10624.5, 1096.66, 33.76), limit 1 h. Arrived in 317.9 s at (-10624.50, 1096.66, 33.76) (zone 40 Westfall); entered Westfall at (-10146.13, 810.67, 21.68) after 232 s; walked 2144.7 yd vs 2092.8 yd straight line, 43 moves, 6 path queries. Route: `docs/westfall_route.svg` / `.json`. A repeat run with the same code arrived identically; an experiment that split long legs at GetHeight points stranded the character on Sentinel Tower's roof 108 yd short and was reverted.
- 2026-10-22 — **Map data:** AzerothCore client-data v20.0 (wowgaming/client-data, 1.1 GB; dbc/maps/vmaps/mmaps) downloaded to `C:\acsdk-build\data` with owner approval, in place of extracting from a client for now.
- 2026-10-22 — **Bug-report fixes (SDK 0.5.0), from consumer testing (bug-reports/).** (1) DebugService.CreateCharacter looked the new character up by the name as sent while AC stores it capitalised, so it reported REJECTED_OR_TIMEOUT for characters it had created; now normalised (`normalizePlayerName`), returns the stored `name`, and failures carry AC's `SMSG_CHAR_CREATE` ResponseCodes name. (2) New *interact* channel in ActionService: ReleaseSpirit, ReclaimCorpse, SpiritHealerResurrect, Talk (GOSSIP_HELLO / QUESTGIVER_HELLO / LIST_INVENTORY by npc flags), SelectGossipOption, AcceptQuest, CompleteQuest (drives REQUEST_REWARD / CHOOSE_REWARD as the client's dialog would), AbandonQuest, OpenVendor, BuyItem, SellItem, EquipItem, Loot (open, take money and allowed slots, release). Window contents come back in `CommandResult.interaction`, decoded from the SMSGs (golden-byte tests). Handlers that refuse silently time out with a reason read from game state (TOO_FAR, NOT_DEAD, RECLAIM_DELAY, QUEST_NOT_OFFERED, ...); game refusals use AC's enum names (BUY_ERR_*, SELL_ERR_*, EQUIP_ERR_*, LOOT_ERROR_*, INVALIDREASON_*), generated by `scripts/gen_enum_names.py`. `GetState` sections CORPSE, EQUIPMENT, INVENTORY, QUESTS. Movement no longer fails DIED for a character that was already dead (ghosts walk). Python: porcelain for all of it, `helpers.recover()` (release → ghost run → reclaim, spirit healer fallback), `helpers.approach()`, `helpers.kill(stop_below_health=20)` (LOW_HEALTH), kill() finds its target by guid (it reported TARGET_GONE beyond 60 yd). Verified live: Examplechar2 died and was recovered at its corpse; Examplechar took and turned in A Threat Within, abandoned/re-took Kobold Camp Cleanup, killed and looted wolves, sold the loot, bought and equipped a Crude Throwing Axe; Eagan's Wolves Across the Border was refused with INVALIDREASON_DONT_HAVE_REQ (prerequisite missing), as in the game.

## 14. Alternatives considered (and why rejected)
- **Python headless client speaking the 3.3.5a protocol:** large protocol effort; no server-side
  pathfinding or omniscient state.
- **mod-eluna (Lua in-process):** keeps scripting inside the server in Lua; doesn't meet the
  external-Python-controller goal. Useful reference for hook coverage.

## 15. Risks
| Risk | Mitigation |
|---|---|
| Socketless sessions need core changes stock AC lacks | Phase 0 R1/R2/Spike B decide early; minimal patch set; consider upstreaming hooks |
| Headless characters get stuck on client-expected acks | §5.2 reflex list built from research; integration tests for teleports, deaths, zone changes |
| Threading bugs crash worldserver | Invariants §4.1; thread-id asserts; start with `MapUpdate.Threads = 1` |
| gRPC/CMake integration pain on Windows (CRT mismatch, duplicate OpenSSL, DLL deployment) | Phase 0 Spike A first; recipe in `docs/windows-build.md` |
| Protocol churn breaks scripts | `v1` package, additive changes only |
| Slow or crashed controllers | Bounded queues, per-tick budgets, lease TTLs |
| Resource bloat with many controllers | §4.5 design defaults; diagnostic counters |
| Replies attributed to the wrong command | Per-opcode correlation strategies (§4.6.5); single-in-flight channels where needed; Phase 3 test with spells cast on the character by others |
| Controller impersonation / lease theft | Unguessable lease tokens on every command (§4.4); Phase 2 security tests |
| Logic creeping into plumbing over time | §4.0 placement rule; reviews reject C++ features that porcelain could build |
| Packet layouts built incorrectly (silent misbehavior) | Formats taken from handler read code; reuse AC serializers; golden-byte tests; log AC's packet-read errors |
| Some handlers assume client-side state a headless character lacks (e.g. movement/facing flags) | Research R3 flags these per opcode; resolve via whitelisted movement APIs or reflexes, not by reimplementing the handler |

## 16. References
- AzerothCore: https://www.azerothcore.org/ · https://github.com/azerothcore/azerothcore-wotlk
- gRPC: https://grpc.io/ · Python asyncio API: `grpc.aio`
