from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models, schemas
from ..worker import recalculate_matchday_scores_task, deactivate_players_for_teams_task, reactivate_players_for_teams_task, calculate_prediction_points_task
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from ..dependencies import require_admin

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

class MatchdayUpdateRequest(BaseModel):
    name: Optional[str] = None
    deadline_utc: Optional[datetime] = None
    status: Optional[str] = None
    is_locked: Optional[bool] = None


class MatchdayFullUpdate(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    is_locked: Optional[bool] = None
    lock_at_utc: Optional[datetime] = None


@router.patch("/matchday/{matchday_id}", status_code=200)
def update_matchday(
    matchday_id: int,
    payload: MatchdayUpdateRequest,
    db: Session = Depends(get_db)
):
    matchday = db.query(models.Matchday).filter(models.Matchday.id == matchday_id).first()
    if not matchday:
        raise HTTPException(status_code=404, detail="Matchday not found")

    if payload.name is not None:
        matchday.name = payload.name
    if payload.deadline_utc is not None:
        matchday.deadline_utc = payload.deadline_utc
    if payload.is_locked is not None:
        matchday.is_locked = payload.is_locked
    if payload.status is not None:
        try:
            matchday.status = models.MatchdayStatus(payload.status)
        except ValueError:
            valid = [e.value for e in models.MatchdayStatus]
            raise HTTPException(status_code=400, detail=f"Invalid status. Valid: {valid}")
            
    db.commit()
    db.refresh(matchday)
    return {
        "id": matchday.id,
        "name": matchday.name,
        "status": matchday.status.value,
        "is_locked": matchday.is_locked,
        "lock_at_utc": matchday.lock_at_utc.isoformat() if matchday.lock_at_utc else None,
    }


@router.delete("/matchday/{matchday_id}", status_code=200)
def delete_matchday(matchday_id: int, db: Session = Depends(get_db)):
    """
    Delete a matchday and all its fixtures, player scores, and team scores.
    Blocked if the matchday is closed (scoring already applied).
    """
    md = db.query(models.Matchday).filter(models.Matchday.id == matchday_id).first()
    if not md:
        raise HTTPException(status_code=404, detail="Matchday not found")
    if md.status == models.MatchdayStatus.closed:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a closed matchday — scoring data would be lost. Change status first.",
        )
    db.delete(md)
    db.commit()
    return {"deleted": matchday_id}


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
        
    if payload.name is not None:
        player.name = payload.name
    if payload.position is not None:
        try:
            player.position = models.PlayerPosition(payload.position)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid position")
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


@router.get("/users/{user_id}", status_code=200)
def get_user(user_id: int, db: Session = Depends(get_db)):
    """Get a single user by ID."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role.value,
        "is_active": user.is_active,
        "onboarding_complete": user.onboarding_complete,
    }


@router.post("/users", status_code=201)
def create_user(payload: schemas.UserCreate, db: Session = Depends(get_db)):
    """Create a new user."""
    # Check if user already exists
    existing_user = db.query(models.User).filter(
        (models.User.username == payload.username) | 
        (models.User.email == payload.email)
    ).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Username or email already exists")
    
    # Create user with hashed password
    from ..security import get_password_hash
    hashed_password = get_password_hash(payload.password)
    
    user = models.User(
        username=payload.username,
        email=payload.email,
        password_hash=hashed_password,
        role=models.UserRole.user,  # Default to user role
        is_active=True
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role.value,
        "is_active": user.is_active,
        "onboarding_complete": user.onboarding_complete,
    }


@router.put("/users/{user_id}", status_code=200)
def update_user(user_id: int, payload: schemas.UserCreate, db: Session = Depends(get_db)):
    """Update user details."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Check if username or email is already taken by another user
    existing_user = db.query(models.User).filter(
        (models.User.id != user_id) &
        ((models.User.username == payload.username) | 
         (models.User.email == payload.email))
    ).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Username or email already exists")
    
    user.username = payload.username
    user.email = payload.email
    if payload.password:
        from ..security import get_password_hash
        user.password_hash = get_password_hash(payload.password)
    
    db.commit()
    db.refresh(user)
    
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role.value,
        "is_active": user.is_active,
        "onboarding_complete": user.onboarding_complete,
    }


@router.delete("/users/{user_id}", status_code=200)
def delete_user(user_id: int, db: Session = Depends(get_db)):
    """Delete a user."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    db.delete(user)
    db.commit()
    
    return {"deleted": user_id}


@router.post("/users/{user_id}/reset-password", status_code=200)
def reset_user_password(user_id: int, db: Session = Depends(get_db)):
    """Reset user password (placeholder)."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # In a real implementation, this would:
    # 1. Generate a new temporary password
    # 2. Send it to the user's email
    # 3. Store the temporary password hash
    # For now, we'll just return a placeholder response
    
    return {
        "message": "Password reset initiated. A temporary password will be sent to the user's email.",
        "user_id": user_id
    }


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


@router.post("/matchday/{matchday_id}/reset-lock", status_code=200)
def reset_matchday_lock_to_earliest_fixture(matchday_id: int, db: Session = Depends(get_db)):
    """
    Reset the matchday lock time to the earliest fixture's kickoff time.
    This is useful for manually adjusting the lock time from admin view.
    """
    from ..services.matchday_lock_service import _get_lock_offset, _now_utc
    from datetime import timedelta

    matchday = db.query(models.Matchday).filter(models.Matchday.id == matchday_id).first()
    if not matchday:
        raise HTTPException(status_code=404, detail="Matchday not found")
    
    if not matchday.fixtures:
        raise HTTPException(status_code=400, detail="No fixtures found for this matchday")
    
    # Find the earliest fixture that hasn't kicked off yet
    now_utc = _now_utc()
    upcoming_fixtures = [
        f for f in matchday.fixtures 
        if f.kickoff_utc > now_utc
    ]
    
    if not upcoming_fixtures:
        # If no upcoming fixtures, use the earliest fixture (even if it's in the past)
        # This maintains backward compatibility for edge cases
        earliest_fixture = min(matchday.fixtures, key=lambda f: f.kickoff_utc)
    else:
        # Use the earliest upcoming fixture
        earliest_fixture = min(upcoming_fixtures, key=lambda f: f.kickoff_utc)
    
    # Calculate lock time based on earliest fixture
    lock_offset = _get_lock_offset(db)
    lock_at = earliest_fixture.kickoff_utc - timedelta(minutes=lock_offset)
    
    matchday.lock_at_utc = lock_at
    db.commit()
    db.refresh(matchday)
    
    return {
        "matchday_id": matchday_id,
        "lock_at_utc": lock_at.isoformat() if lock_at else None,
    }


class FixtureUpdate(BaseModel):
    home_team_id: Optional[int] = None
    away_team_id: Optional[int] = None
    kickoff_utc: Optional[datetime] = None
    finished: Optional[bool] = None


@router.patch("/fixtures/{fixture_id}", status_code=200)
def update_fixture(fixture_id: int, payload: FixtureUpdate, db: Session = Depends(get_db)):
    """Update individual fixture fields and recalculate the matchday lock time."""
    from ..services.matchday_lock_service import initialize_matchday_lock
    from datetime import timezone

    fixture = db.query(models.Fixture).filter(models.Fixture.id == fixture_id).first()
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")

    if payload.home_team_id is not None:
        if not db.query(models.Team).filter(models.Team.id == payload.home_team_id).first():
            raise HTTPException(status_code=400, detail=f"Home team {payload.home_team_id} not found")
        fixture.home_team_id = payload.home_team_id

    if payload.away_team_id is not None:
        if not db.query(models.Team).filter(models.Team.id == payload.away_team_id).first():
            raise HTTPException(status_code=400, detail=f"Away team {payload.away_team_id} not found")
        fixture.away_team_id = payload.away_team_id

    if fixture.home_team_id == fixture.away_team_id:
        raise HTTPException(status_code=400, detail="Home and away team cannot be the same")

    if payload.kickoff_utc is not None:
        kickoff = payload.kickoff_utc
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        fixture.kickoff_utc = kickoff

    if payload.finished is not None:
        fixture.finished = payload.finished

    db.commit()
    db.refresh(fixture)

    # Recalculate matchday lock
    matchday = db.query(models.Matchday).filter(models.Matchday.id == fixture.matchday_id).first()
    db.expire(matchday)
    lock_at = initialize_matchday_lock(matchday, db)

    return {
        "id": fixture.id,
        "matchday_id": fixture.matchday_id,
        "home_team_id": fixture.home_team_id,
        "away_team_id": fixture.away_team_id,
        "kickoff_utc": fixture.kickoff_utc.isoformat(),
        "finished": fixture.finished,
        "lock_at_utc": lock_at.isoformat() if lock_at else None,
    }


@router.delete("/fixtures/{fixture_id}", status_code=200)
def delete_fixture(fixture_id: int, db: Session = Depends(get_db)):
    """Delete a single fixture and recalculate the matchday lock time."""
    from ..services.matchday_lock_service import initialize_matchday_lock

    fixture = db.query(models.Fixture).filter(models.Fixture.id == fixture_id).first()
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")

    matchday_id = fixture.matchday_id
    db.delete(fixture)
    db.commit()

    matchday = db.query(models.Matchday).filter(models.Matchday.id == matchday_id).first()
    db.expire(matchday)
    lock_at = initialize_matchday_lock(matchday, db) if matchday.fixtures else None

    return {
        "deleted": fixture_id,
        "matchday_id": matchday_id,
        "lock_at_utc": lock_at.isoformat() if lock_at else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURE RESULT endpoints
# ─────────────────────────────────────────────────────────────────────────────

class FixtureResultIn(BaseModel):
    home_goals: int
    away_goals: int
    extra_time_played: bool = False
    home_extra_goals: int = 0
    away_extra_goals: int = 0
    penalty_shootout: bool = False
    home_penalties: int = 0
    away_penalties: int = 0
    winner_team_id: Optional[int] = None
    source: str = "admin"


class FixtureResultUpdate(BaseModel):
    home_goals: Optional[int] = None
    away_goals: Optional[int] = None
    extra_time_played: Optional[bool] = None
    home_extra_goals: Optional[int] = None
    away_extra_goals: Optional[int] = None
    penalty_shootout: Optional[bool] = None
    home_penalties: Optional[int] = None
    away_penalties: Optional[int] = None
    winner_team_id: Optional[int] = None
    source: Optional[str] = None


def _fixture_result_dict(fr: models.FixtureResult) -> dict:
    return {
        "id": fr.id,
        "fixture_id": fr.fixture_id,
        "home_goals": fr.home_goals,
        "away_goals": fr.away_goals,
        "extra_time_played": fr.extra_time_played,
        "home_extra_goals": fr.home_extra_goals,
        "away_extra_goals": fr.away_extra_goals,
        "penalty_shootout": fr.penalty_shootout,
        "home_penalties": fr.home_penalties,
        "away_penalties": fr.away_penalties,
        "winner_team_id": fr.winner_team_id,
        "verified_at": fr.verified_at.isoformat() if fr.verified_at else None,
        "source": fr.source,
    }


@router.get("/matchday/{matchday_id}/fixture-results", status_code=200)
def list_matchday_fixture_results(matchday_id: int, db: Session = Depends(get_db)):
    """
    Return all fixtures for a matchday, each with their FixtureResult (if any).
    """
    matchday = db.query(models.Matchday).filter(models.Matchday.id == matchday_id).first()
    if not matchday:
        raise HTTPException(status_code=404, detail="Matchday not found")

    fixtures = (
        db.query(models.Fixture)
        .filter(models.Fixture.matchday_id == matchday_id)
        .all()
    )

    result = []
    for f in fixtures:
        result.append({
            "fixture_id": f.id,
            "home_team_id": f.home_team_id,
            "home_team_name": f.home_team.name,
            "away_team_id": f.away_team_id,
            "away_team_name": f.away_team.name,
            "kickoff_utc": f.kickoff_utc.isoformat(),
            "finished": f.finished,
            "result": _fixture_result_dict(f.result) if f.result else None,
        })

    return {"matchday_id": matchday_id, "fixtures": result}


@router.post("/fixtures/{fixture_id}/result", status_code=201)
def create_fixture_result(
    fixture_id: int,
    payload: FixtureResultIn,
    db: Session = Depends(get_db),
):
    """Create a FixtureResult for a fixture. Each fixture can have at most one result."""
    fixture = db.query(models.Fixture).filter(models.Fixture.id == fixture_id).first()
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")
    if fixture.result:
        raise HTTPException(
            status_code=409,
            detail="A result already exists for this fixture. Use PATCH to update it.",
        )

    if payload.winner_team_id is not None:
        if not db.query(models.Team).filter(models.Team.id == payload.winner_team_id).first():
            raise HTTPException(status_code=400, detail=f"Winner team {payload.winner_team_id} not found")

    from datetime import timezone
    fr = models.FixtureResult(
        fixture_id=fixture_id,
        home_goals=payload.home_goals,
        away_goals=payload.away_goals,
        extra_time_played=payload.extra_time_played,
        home_extra_goals=payload.home_extra_goals,
        away_extra_goals=payload.away_extra_goals,
        penalty_shootout=payload.penalty_shootout,
        home_penalties=payload.home_penalties,
        away_penalties=payload.away_penalties,
        winner_team_id=payload.winner_team_id,
        source=payload.source,
        verified_at=datetime.now(timezone.utc),
    )
    db.add(fr)
    fixture.finished = True
    db.commit()
    db.refresh(fr)
    calculate_prediction_points_task.delay(fixture_id)
    return _fixture_result_dict(fr)


@router.get("/fixture-results/{result_id}", status_code=200)
def get_fixture_result(result_id: int, db: Session = Depends(get_db)):
    """Get a single FixtureResult by its ID."""
    fr = db.query(models.FixtureResult).filter(models.FixtureResult.id == result_id).first()
    if not fr:
        raise HTTPException(status_code=404, detail="FixtureResult not found")
    return _fixture_result_dict(fr)


@router.patch("/fixture-results/{result_id}", status_code=200)
def update_fixture_result(
    result_id: int,
    payload: FixtureResultUpdate,
    db: Session = Depends(get_db),
):
    """Partially update an existing FixtureResult."""
    fr = db.query(models.FixtureResult).filter(models.FixtureResult.id == result_id).first()
    if not fr:
        raise HTTPException(status_code=404, detail="FixtureResult not found")

    if payload.home_goals is not None:
        fr.home_goals = payload.home_goals
    if payload.away_goals is not None:
        fr.away_goals = payload.away_goals
    if payload.extra_time_played is not None:
        fr.extra_time_played = payload.extra_time_played
    if payload.home_extra_goals is not None:
        fr.home_extra_goals = payload.home_extra_goals
    if payload.away_extra_goals is not None:
        fr.away_extra_goals = payload.away_extra_goals
    if payload.penalty_shootout is not None:
        fr.penalty_shootout = payload.penalty_shootout
    if payload.home_penalties is not None:
        fr.home_penalties = payload.home_penalties
    if payload.away_penalties is not None:
        fr.away_penalties = payload.away_penalties
    if payload.winner_team_id is not None:
        if not db.query(models.Team).filter(models.Team.id == payload.winner_team_id).first():
            raise HTTPException(status_code=400, detail=f"Winner team {payload.winner_team_id} not found")
        fr.winner_team_id = payload.winner_team_id
    if payload.source is not None:
        fr.source = payload.source

    from datetime import timezone
    fr.verified_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(fr)
    return _fixture_result_dict(fr)


@router.delete("/fixture-results/{result_id}", status_code=200)
def delete_fixture_result(result_id: int, db: Session = Depends(get_db)):
    """Delete a FixtureResult and mark the linked fixture as not finished."""
    fr = db.query(models.FixtureResult).filter(models.FixtureResult.id == result_id).first()
    if not fr:
        raise HTTPException(status_code=404, detail="FixtureResult not found")

    fixture_id = fr.fixture_id
    fixture = db.query(models.Fixture).filter(models.Fixture.id == fixture_id).first()

    db.delete(fr)
    if fixture:
        fixture.finished = False
    db.commit()

    return {"deleted": result_id, "fixture_id": fixture_id}


@router.get("/export/players")
def export_players(season_id: Optional[int] = None, db: Session = Depends(get_db)):
    """
    Return player_id, name, and team_name for all active players.
    Optionally filter by season_id to restrict to teams in that season's leagues.
    """
    query = (
        db.query(models.Player, models.Team)
        .join(models.Team, models.Player.team_id == models.Team.id)
        .filter(models.Player.is_active == True)
    )

    if season_id is not None:
        query = (
            query
            .join(models.League, models.Team.league_id == models.League.id)
            .filter(models.League.season_id == season_id)
        )

    rows = query.order_by(models.Team.name, models.Player.name).all()

    return {
        "players": [
            {
                "player_id": player.id,
                "name": player.name,
                "team_name": team.name,
            }
            for player, team in rows
        ]
    }


# ─────────────────────────────────────────────────────────────────────────────
# TournamentPhase CRUD endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/phases", response_model=List[schemas.TournamentPhaseResponse])
def list_tournament_phases(season_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(models.TournamentPhase)
    if season_id is not None:
        query = query.filter(models.TournamentPhase.season_id == season_id)
    return query.all()

@router.get("/phases/{phase_id}", response_model=schemas.TournamentPhaseResponse)
def get_tournament_phase(phase_id: int, db: Session = Depends(get_db)):
    phase = db.query(models.TournamentPhase).filter(models.TournamentPhase.id == phase_id).first()
    if not phase:
        raise HTTPException(status_code=404, detail="Tournament phase not found")
    return phase

@router.patch("/phases/{phase_id}", response_model=schemas.TournamentPhaseResponse)
def update_tournament_phase(
    phase_id: int,
    payload: schemas.TournamentPhaseUpdate,
    db: Session = Depends(get_db)
):
    phase = db.query(models.TournamentPhase).filter(models.TournamentPhase.id == phase_id).first()
    if not phase:
        raise HTTPException(status_code=404, detail="Tournament phase not found")

    update_data = payload.model_dump(exclude_unset=True)
    if "name" in update_data:
        try:
            phase.name = models.PhaseName(update_data["name"])
        except ValueError:
            valid = [e.value for e in models.PhaseName]
            raise HTTPException(status_code=400, detail=f"Invalid phase name. Valid values: {valid}")

    if "season_id" in update_data:
        # Verify season exists
        season = db.query(models.Season).filter(models.Season.id == update_data["season_id"]).first()
        if not season:
            raise HTTPException(status_code=404, detail="Season not found")
        phase.season_id = update_data["season_id"]

    db.commit()
    db.refresh(phase)
    return phase

@router.delete("/phases/{phase_id}", status_code=200)
def delete_tournament_phase(phase_id: int, db: Session = Depends(get_db)):
    phase = db.query(models.TournamentPhase).filter(models.TournamentPhase.id == phase_id).first()
    if not phase:
        raise HTTPException(status_code=404, detail="Tournament phase not found")
    db.delete(phase)
    db.commit()
    return {"deleted": phase_id}


# ─────────────────────────────────────────────────────────────────────────────
# Team CRUD endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/teams", response_model=List[schemas.TeamResponseDetail])
def list_teams(
    league_id: Optional[int] = None,
    season_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    query = db.query(models.Team)
    if league_id is not None:
        query = query.filter(models.Team.league_id == league_id)
    if season_id is not None:
        query = query.join(models.League).filter(models.League.season_id == season_id)
    return query.all()

@router.get("/teams/{team_id}", response_model=schemas.TeamResponseDetail)
def get_team(team_id: int, db: Session = Depends(get_db)):
    team = db.query(models.Team).filter(models.Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team

@router.patch("/teams/{team_id}", response_model=schemas.TeamResponseDetail)
def update_team(
    team_id: int,
    payload: schemas.TeamUpdate,
    db: Session = Depends(get_db)
):
    team = db.query(models.Team).filter(models.Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    update_data = payload.model_dump(exclude_unset=True)

    if "name" in update_data:
        team.name = update_data["name"]

    if "league_id" in update_data:
        league = db.query(models.League).filter(models.League.id == update_data["league_id"]).first()
        if not league:
            raise HTTPException(status_code=404, detail="League not found")
        team.league_id = update_data["league_id"]

    if "eliminated_in_phase_id" in update_data:
        old_phase_id = team.eliminated_in_phase_id
        new_phase_id = update_data["eliminated_in_phase_id"]

        if new_phase_id is not None:
            phase = db.query(models.TournamentPhase).filter(models.TournamentPhase.id == new_phase_id).first()
            if not phase:
                raise HTTPException(status_code=404, detail="Tournament phase not found")

        team.eliminated_in_phase_id = new_phase_id

        # Business logic for deactivating/reactivating players based on change
        if old_phase_id is None and new_phase_id is not None:
            # Team is eliminated -> deactivate its players
            deactivate_players_for_teams_task.delay([team.id])
        elif old_phase_id is not None and new_phase_id is None:
            # Team is restored -> reactivate its players
            reactivate_players_for_teams_task.delay([team.id])

    db.commit()
    db.refresh(team)
    return team

@router.delete("/teams/{team_id}", status_code=200)
def delete_team(team_id: int, db: Session = Depends(get_db)):
    team = db.query(models.Team).filter(models.Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    db.delete(team)
    db.commit()
    return {"deleted": team_id}


# ─────────────────────────────────────────────────────────────────────────────
# Player CRUD endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/players", response_model=List[schemas.PlayerResponseDetail])
def list_players(team_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(models.Player)
    if team_id is not None:
        query = query.filter(models.Player.team_id == team_id)
    return query.all()

@router.get("/players/{player_id}", response_model=schemas.PlayerResponseDetail)
def get_player(player_id: int, db: Session = Depends(get_db)):
    player = db.query(models.Player).filter(models.Player.id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    return player

@router.delete("/players/{player_id}", status_code=200)
def delete_player(player_id: int, db: Session = Depends(get_db)):
    player = db.query(models.Player).filter(models.Player.id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    db.delete(player)
    db.commit()
    return {"deleted": player_id}


