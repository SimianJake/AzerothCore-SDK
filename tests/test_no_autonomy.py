"""PLAN.md §10 no-autonomy test: log characters in, claim none, and check nothing about them changes.

Slow (default 30 minutes). Runs only with ACSDK_SLOW=1; ACSDK_NO_AUTONOMY_MINUTES overrides the duration.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest
from conftest import CHARACTERS

from azerothcore_sdk import CharacterState

pytestmark = pytest.mark.skipif(os.environ.get("ACSDK_SLOW") != "1", reason="slow; set ACSDK_SLOW=1")

MINUTES = float(os.environ.get("ACSDK_NO_AUTONOMY_MINUTES", "30"))


def fingerprint(s: CharacterState) -> tuple[object, ...]:
    """Everything a controller could have changed. Vitals are compared too: nothing should touch them."""
    return (s.in_world, s.map_id, s.position, s.orientation, s.moving, s.target_guid, s.in_combat, s.dead,
            s.health, s.power_type, s.money_copper, s.lease_holder)


async def test_unclaimed_characters_never_act(make_client) -> None:  # type: ignore[no-untyped-def]
    # One client per account: a session sees only its own account's characters.
    chars = [await (await make_client(f"no-autonomy-observer-{i}", i)).characters.login(name) for i, name in enumerate(CHARACTERS)]
    await asyncio.sleep(5)  # let login settle
    baseline = {c.name: fingerprint(await c.state()) for c in chars}

    deadline = time.monotonic() + MINUTES * 60
    samples = 0
    while time.monotonic() < deadline:
        await asyncio.sleep(30)
        for c in chars:
            now = fingerprint(await c.state())
            assert now == baseline[c.name], f"{c.name} changed without a controller: {baseline[c.name]} -> {now}"
        samples += 1
        print(f"[{time.strftime('%H:%M:%S')}] sample {samples}: unchanged")

    assert samples >= int(MINUTES * 2) - 1
