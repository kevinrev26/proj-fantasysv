"""
services/lock_guard.py
───────────────────────
Centralised guard helpers for write operations that must be blocked
while a matchday is locked.

Usage (in any write endpoint)
──────────────────────────────
    from ..services.lock_guard import assert_matchday_unlocked

    matchday = get_current_matchday(db)
    assert_matchday_unlocked(matchday)   # raises 409 if locked
    # … proceed with transfer / squad change / etc.
"""

from __future__ import annotations

from fastapi import HTTPException

from ..models import Matchday


def assert_matchday_unlocked(matchday: Matchday | None) -> None:
    """
    Raise HTTP 409 if *matchday* is currently locked.

    Parameters
    ----------
    matchday:
        The active/upcoming Matchday ORM object, or None.
        If None the call is a no-op (no matchday → no lock to enforce).

    Raises
    ------
    HTTPException(409) when matchday.is_locked is True.
    """
    if matchday is None:
        return

    if matchday.is_locked:
        raise HTTPException(
            status_code=409,
            detail=(
                "Matchday is locked. "
                "No transfers, formation changes, captain/joker or bench changes "
                "are allowed until the matchday is closed."
            ),
        )
