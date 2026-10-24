"""Porcelain spell-name resolution (decision D3): names -> ids from cached GetSpellInfo, client side."""

from __future__ import annotations

from typing import TYPE_CHECKING

from azerothcore_sdk.plumbing import SpellInfo, StateSection

if TYPE_CHECKING:
    from azerothcore_sdk.character import Character
    from azerothcore_sdk.client import Client


class SpellNotKnown(LookupError):
    pass


class SpellCache:
    """Static spell data, fetched once per id and shared by every character of a Client (PLAN.md §4.5)."""

    def __init__(self, client: Client) -> None:
        self._client = client
        self._info: dict[int, SpellInfo] = {}

    async def get(self, spell_ids: list[int]) -> dict[int, SpellInfo]:
        missing = [i for i in spell_ids if i not in self._info]
        if missing:
            for info in await self._client._plumbing_world.spell_info(missing):
                self._info[info.id] = info
        return {i: self._info[i] for i in spell_ids if i in self._info}

    async def resolve(self, character: Character, name: str) -> SpellInfo:
        """The character's highest-rank known, non-passive spell called `name` (case-insensitive)."""
        state = await character.state(StateSection.SPELLS)
        infos = await self.get(list(state.known_spells))
        wanted = name.casefold()
        matches = [s for s in infos.values() if s.name.casefold() == wanted and not s.passive]
        if not matches:
            raise SpellNotKnown(f"{character.name} does not know a spell called {name!r}")

        def rank_number(s: SpellInfo) -> int:
            digits = "".join(ch for ch in s.rank if ch.isdigit())
            return int(digits) if digits else 0

        return max(matches, key=lambda s: (rank_number(s), s.id))
