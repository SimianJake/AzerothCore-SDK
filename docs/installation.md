# Installing the AzerothCore SDK and writing your first script

This guide is for someone who has been given the AzerothCore SDK and asked to write code with it. It
assumes no prior knowledge of the project. You need a computer with internet access and permission to
install software. The commands are written for Windows; macOS and Linux versions are given where they
differ.

## What this is

AzerothCore is a World of Warcraft (3.3.5a) game server. The server you will use runs an add-on called
`mod-acsdk`, which lets a program log characters into the game and control them with no game client
open. These are called "headless" characters.

The SDK is the Python package (`azerothcore-sdk`) your program uses to talk to that server. You do not
run or install the game server yourself. Someone else (the server owner) runs it and gives you access.

## Step 1: Get these from the server owner

Ask the server owner for the following before you start. You need all of them.

| Item | Example | What it is for |
|---|---|---|
| The SDK package file | `azerothcore_sdk-1.1.0-py3-none-any.whl` | What you install in Step 3. You may be given a package index address instead (see Step 3). |
| `SDK_GUIDE.md` | | The reference for every call. Keep it next to your code. |
| Server address | `127.0.0.1:50051` or `192.168.1.20:50051` | Where your program connects. |
| Account name and password | `acsdkhcalex` / a password | Your own game account. The SDK logs in with it, and you can see and control only the characters on this account. Keep the password private and never commit it to git or paste it in chat. |
| Character name(s) | `Examplechar` | The character(s) on your account. One of them can be in the game at a time. |
| How to connect | "same PC", "LAN", "Tailscale", "SSH tunnel" | Whether you need anything extra to reach the server (Step 5). |

### For the server owner: creating an account for someone

SDK logins are ordinary AzerothCore game accounts. Give each person their own account; a login can only see and
control that account's characters. In the worldserver console (or in game as a GM, with a leading `.`):

```
account create acsdkhcalex <password>
```

The name must start with `ACSDK.HeadlessAccountPrefix` (default `acsdkhc`) unless you set that option to empty in
`mod_acsdk.conf`. Keep the account player-level (no `account set gmlevel`): GM accounts are refused by default.
Then create a character on it, for example by logging in with a game client once, and send the person the
account name, password and character name. `.acsdk session list` shows who is connected and
`.acsdk session revoke <account>` disconnects someone; banning the account or changing its password also ends its
SDK session within a minute.

## Step 2: Install Python

You need Python 3.11 or newer.

1. Check whether you already have it. Open **PowerShell** (Start menu, type `PowerShell`) and run:
   ```powershell
   py --version
   ```
   If it prints `Python 3.11` or higher, go to Step 3.
2. Otherwise download Python from <https://www.python.org/downloads/> and run the installer. On the first
   screen, tick **"Add python.exe to PATH"**, then click **Install Now**.
3. Close PowerShell, open a new one, and run `py --version` again to confirm.

macOS and Linux: use `python3 --version`. Install with `brew install python@3.12` (macOS) or your
distribution's package manager (for example `sudo apt install python3 python3-venv`). Write `python3`
wherever this guide says `py`.

## Step 3: Create a project folder and install the SDK

1. Make a folder for your code and open PowerShell in it:
   ```powershell
   mkdir C:\my-acsdk-bot
   cd C:\my-acsdk-bot
   ```
2. Create a virtual environment. This is a private copy of Python for this project, so the SDK does not
   interfere with anything else on your computer:
   ```powershell
   py -m venv .venv
   .venv\Scripts\Activate.ps1
   ```
   Your prompt now starts with `(.venv)`. Run `.venv\Scripts\Activate.ps1` again every time you open a
   new PowerShell window to work on this project.
   - If PowerShell says *"running scripts is disabled on this system"*, run
     `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, answer `Y`, and try again.
   - macOS/Linux: `python3 -m venv .venv` then `source .venv/bin/activate`.
3. Install the SDK. Use the line that matches what the server owner gave you:
   ```powershell
   # You were given a .whl file: copy it into this folder, then
   pip install .\azerothcore_sdk-1.1.0-py3-none-any.whl

   # You were given a package index address instead
   pip install --extra-index-url http://<index-address>/simple/ azerothcore-sdk
   ```
   pip also downloads the two libraries the SDK needs (`grpcio` and `protobuf`) from the internet.
   If the index address starts with `http://` and is not `localhost`, add
   `--trusted-host <index-host>` to the command.
4. Check that it worked:
   ```powershell
   python -c "import azerothcore_sdk; print(azerothcore_sdk.__version__)"
   ```
   It should print the version, for example `1.1.0`.

## Step 4: Store the address and your login

Keep your password out of your code by putting it in environment variables. The SDK reads
`ACSDK_USERNAME` and `ACSDK_PASSWORD` by itself. In PowerShell, run these **once**. They are saved for
your user account, and apply to PowerShell windows you open afterwards:

```powershell
setx ACSDK_TARGET "127.0.0.1:50051"
setx ACSDK_USERNAME "<your account name>"
setx ACSDK_PASSWORD "<your account password>"
setx ACSDK_CHARACTER "<your character's name>"
```

Close PowerShell, open a new one, `cd C:\my-acsdk-bot`, and activate the venv again
(`.venv\Scripts\Activate.ps1`).

macOS/Linux: add `export ACSDK_TARGET=...` lines (same four names) to `~/.bashrc` or `~/.zshrc`, then
open a new terminal.

## Step 5: Make sure you can reach the server

What you do depends on what the server owner said in Step 1:

- **Same PC as the server:** nothing to do. The address is `127.0.0.1:50051`.
- **LAN or VPN (for example Tailscale):** join the network if needed, then use the address the owner
  gave you.
- **SSH tunnel:** the owner gives you an SSH login. Keep this running in its own PowerShell window
  while you work:
  ```powershell
  ssh -N -L 50051:127.0.0.1:50051 <user>@<server-host>
  ```
  It looks like nothing happens, which is normal. Your address is then `127.0.0.1:50051`.

Now test the connection. Save this as `check.py` in your folder:

```python
"""Logs in to the server and lists the characters on your account. Changes nothing."""
import asyncio
import os

from azerothcore_sdk import Client


async def main():
    async with Client(os.environ["ACSDK_TARGET"], label="check") as ac:  # logs in as ACSDK_USERNAME
        info = await ac.server.info()
        print("Connected to realm:", info.realm_name, "as", ac.session.username)
        for c in await ac.characters.list():
            print(f"  {c.name}  level {c.level}  online={c.online}")


asyncio.run(main())
```

Run it:

```powershell
python check.py
```

You should see the realm name and your character(s). If not, see [Troubleshooting](#troubleshooting).

## Step 6: Your first script

Save this as `hello.py`. It logs the character in, takes control, prints where it is, says something in
game chat, walks a few yards, and lets go.

```python
"""Log a character in, say hello, take a few steps, and release it."""
import asyncio
import os

from azerothcore_sdk import Client


async def main():
    name = os.environ["ACSDK_CHARACTER"]
    async with Client(os.environ["ACSDK_TARGET"], label="hello") as ac:
        char = await ac.characters.login(name)       # does nothing if it is already logged in
        async with char.lease():                     # take control; released when the block ends
            state = await char.state()
            x, y, z = state.position
            print(f"{state.name} is at {x:.1f}, {y:.1f}, {z:.1f} (level {state.level})")

            r = await char.say("Hello from the SDK!")
            print("say:", r.status, r.reason)

            r = await char.move_to(x + 5, y, z)      # 5 yards along the x axis
            print("move:", r.status, r.reason)


asyncio.run(main())
```

```powershell
python hello.py
```

If the server owner (or anyone) is logged into the game near that character, they will see it speak and
move.

## How the SDK works (read this before writing more)

- **Everything is `async`.** SDK calls are awaited (`await char.say(...)`) inside an `async def`
  function, which you start with `asyncio.run(main())`. If you are new to this, copy the shape of the
  scripts above.
- **One `Client` per program, logged in as your account.** Open it with `async with Client(...) as ac:`.
  All your characters share it. `label` is a name for your program that shows up in server logs, for
  example `"alex-fishing-bot"`. An account has one SDK session at a time: starting a second program with
  the same account logs the first one out (its leases end).
- **You only see your own characters.** Anything about another account's character fails with
  `NotYourCharacter`.
- **Reading is free, acting needs a lease.** You can read your characters' state at any time. To make one
  do something you must hold its **lease**: `async with char.lease():`. Only one lease per character at a
  time; the SDK renews it for you while the block runs.
- **Characters do nothing on their own.** If your program stops, the lease runs out after about 15
  seconds and the character just stands there, still logged in. Call `await char.logout()` when you are
  finished with it.
- **Every action returns a result.** Check `r.ok` (True when it worked). `r.status` is `COMPLETED`,
  `FAILED`, `TIMEOUT` or `CANCELLED`, and `r.reason` says why it failed, for example `NO_PATH`.
  The meaning of each reason is in `SDK_GUIDE.md`, section 4.
- **A new command of the same kind replaces the old one.** Starting a new move while one is running
  cancels the first (reason `SUPERSEDED`). Movement, casting, melee and chat are separate, so you can
  move and chat at the same time.
- **Positions are game coordinates** `(x, y, z)` in yards on the character's current map.

## What you can do next

`SDK_GUIDE.md` section 3 lists every call. The common ones:

| Goal | Call |
|---|---|
| Walk somewhere nearby | `await char.move_to(x, y, z)` |
| Travel a long way (finds a path) | `await helpers.travel(char, (x, y, z))` |
| Find creatures around you | `await char.nearby("creature", radius=30, hostile=True)` |
| Fight | `await helpers.kill(char, mob, spells=["Fireball"])` |
| Talk to an NPC, quests, vendors | `char.talk(npc)`, `accept_quest(...)`, `buy(...)`, `sell(...)` |
| React to chat, damage, invites | `async for ev in char.events(events.Chat): ...` |
| Recover after dying | `await helpers.recover(char)` |

Import the helpers with `from azerothcore_sdk import helpers, events`. Ask the server owner for the
`examples` folder: it has short working scripts (`kill_a_mob.py`, `chat_echo.py`, `travel.py`,
`quest_and_loot.py`) you can copy from.

Your code editor will autocomplete SDK calls and show their parameters. Visual Studio Code with the
Python extension works well. Point it at your venv with "Python: Select Interpreter" and choose `.venv`.

## Troubleshooting

| What you see | What it means and what to do |
|---|---|
| `py` or `python` is not recognized | Python is not installed or not on PATH. Redo Step 2 and tick "Add python.exe to PATH". |
| `No module named azerothcore_sdk` | The venv is not active. Run `.venv\Scripts\Activate.ps1` in your project folder. |
| `KeyError: 'ACSDK_TARGET'` or `ValueError: Client needs username and password` | The environment variables are not set in this window. Redo Step 4 and open a new PowerShell. |
| `ServerUnavailable` | Your program cannot reach the server. Check the address, that the SSH tunnel or VPN is up, and ask the owner whether the server is running. |
| `Unauthenticated` | Wrong account name or password (it is not case-sensitive). Copy them again exactly, with no spaces or quotes around them. |
| `AccountRefused` | The password is right but the account may not use the SDK: `BANNED`, `LOCKED`, `GM_ACCOUNT`, or `ACCOUNT_NOT_ELIGIBLE` (its name does not start with the server's account prefix). Ask the server owner. |
| `ServerBusy: too many failed logins` | Several wrong passwords in a row for that account name; wait the number of seconds it says. Other accounts are not affected. |
| `NotYourCharacter` | That character is on another account. You can only use your own account's characters. |
| `IncompatibleServer` | The server runs an older `mod-acsdk` (before 1.1, which still used a shared token). Ask the owner which SDK version to use. |
| `LoginFailed` | The character could not be logged in; the reason is in the message. `CHARACTER_NOT_FOUND`: check the spelling. `ACCOUNT_ALREADY_ONLINE`: someone is playing a character on your account in the game, or another of your characters is already logged in. |
| `LeaseError: ALREADY_LEASED` | Another part of your program already holds this character's lease. `char.lease(force=True)` takes it over. |
| A command returns `INVALID_LEASE_TOKEN` | The command ran outside the `async with char.lease():` block, or the lease was lost (for example because your session ended: another program logged in with your account, or you were idle for 30 minutes). |
| `pip install` fails downloading `grpcio` | You need internet access to PyPI. On a work network, ask whether a proxy is required. |

If you think you have found a bug in the SDK, tell the server owner what your script did, the `reason` it got
back, and your SDK version (`python -c "import azerothcore_sdk; print(azerothcore_sdk.__version__)"`).

## Updating the SDK

When the server owner sends a new version, activate your venv and install the new file the same way:

```powershell
pip install --upgrade .\azerothcore_sdk-<new-version>-py3-none-any.whl
# or, from a package index:
pip install --upgrade --extra-index-url http://<index-address>/simple/ azerothcore-sdk
```

Use the SDK version the owner gives you. A newer SDK can have calls the server does not support yet.
