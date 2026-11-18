"""Decides whether a Plex account may sign in to this instance."""

from __future__ import annotations

from dataclasses import dataclass

from plekstender.config import AccessMode
from plekstender.plex.tv import PlexAccount


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    make_admin: bool = False
    reason: str = ""


def identity_matches(account: PlexAccount, candidates: list[str] | set[str]) -> bool:
    wanted = {c.strip().lower() for c in candidates if c.strip()}
    names = {account.username.lower()}
    if account.email:
        names.add(account.email.lower())
    return bool(wanted & names)


def decide_bootstrap(account: PlexAccount, initial_admin: str | None) -> AccessDecision:
    """No users exist yet: the first account becomes admin (optionally pinned by env)."""
    if initial_admin and not identity_matches(account, [initial_admin]):
        return AccessDecision(False, reason="This instance is waiting for its admin to sign in.")
    return AccessDecision(True, make_admin=True)


def decide_access(
    account: PlexAccount,
    *,
    is_admin: bool,
    mode: AccessMode,
    allowlist: list[str],
    approved_server_ids: set[str],
    accessible_server_ids: set[str] | None,
) -> AccessDecision:
    """Access check for an instance that already has users.

    `accessible_server_ids` are the machine identifiers the account can reach on plex.tv;
    only needed (and only fetched by the caller) in SERVERS mode.
    """
    if is_admin:
        return AccessDecision(True)
    match mode:
        case AccessMode.OPEN:
            return AccessDecision(True)
        case AccessMode.ALLOWLIST:
            if identity_matches(account, allowlist):
                return AccessDecision(True)
            return AccessDecision(False, reason="Your Plex account isn't on this instance's list.")
        case AccessMode.SERVERS:
            if accessible_server_ids and accessible_server_ids & approved_server_ids:
                return AccessDecision(True)
            return AccessDecision(
                False, reason="Your Plex account doesn't have access to an approved server."
            )
