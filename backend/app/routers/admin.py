from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Header
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models, schemas
from ..worker import recalculate_matchday_scores_task, deactivate_players_for_teams_task, reactivate_players_for_teams_task
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

def verify_admin(authorization: str = Header(...)):
    if authorization not in ("Bearer dummy-admin-token", "dummy-admin-token"):
        raise HTTPException(status_code=401, detail="Unauthorized")

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(verify_admin)])

class MatchdayUpdateRequest(BaseModel):
    deadline_utc: Optional[datetime] = None
    status: Optional[str] = None

@router.patch("/matchday/{matchday_id}", status_code=200)
def update_matchday(
    matchday_id: int,
    payload: MatchdayUpdateRequest,
    db: Session = Depends(get_db)
):
    matchday = db.query(models.Matchday).filter(models.Matchday.id == matchday_id).first()
    if not matchday:
        raise HTTPException(status_code=404, detail="Matchday not found")
        
    if payload.deadline_utc is not None:
        matchday.deadline_utc = payload.deadline_utc
    if payload.status is not None:
        try:
            matchday.status = models.MatchdayStatus(payload.status)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid status")
            
    db.commit()
    return {"status": "success", "matchday_id": matchday.id}

class PlayerStatsUpdate(BaseModel):
    player_id: int
    minutes_played: int
    goals: int
    assists: int
    goals_conceded: int
    yellow_card: int
    red_card: int
    own_goal: int
    penalty_missed: int
    penalty_saved: int

class MatchdayResultsRequest(BaseModel):
    stats: List[PlayerStatsUpdate]

@router.post("/matchday/{matchday_id}/results", status_code=202)
def submit_match_results(
    matchday_id: int,
    payload: MatchdayResultsRequest,
    db: Session = Depends(get_db)
):
    # Verify matchday exists
    matchday = db.query(models.Matchday).filter(models.Matchday.id == matchday_id).first()
    if not matchday:
        raise HTTPException(status_code=404, detail="Matchday not found")
    
    # Update or create PlayerScore for each player
    for stat in payload.stats:
        player_score = db.query(models.PlayerScore).filter(
            models.PlayerScore.player_id == stat.player_id,
            models.PlayerScore.matchday_id == matchday_id
        ).first()
        if not player_score:
            player_score = models.PlayerScore(player_id=stat.player_id, matchday_id=matchday_id)
        player_score.minutes_played = stat.minutes_played
        player_score.goals = stat.goals
        player_score.assists = stat.assists
        player_score.goals_conceded = stat.goals_conceded
        player_score.yellow_card = stat.yellow_card
        player_score.red_card = stat.red_card
        player_score.own_goal = stat.own_goal
        player_score.penalty_missed = stat.penalty_missed
        player_score.penalty_saved = stat.penalty_saved
        # Note: base_points, bonus_points, final_points will be recomputed by task
        db.add(player_score)
    
    db.commit()
    
    # Enqueue Celery task
    task = recalculate_matchday_scores_task.delay(matchday_id)
    
    # Store task info in matchday
    matchday.task_id = task.id
    matchday.task_status = "pending"
    db.add(matchday)
    db.commit()
    
    return {"task_id": task.id, "status": "accepted"}

@router.get("/matchday/{matchday_id}/players")
def get_matchday_players(matchday_id: int, league_id: Optional[int] = None, db: Session = Depends(get_db)):
    # Fetch matchday and its season
    matchday = db.query(models.Matchday).filter(models.Matchday.id == matchday_id).first()
    if not matchday:
        raise HTTPException(404, "Matchday not found")
    
    # Get players
    query = db.query(models.Player).join(models.Team).filter(
        models.Player.is_active == True
    )
    if league_id is not None:
        query = query.filter(models.Team.league_id == league_id)
        
    players = query.all()
    
    # Group by team
    teams_dict = {}
    for p in players:
        if p.team_id not in teams_dict:
            teams_dict[p.team_id] = {
                "team_id": p.team_id,
                "team_name": p.team.name,
                "players": []
            }
        teams_dict[p.team_id]["players"].append({
            "player_id": p.id,
            "name": p.name,
            "position": p.position.value
        })
    return {"matchday_id": matchday_id, "teams": list(teams_dict.values())}

@router.get("/matchday/{matchday_id}/task-status")
def get_task_status(matchday_id: int, db: Session = Depends(get_db)):
    matchday = db.query(models.Matchday).filter(models.Matchday.id == matchday_id).first()
    if not matchday:
        raise HTTPException(status_code=404)
    return {
        "task_id": matchday.task_id,
        "status": matchday.task_status
    }

class EliminationRequest(BaseModel):
    team_ids: List[int]

@router.post("/phase/{phase_id}/eliminate", status_code=202)
def eliminate_teams(
    phase_id: int,
    payload: EliminationRequest,
    db: Session = Depends(get_db),
    # TODO: add admin role dependency
):
    # Verify phase exists
    phase = db.query(models.TournamentPhase).filter(models.TournamentPhase.id == phase_id).first()
    if not phase:
        raise HTTPException(404, "Phase not found")
    
    # Verify all teams exist and are not already eliminated in a higher phase? (optional)
    teams = db.query(models.Team).filter(models.Team.id.in_(payload.team_ids)).all()
    if len(teams) != len(payload.team_ids):
        raise HTTPException(400, "One or more team IDs invalid")
    
    # Mark teams as eliminated in this phase (atomic)
    for team in teams:
        team.eliminated_in_phase_id = phase_id
    db.commit()
    
    # Enqueue Celery task to deactivate players of these teams
    task = deactivate_players_for_teams_task.delay(payload.team_ids)
    
    return {"task_id": task.id, "status": "accepted", "teams_eliminated": payload.team_ids}

@router.post("/phase/{phase_id}/restore", status_code=202)
def restore_teams(
    phase_id: int,
    payload: EliminationRequest,
    db: Session = Depends(get_db),
):
    # Optional: ensure the teams were eliminated in this phase
    teams = db.query(models.Team).filter(
        models.Team.id.in_(payload.team_ids),
        models.Team.eliminated_in_phase_id == phase_id
    ).all()
    if len(teams) != len(payload.team_ids):
        raise HTTPException(400, "Some teams are not eliminated in this phase")
    
    # Clear elimination flag
    for team in teams:
        team.eliminated_in_phase_id = None
    db.commit()
    
    # Re-activate players of these teams
    task = reactivate_players_for_teams_task.delay(payload.team_ids)
    
    return {"task_id": task.id, "status": "accepted", "teams_restored": payload.team_ids}

@router.get("/seasons")
def get_seasons(db: Session = Depends(get_db)):
    seasons = db.query(models.Season).all()
    return {"seasons": seasons}

@router.get("/seasons/{season_id}/leagues")
def get_leagues(season_id: int, db: Session = Depends(get_db)):
    leagues = db.query(models.League).filter(models.League.season_id == season_id).all()
    return {"leagues": leagues}

@router.get("/seasons/{season_id}/matchdays")
def get_matchdays(season_id: int, db: Session = Depends(get_db)):
    matchdays = db.query(models.Matchday).filter(models.Matchday.season_id == season_id).all()
    return {"matchdays": matchdays}

@router.post("/seasons/{season_id}/phases", status_code=201)
def create_tournament_phase(season_id: int, payload: schemas.TournamentPhaseCreate, db: Session = Depends(get_db)):
    season = db.query(models.Season).filter(models.Season.id == season_id).first()
    if not season:
        raise HTTPException(status_code=404, detail="Season not found")

    try:
        phase_name = models.PhaseName(payload.name)
    except ValueError:
        valid = [e.value for e in models.PhaseName]
        raise HTTPException(status_code=400, detail=f"Invalid phase name. Valid values: {valid}")

    phase = models.TournamentPhase(
        name=phase_name,
        season_id=season_id
    )
    db.add(phase)
    db.commit()
    db.refresh(phase)
    return {"id": phase.id, "name": phase.name.value, "season_id": phase.season_id}

@router.post("/seasons", status_code=201)
def create_season(payload: schemas.SeasonCreate, db: Session = Depends(get_db)):
    season = models.Season(
        name=payload.name,
        start_date=payload.start_date,
        end_date=payload.end_date
    )
    db.add(season)
    db.commit()
    db.refresh(season)
    return season

@router.post("/seasons/{season_id}/matchdays", status_code=201)
def create_matchday(season_id: int, payload: schemas.MatchdayCreate, db: Session = Depends(get_db)):
    season = db.query(models.Season).filter(models.Season.id == season_id).first()
    if not season:
        raise HTTPException(status_code=404, detail="Season not found")
        
    matchday = models.Matchday(
        name=payload.name,
        season_id=season_id,
        deadline_utc=payload.deadline_utc
    )
    db.add(matchday)
    db.commit()
    db.refresh(matchday)
    return matchday

@router.post("/seasons/{season_id}/leagues", status_code=201)
def create_league(season_id: int, payload: schemas.LeagueCreate, db: Session = Depends(get_db)):
    season = db.query(models.Season).filter(models.Season.id == season_id).first()
    if not season:
        raise HTTPException(status_code=404, detail="Season not found")
        
    league = models.League(
        name=payload.name,
        season_id=season_id
    )
    db.add(league)
    db.commit()
    db.refresh(league)
    return league

@router.post("/leagues/{league_id}/teams", status_code=201)
def create_team(league_id: int, payload: schemas.TeamCreate, db: Session = Depends(get_db)):
    league = db.query(models.League).filter(models.League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")
        
    team = models.Team(
        name=payload.name,
        league_id=league_id
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    return team

@router.post("/teams/{team_id}/players", status_code=201)
def create_player(team_id: int, payload: schemas.PlayerCreate, db: Session = Depends(get_db)):
    team = db.query(models.Team).filter(models.Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
        
    try:
        position = models.PlayerPosition(payload.position)
        tier = models.PlayerTier(payload.tier)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid position or tier")
        
    player = models.Player(
        name=payload.name,
        position=position,
        tier=tier,
        credit_value=payload.credit_value,
        team_id=team_id
    )
    db.add(player)
    db.commit()
    db.refresh(player)
    return player

@router.patch("/players/{player_id}", status_code=200)
def update_player(player_id: int, payload: schemas.PlayerUpdate, db: Session = Depends(get_db)):
    player = db.query(models.Player).filter(models.Player.id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
        
    if payload.is_active is not None:
        player.is_active = payload.is_active
    if payload.credit_value is not None:
        player.credit_value = payload.credit_value
    if payload.tier is not None:
        try:
            player.tier = models.PlayerTier(payload.tier)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid tier")
            
    db.commit()
    db.refresh(player)
    return player

# ── User management ────────────────────────────────────────────────────────────

class UserActivateRequest(BaseModel):
    is_active: bool

class UserRoleRequest(BaseModel):
    role: str  # "user" or "admin"


@router.get("/users", status_code=200)
def list_users(db: Session = Depends(get_db)):
    """List all registered users."""
    users = db.query(models.User).order_by(models.User.id).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "role": u.role.value,
            "is_active": u.is_active,
            "onboarding_complete": u.onboarding_complete,
        }
        for u in users
    ]


@router.patch("/users/{user_id}/activate", status_code=200)
def set_user_active(user_id: int, payload: UserActivateRequest, db: Session = Depends(get_db)):
    """Manually activate or deactivate a user account."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = payload.is_active
    if payload.is_active:
        user.activation_token = None  # clear any pending token
    db.commit()
    return {"id": user.id, "username": user.username, "is_active": user.is_active}


@router.patch("/users/{user_id}/role", status_code=200)
def set_user_role(user_id: int, payload: UserRoleRequest, db: Session = Depends(get_db)):
    """Promote a user to admin or demote to regular user."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        user.role = models.UserRole(payload.role)
    except ValueError:
        raise HTTPException(status_code=400, detail="Role must be 'user' or 'admin'")
    # Promoted admins should be active
    if user.role == models.UserRole.admin:
        user.is_active = True
        user.onboarding_complete = True
        user.activation_token = None
    db.commit()
    return {"id": user.id, "username": user.username, "role": user.role.value}


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURE endpoints
# ─────────────────────────────────────────────────────────────────────────────

class FixtureIn(BaseModel):
    home_team_id: int
    away_team_id: int
    kickoff_utc: datetime
    finished: bool = False


class FixtureBulkRequest(BaseModel):
    fixtures: List[FixtureIn]


@router.get("/matchday/{matchday_id}/fixtures")
def get_fixtures(matchday_id: int, db: Session = Depends(get_db)):
    """Return all fixtures for a matchday."""
    matchday = db.query(models.Matchday).filter(models.Matchday.id == matchday_id).first()
    if not matchday:
        raise HTTPException(status_code=404, detail="Matchday not found")
    return {
        "fixtures": [
            {
                "id": f.id,
                "matchday_id": f.matchday_id,
                "home_team_id": f.home_team_id,
                "away_team_id": f.away_team_id,
                "kickoff_utc": f.kickoff_utc.isoformat(),
                "finished": f.finished,
            }
            for f in matchday.fixtures
        ]
    }


@router.post("/matchday/{matchday_id}/fixtures", status_code=201)
def bulk_create_fixtures(
    matchday_id: int,
    payload: FixtureBulkRequest,
    db: Session = Depends(get_db),
):
    """
    Bulk-create fixtures for a matchday.

    Replaces all existing fixtures for the matchday, then recalculates
    lock_at_utc from the earliest kickoff.
    """
    from ..services.matchday_lock_service import initialize_matchday_lock
    from datetime import timezone

    matchday = db.query(models.Matchday).filter(models.Matchday.id == matchday_id).first()
    if not matchday:
        raise HTTPException(status_code=404, detail="Matchday not found")

    # Validate teams exist
    all_team_ids = {t.id for t in db.query(models.Team.id).all()}
    for f in payload.fixtures:
        if f.home_team_id not in all_team_ids:
            raise HTTPException(status_code=400, detail=f"Home team {f.home_team_id} not found")
        if f.away_team_id not in all_team_ids:
            raise HTTPException(status_code=400, detail=f"Away team {f.away_team_id} not found")
        if f.home_team_id == f.away_team_id:
            raise HTTPException(status_code=400, detail="Home and away team cannot be the same")

    # Replace fixtures
    db.query(models.Fixture).filter(models.Fixture.matchday_id == matchday_id).delete()
    for f in payload.fixtures:
        kickoff = f.kickoff_utc
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        db.add(models.Fixture(
            matchday_id=matchday_id,
            home_team_id=f.home_team_id,
            away_team_id=f.away_team_id,
            kickoff_utc=kickoff,
            finished=f.finished,
        ))
    db.flush()

    # Recalculate lock
    db.expire(matchday)  # ensure fixtures relationship is fresh
    lock_at = initialize_matchday_lock(matchday, db)

    return {
        "created": len(payload.fixtures),
        "matchday_id": matchday_id,
        "lock_at_utc": lock_at.isoformat() if lock_at else None,
    }


@router.post("/matchday/{matchday_id}/init-lock", status_code=200)
def init_matchday_lock(matchday_id: int, db: Session = Depends(get_db)):
    """Recalculate and persist lock_at_utc from existing fixtures."""
    from ..services.matchday_lock_service import initialize_matchday_lock

    matchday = db.query(models.Matchday).filter(models.Matchday.id == matchday_id).first()
    if not matchday:
        raise HTTPException(status_code=404, detail="Matchday not found")
    if not matchday.fixtures:
        raise HTTPException(status_code=400, detail="No fixtures found for this matchday")

    lock_at = initialize_matchday_lock(matchday, db)
    return {
        "matchday_id": matchday_id,
        "lock_at_utc": lock_at.isoformat() if lock_at else None,
    }


@router.get("/seasons/{season_id}/teams")
def get_teams_for_season(season_id: int, db: Session = Depends(get_db)):
    """Return all teams belonging to any league in a season (for fixture dropdowns)."""
    teams = (
        db.query(models.Team)
        .join(models.League, models.Team.league_id == models.League.id)
        .filter(models.League.season_id == season_id)
        .order_by(models.Team.name)
        .all()
    )
    return {"teams": [{"id": t.id, "name": t.name, "league_id": t.league_id} for t in teams]}
