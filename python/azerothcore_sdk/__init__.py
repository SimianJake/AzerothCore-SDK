"""Async Python SDK for driving headless AzerothCore characters (see PLAN.md)."""

from azerothcore_sdk import events
from azerothcore_sdk.character import Character, LeaseError, LeaseLost
from azerothcore_sdk.client import Client, LoginFailed
from azerothcore_sdk.plumbing import CharacterState, Lifecycle, StateSection
from azerothcore_sdk.plumbing.errors import (
    AccountRefused,
    AcsdkError,
    IncompatibleServer,
    NotYourCharacter,
    ServerBusy,
    ServerUnavailable,
    Timeout,
    Unauthenticated,
)

__version__ = "1.1.0"

__all__ = [
    "AccountRefused",
    "AcsdkError",
    "Character",
    "CharacterState",
    "Client",
    "IncompatibleServer",
    "LeaseError",
    "LeaseLost",
    "Lifecycle",
    "LoginFailed",
    "NotYourCharacter",
    "ServerBusy",
    "ServerUnavailable",
    "StateSection",
    "Timeout",
    "Unauthenticated",
    "events",
]
