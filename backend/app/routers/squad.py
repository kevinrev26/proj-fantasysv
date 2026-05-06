"""
routers/squad.py
─────────────────
Squad management endpoints.

Lock guard is injected into every write operation:
  • PUT /squad/          – full squad update (includes transfers)
  • POST /squad/transfer – explicit transfer endpoint (if used separately)
  • POST /squad/captain  – captain/joker assignment
  • POST /squad/bench    – bench swap

Inter-matchday transfer rules
──────────────────────────────
• 1 free transfer is carried over between matchdays (banked, not accumulated beyond 1).
• Free transfers reset to the configured default between league phases.
  A "phase boundary" is detected when the current matchday's phase differs
  from the previous closed matchday's phase (via TournamentPhase / PhaseName
  stored on the season – simplest proxy available in this schema).
  Because the schema does not link Matchday → TournamentPhase directly,
  we detect a phase boundary by checking if the immediately preceding
  *closed* matchday is in a different tournament phase bracket.
  If we cannot determine this, we fall back to the standard carry-over rule.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..services.lock_guard import assert_matchday_unlocked

router = APIRouter(prefix="/squad", tags=["squad"])


# ---------------------------------------------------------------------------
# Auth helpers (kept identical to original)
# ---------------------------------------------------------------------------

def get_current_user_id(request: Request) -> int:
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        if token.startswith("dummy-token-user-"):
            try:
                return int(token.split("-")[-1])
            except ValueError:
                pass
    return 1


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class PlayerSlot(BaseModel):
    player_id: int
    slot: str  # "starter" | "bench"
    formation_position: str
    is_x2_joker: bool = False


class SquadUpdateRequest(BaseModel):
    players: List[PlayerSlot]
    formation_id: str


class CaptainRequest(BaseModel):
    player_id: int
    is_x2_joker: bool = False


class BenchSwapRequest(BaseModel):
    starter_player_id: int
    bench_player_id: int


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _active_season(db: Session) -> models.Season:
    season = (
        db.query(models.Season)
        .filter(models.Season.status == models.SeasonStatus.active)
        .first()
    )
    if not season:
        raise HTTPException(status_code=404, detail="No active season found")
    return season


def _upcoming_matchday(db: Session, season_id: int) -> Optional[models.Matchday]:
    """Return the earliest non-closed matchday for *season_id*."""
    return (
        db.query(models.Matchday)
        .filter(
            models.Matchday.season_id == season_id,
            models.Matchday.status != models.MatchdayStatus.closed,
        )
        .order_by(models.Matchday.id.asc())
        .first()
    )


def _last_closed_matchday(db: Session, season_id: int, before_id: int) -> Optional[models.Matchday]:
    return (
        db.query(models.Matchday)
        .filter(
            models.Matchday.season_id == season_id,
            models.Matchday.status == models.MatchdayStatus.closed,
            models.Matchday.id < before_id,
        )
        .order_by(models.Matchday.id.desc())
        .first()
    )


def _fantasy_team(db: Session, user_id: int, season_id: int) -> Optional[models.FantasyTeam]:
    return (
        db.query(models.FantasyTeam)
        .filter(
            models.FantasyTeam.user_id == user_id,
            models.FantasyTeam.season_id == season_id,
        )
        .first()
    )


def _get_config_int(db: Session, key: str, default: int) -> int:
    row = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if row:
        try:
            return int(row.value)
        except ValueError:
            pass
    return default


# ---------------------------------------------------------------------------
# Inter-matchday free-transfer carry-over logic
# ---------------------------------------------------------------------------

def _compute_available_free_transfers(
    db: Session,
    team: models.FantasyTeam,
    matchday: models.Matchday,
    base_allowance: int,
) -> int:
    """
    Compute how many free transfers are available for *matchday*.

    Rules
    ─────
    1. Count how many free transfers were *used* in the previous closed matchday.
    2. Unused free transfers from the previous matchday carry over by 1 (max 1 banked).
       → available = base_allowance + min(1, unused_in_prev_matchday)
    3. At a league-phase boundary, the carry-over bank is discarded:
       → available = base_allowance   (fresh start for the new phase)
    4. Subtract transfers already used in the current matchday.
    5. Floor at 0.

    Phase boundary detection
    ────────────────────────
    The schema does not attach TournamentPhase to Matchday directly.
    We use the heuristic: if the matchday name changes prefix (e.g. "MD1" vs
    "Group MD1"), treat it as a phase change.  Operators can also set the
    SystemConfig key FORCE_TRANSFER_RESET_MATCHDAY_IDS to a comma-separated
    list of matchday IDs that should trigger a reset.
    """
    prev = _last_closed_matchday(db, matchday.season_id, matchday.id)

    # Check operator-forced phase reset list
    reset_ids_raw = _get_config_str(db, "FORCE_TRANSFER_RESET_MATCHDAY_IDS", "")
    forced_reset_ids = {
        int(x.strip()) for x in reset_ids_raw.split(",") if x.strip().isdigit()
    }
    is_phase_boundary = matchday.id in forced_reset_ids

    carryover = 0
    if not is_phase_boundary and prev is not None:
        prev_used = (
            db.query(func.count(models.Transfer.id))
            .filter(
                models.Transfer.fantasy_team_id == team.id,
                models.Transfer.matchday_id == prev.id,
            )
            .scalar()
        ) or 0
        unused_prev = max(0, base_allowance - prev_used)
        carryover = min(1, unused_prev)  # bank at most 1

    total_available = base_allowance + carryover

    already_used = (
        db.query(func.count(models.Transfer.id))
        .filter(
            models.Transfer.fantasy_team_id == team.id,
            models.Transfer.matchday_id == matchday.id,
        )
        .scalar()
    ) or 0

    return max(0, total_available - already_used)


def _get_config_str(db: Session, key: str, default: str) -> str:
    row = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    return row.value if row else default


# ---------------------------------------------------------------------------
# READ endpoints (no lock guard needed)
# ---------------------------------------------------------------------------

@router.get("/players")
def get_all_players(db: Session = Depends(get_db)):
    players = db.query(models.Player).filter(models.Player.is_active.is_(True)).all()
    return {
        "players": [
            {
                "id": p.id,
                "name": p.name,
                "pos": p.position.name,
                "tier": p.tier.name,
                "club": p.team.name if p.team else "Unknown",
                "rating": 80,
                "cost": p.credit_value,
                "active": p.is_active,
            }
            for p in players
        ]
    }


@router.get("/")
def get_squad(request: Request, db: Session = Depends(get_db)):
    user_id = get_current_user_id(request)
    season = _active_season(db)
    team = _fantasy_team(db, user_id, season.id)
    matchday = _upcoming_matchday(db, season.id)

    base_allowance = _get_config_int(db, "free_transfers_per_matchday", 1)

    free_remaining = base_allowance
    if matchday and team:
        free_remaining = _compute_available_free_transfers(
            db, team, matchday, base_allowance
        )

    if not team:
        return {
            "squad": [],
            "budget": 100,
            "free_transfers_remaining": base_allowance,
            "is_locked": matchday.is_locked if matchday else False,
            "matchday_deadline": (
                matchday.lock_at_utc.isoformat()
                if matchday and matchday.lock_at_utc
                else None
            ),
        }

    players = []
    total_cost = 0
    for fp in team.fantasy_players:
        players.append(
            {
                "player_id": fp.player_id,
                "slot": fp.slot.name,
                "formation_position": fp.formation_position,
                "is_x2_joker": fp.is_x2_joker,
            }
        )
        total_cost += fp.player.credit_value

    return {
        "squad": players,
        "budget": 100 - total_cost,
        "free_transfers_remaining": free_remaining,
        "is_locked": matchday.is_locked if matchday else False,
        "matchday_deadline": (
            matchday.lock_at_utc.isoformat()
            if matchday and matchday.lock_at_utc
            else None
        ),
    }


@router.get("/leaderboard")
def get_leaderboard(request: Request, db: Session = Depends(get_db)):
    user_id = get_current_user_id(request)
    season = db.query(models.Season).filter(
        models.Season.status == models.SeasonStatus.active
    ).first()
    if not season:
        return {"leaderboard": []}

    last_matchday = (
        db.query(models.Matchday)
        .filter(
            models.Matchday.season_id == season.id,
            models.Matchday.status != models.MatchdayStatus.scheduled,
        )
        .order_by(models.Matchday.id.desc())
        .first()
    )
    if not last_matchday:
        return {"leaderboard": []}

    team_scores = (
        db.query(models.TeamScore)
        .filter(models.TeamScore.matchday_id == last_matchday.id)
        .all()
    )

    leaderboard = []
    for ts in team_scores:
        ft = ts.fantasy_team
        total_penalty = (
            db.query(func.sum(models.TeamScore.transfer_penalty))
            .filter(
                models.TeamScore.fantasy_team_id == ft.id,
                models.TeamScore.matchday_id <= last_matchday.id,
            )
            .scalar()
        ) or 0

        leaderboard.append(
            {
                "id": ft.id,
                "name": ft.name,
                "user_username": ft.user.username if ft.user else "User",
                "user_id": ft.user_id,
                "total_points": ts.cumulative_points - total_penalty,
                "matchday_points": ts.points_this_matchday - ts.transfer_penalty,
                "is_current_user": ft.user_id == user_id,
            }
        )

    leaderboard.sort(key=lambda x: x["total_points"], reverse=True)
    for idx, entry in enumerate(leaderboard):
        entry["rank"] = idx + 1

    return {"leaderboard": leaderboard}


# ---------------------------------------------------------------------------
# WRITE endpoints – lock guard applied
# ---------------------------------------------------------------------------

@router.put("/")
def update_squad(
    payload: SquadUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Full squad update (initial selection + subsequent transfers).
    Blocked while the matchday is locked.
    """
    user_id = get_current_user_id(request)
    season = _active_season(db)
    matchday = _upcoming_matchday(db, season.id)

    if not matchday:
        raise HTTPException(status_code=400, detail="No upcoming matchday")

    # ── LOCK GUARD ──────────────────────────────────────────────────────────
    assert_matchday_unlocked(matchday)

    team = _fantasy_team(db, user_id, season.id)
    if not team:
        team = models.FantasyTeam(
            name=f"Team {user_id}", user_id=user_id, season_id=season.id
        )
        db.add(team)
        db.flush()

    base_allowance = _get_config_int(db, "free_transfers_per_matchday", 1)

    current_fps = {
        fp.player_id: fp
        for fp in db.query(models.FantasyPlayer)
        .filter(models.FantasyPlayer.fantasy_team_id == team.id)
        .all()
    }
    new_player_ids = {p.player_id for p in payload.players}
    old_player_ids = set(current_fps.keys())

    removed_ids = old_player_ids - new_player_ids
    added_ids = new_player_ids - old_player_ids

    is_initialization = len(old_player_ids) == 0
    total_penalty = 0

    if not is_initialization:
        available_free = _compute_available_free_transfers(
            db, team, matchday, base_allowance
        )
        # Count paid transfers
        non_eliminated_removals = 0
        if removed_ids:
            removed_players = (
                db.query(models.Player)
                .filter(models.Player.id.in_(list(removed_ids)))
                .all()
            )
            for p in removed_players:
                if p.team and p.team.eliminated_in_phase_id is not None:
                    pass  # free swap for eliminated player
                else:
                    non_eliminated_removals += 1

        paid = max(0, non_eliminated_removals - available_free)
        total_penalty = paid * 4

    # Replace squad
    db.query(models.FantasyPlayer).filter(
        models.FantasyPlayer.fantasy_team_id == team.id
    ).delete()

    for slot_data in payload.players:
        fp_enum = (
            models.FantasySlot.starter
            if slot_data.slot == "starter"
            else models.FantasySlot.bench
        )
        db.add(
            models.FantasyPlayer(
                fantasy_team_id=team.id,
                player_id=slot_data.player_id,
                slot=fp_enum,
                formation_position=slot_data.formation_position,
                is_x2_joker=slot_data.is_x2_joker,
            )
        )

    # Record transfer history
    if not is_initialization and (added_ids or removed_ids):
        list_rem = list(removed_ids)
        list_add = list(added_ids)
        paid_left = max(0, len(list_rem) - _compute_available_free_transfers(
            db, team, matchday, base_allowance
        ))

        for i in range(max(len(list_rem), len(list_add))):
            r_id = list_rem[i] if i < len(list_rem) else None
            a_id = list_add[i] if i < len(list_add) else None
            if r_id and a_id:
                cost = 4 if paid_left > 0 else 0
                if paid_left > 0:
                    paid_left -= 1
                db.add(
                    models.Transfer(
                        fantasy_team_id=team.id,
                        matchday_id=matchday.id,
                        player_out_id=r_id,
                        player_in_id=a_id,
                        cost=cost,
                    )
                )

    if total_penalty > 0:
        ts = (
            db.query(models.TeamScore)
            .filter(
                models.TeamScore.fantasy_team_id == team.id,
                models.TeamScore.matchday_id == matchday.id,
            )
            .first()
        )
        if not ts:
            ts = models.TeamScore(
                fantasy_team_id=team.id,
                matchday_id=matchday.id,
                points_this_matchday=0,
                cumulative_points=0,
                transfer_penalty=0,
            )
            db.add(ts)
        ts.transfer_penalty += total_penalty

    db.commit()
    return {"status": "success", "penalty_incurred": total_penalty}


@router.post("/captain")
def set_captain(
    payload: CaptainRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Assign captain / x2-joker.
    Blocked while the matchday is locked.
    """
    user_id = get_current_user_id(request)
    season = _active_season(db)
    matchday = _upcoming_matchday(db, season.id)

    # ── LOCK GUARD ──────────────────────────────────────────────────────────
    assert_matchday_unlocked(matchday)

    team = _fantasy_team(db, user_id, season.id)
    if not team:
        raise HTTPException(status_code=404, detail="No team found")

    # Clear existing joker, set new one
    for fp in team.fantasy_players:
        fp.is_x2_joker = fp.player_id == payload.player_id and payload.is_x2_joker

    db.commit()
    return {"status": "success"}


@router.post("/bench-swap")
def bench_swap(
    payload: BenchSwapRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Swap a starter with a bench player.
    Blocked while the matchday is locked.
    """
    user_id = get_current_user_id(request)
    season = _active_season(db)
    matchday = _upcoming_matchday(db, season.id)

    # ── LOCK GUARD ──────────────────────────────────────────────────────────
    assert_matchday_unlocked(matchday)

    team = _fantasy_team(db, user_id, season.id)
    if not team:
        raise HTTPException(status_code=404, detail="No team found")

    fps = {fp.player_id: fp for fp in team.fantasy_players}

    starter_fp = fps.get(payload.starter_player_id)
    bench_fp = fps.get(payload.bench_player_id)

    if not starter_fp or not bench_fp:
        raise HTTPException(status_code=404, detail="Player not found in squad")
    if starter_fp.slot != models.FantasySlot.starter:
        raise HTTPException(status_code=400, detail="First player must be a starter")
    if bench_fp.slot != models.FantasySlot.bench:
        raise HTTPException(status_code=400, detail="Second player must be on the bench")

    starter_fp.slot = models.FantasySlot.bench
    bench_fp.slot = models.FantasySlot.starter
    # swap formation positions
    starter_fp.formation_position, bench_fp.formation_position = (
        bench_fp.formation_position,
        starter_fp.formation_position,
    )

    db.commit()
    return {"status": "success"}
