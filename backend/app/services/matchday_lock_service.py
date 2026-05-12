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

import structlog
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..models import Fixture, Matchday, MatchdayStatus, SystemConfig

logger = structlog.get_logger()

_DEFAULT_OFFSET_MINUTES = 60
_CONFIG_KEY = "MATCHDAY_LOCK_OFFSET_MINUTES"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_lock_offset(db: Session) -> int:
    """Return the configured lock offset in minutes (default 60)."""
    logger.debug("Retrieving lock offset from database")
    row = db.query(SystemConfig).filter(SystemConfig.key == _CONFIG_KEY).first()
    if row:
        try:
            offset = int(row.value)
            logger.debug("Lock offset retrieved from config", offset=offset)
            return offset
        except ValueError:
            logger.warning("Invalid lock offset value in config, using default", value=row.value)
            pass
    logger.debug("Using default lock offset", offset=_DEFAULT_OFFSET_MINUTES)
    return _DEFAULT_OFFSET_MINUTES


def _now_utc() -> datetime:
    now = datetime.now(timezone.utc)
    logger.debug("Current UTC time", time=now)
    return now


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
    logger.debug("Calculating matchday lock time", matchday_id=matchday.id)
    earliest: Optional[datetime] = None
    for fixture in matchday.fixtures:
        kickoff = fixture.kickoff_utc
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        if earliest is None or kickoff < earliest:
            earliest = kickoff

    if earliest is None:
        logger.warning("Matchday has no fixtures, no lock time calculated", matchday_id=matchday.id)
        return None

    offset = _get_lock_offset(db)
    lock_at = earliest - timedelta(minutes=offset)
    logger.debug("Matchday lock time calculated", lock_at=lock_at, offset=offset)
    return lock_at


def initialize_matchday_lock(matchday: Matchday, db: Session) -> Optional[datetime]:
    """
    Calculate and persist lock_at_utc.

    Call this once fixtures are known (e.g. when the admin saves fixtures
    for a matchday).  Safe to call again if fixtures change – it just
    recalculates.

    Returns the computed lock_at_utc (or None if no fixtures exist yet).
    """
    logger.info("Initializing matchday lock", matchday_id=matchday.id)
    lock_at = calculate_matchday_lock(matchday, db)
    matchday.lock_at_utc = lock_at
    db.add(matchday)
    try:
        db.commit()
        db.refresh(matchday)
        logger.info("Matchday lock initialized successfully", matchday_id=matchday.id, lock_at=lock_at)
        return lock_at
    except Exception as e:
        logger.error("Failed to initialize matchday lock", matchday_id=matchday.id, error=str(e))
        db.rollback()
        raise


def process_matchday_lock(matchday: Matchday, db: Session) -> bool:
    """
    Evaluate whether the matchday should now be locked and, if so, lock it.

    Idempotent – safe to call in a tight loop.

    Returns True if the lock was *newly* applied, False otherwise.
    """
    logger.debug("Processing matchday lock", matchday_id=matchday.id)
    # Guard: nothing to do if already locked or no lock time set
    if matchday.is_locked:
        logger.debug("Matchday already locked", matchday_id=matchday.id)
        return False

    if matchday.lock_at_utc is None:
        logger.debug("No lock time set for matchday", matchday_id=matchday.id)
        return False

    now = _now_utc()
    lock_at = matchday.lock_at_utc
    if lock_at.tzinfo is None:
        lock_at = lock_at.replace(tzinfo=timezone.utc)

    if now < lock_at:
        logger.debug("Matchday lock time not reached yet", 
                     matchday_id=matchday.id, 
                     now=now, 
                     lock_at=lock_at)
        return False

    # Apply the lock
    matchday.is_locked = True
    matchday.locked_at = now
    if matchday.status == MatchdayStatus.scheduled:
        matchday.status = MatchdayStatus.in_progress

    db.add(matchday)
    try:
        db.commit()
        db.refresh(matchday)
        logger.info("Matchday locked successfully", 
                   matchday_id=matchday.id, 
                   locked_at=now, 
                   status=matchday.status)
        return True
    except Exception as e:
        logger.error("Failed to lock matchday", matchday_id=matchday.id, error=str(e))
        db.rollback()
        raise
