"""
services/matchday_lock_service.py
──────────────────────────────────
Global Round Lock – MVP implementation.

Responsibilities
────────────────
• calculate_matchday_lock  – derive lock_at_utc from fixtures + configurable offset
• initialize_matchday_lock – persist lock_at_utc to the matchday row
• process_matchday_lock    – idempotently flip is_locked when the clock fires

Design notes
────────────
• All times are UTC-aware datetimes.
• Offset is stored in SystemConfig (key=MATCHDAY_LOCK_OFFSET_MINUTES, default=60).
• process_matchday_lock is idempotent: safe to call from a periodic Celery beat task
  or any repeated HTTP trigger.
• No per-player, per-fixture, or rolling locks – MVP scope only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..models import Fixture, Matchday, MatchdayStatus, SystemConfig

_DEFAULT_OFFSET_MINUTES = 60
_CONFIG_KEY = "MATCHDAY_LOCK_OFFSET_MINUTES"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_lock_offset(db: Session) -> int:
    """Return the configured lock offset in minutes (default 60)."""
    row = db.query(SystemConfig).filter(SystemConfig.key == _CONFIG_KEY).first()
    if row:
        try:
            return int(row.value)
        except ValueError:
            pass
    return _DEFAULT_OFFSET_MINUTES


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calculate_matchday_lock(matchday: Matchday, db: Session) -> Optional[datetime]:
    """
    Compute lock_at_utc for *matchday* without persisting it.

    Returns
    -------
    datetime (UTC-aware) or None if the matchday has no fixtures.
    """
    earliest: Optional[datetime] = None
    for fixture in matchday.fixtures:
        kickoff = fixture.kickoff_utc
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        if earliest is None or kickoff < earliest:
            earliest = kickoff

    if earliest is None:
        return None

    offset = _get_lock_offset(db)
    return earliest - timedelta(minutes=offset)


def initialize_matchday_lock(matchday: Matchday, db: Session) -> Optional[datetime]:
    """
    Calculate and persist lock_at_utc.

    Call this once fixtures are known (e.g. when the admin saves fixtures
    for a matchday).  Safe to call again if fixtures change – it just
    recalculates.

    Returns the computed lock_at_utc (or None if no fixtures exist yet).
    """
    lock_at = calculate_matchday_lock(matchday, db)
    matchday.lock_at_utc = lock_at
    db.add(matchday)
    db.commit()
    db.refresh(matchday)
    return lock_at


def process_matchday_lock(matchday: Matchday, db: Session) -> bool:
    """
    Evaluate whether the matchday should now be locked and, if so, lock it.

    Idempotent – safe to call in a tight loop.

    Returns True if the lock was *newly* applied, False otherwise.
    """
    # Guard: nothing to do if already locked or no lock time set
    if matchday.is_locked:
        return False

    if matchday.lock_at_utc is None:
        return False

    now = _now_utc()
    lock_at = matchday.lock_at_utc
    if lock_at.tzinfo is None:
        lock_at = lock_at.replace(tzinfo=timezone.utc)

    if now < lock_at:
        return False

    # Apply the lock
    matchday.is_locked = True
    matchday.locked_at = now
    if matchday.status == MatchdayStatus.scheduled:
        matchday.status = MatchdayStatus.in_progress

    db.add(matchday)
    db.commit()
    db.refresh(matchday)
    return True
