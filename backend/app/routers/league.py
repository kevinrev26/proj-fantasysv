from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
from typing import List, Optional
import random
import structlog

from ..dependencies import get_current_user
from ..database import get_db
from ..models import (
    User, Season, FantasyTeam, PrivateLeague, PrivateLeagueMembership, TeamScore, Matchday
)

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/league",
    tags=["Private Leagues"]
)

# ---------------------------------------------------------------------------
# Helper: Generate unique 5-digit join code
# ---------------------------------------------------------------------------
def generate_unique_join_code(db: Session, length: int = 5) -> str:
    """Generate a numeric join code that does not exist in PrivateLeague."""
    while True:
        # Generate random numeric string of given length
        code = "".join([str(random.randint(0, 9)) for _ in range(length)])
        existing = db.query(PrivateLeague).filter(PrivateLeague.join_code == code).first()
        if not existing:
            return code

# ---------------------------------------------------------------------------
# Pydantic Schemas (inline for this router)
# ---------------------------------------------------------------------------
from pydantic import BaseModel, Field

class PrivateLeagueCreate(BaseModel):
    name: str
    season_id: int
    max_teams: Optional[int] = None

class PrivateLeagueJoin(BaseModel):
    join_code: str = Field(..., min_length=5, max_length=5)

class PrivateLeagueResponse(BaseModel):
    id: int
    name: str
    season_id: int
    join_code: str
    created_by_user_id: int
    is_active: bool
    max_teams: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True

class MembershipResponse(BaseModel):
    private_league_id: int
    private_league_name: str
    season_id: int
    join_code: str
    joined_at: str

class LeaderboardEntry(BaseModel):
    fantasy_team_name: str
    username: str
    total_points: int

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/private", response_model=PrivateLeagueResponse)
def create_private_league(
    league_data: PrivateLeagueCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create a new private league. The creator automatically joins."""
    # 1. Validate season exists and is active (optional)
    season = db.query(Season).filter(
        Season.id == league_data.season_id,
        Season.status == "active"   # SeasonStatus.active
    ).first()
    if not season:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Season not found or not active"
        )

    # 2. Ensure user has a fantasy team for this season
    fantasy_team = db.query(FantasyTeam).filter(
        FantasyTeam.user_id == current_user.id,
        FantasyTeam.season_id == league_data.season_id
    ).first()
    if not fantasy_team:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must have a fantasy team for this season before creating a league"
        )

    # 3. Generate unique join code
    join_code = generate_unique_join_code(db)

    # 4. Create private league
    new_league = PrivateLeague(
        name=league_data.name,
        season_id=league_data.season_id,
        created_by_user_id=current_user.id,
        join_code=join_code,
        max_teams=league_data.max_teams,
        is_active=True
    )
    db.add(new_league)
    db.flush()  # to get new_league.id

    # 5. Add creator as a member
    membership = PrivateLeagueMembership(
        private_league_id=new_league.id,
        fantasy_team_id=fantasy_team.id
    )
    db.add(membership)
    db.commit()
    db.refresh(new_league)

    return new_league


@router.post("/private/join", status_code=status.HTTP_200_OK)
def join_private_league(
    join_data: PrivateLeagueJoin,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Join a private league using a 5-digit join code."""
    # 1. Find the league by code
    league = db.query(PrivateLeague).filter(
        PrivateLeague.join_code == join_data.join_code,
        PrivateLeague.is_active == True
    ).first()
    if not league:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or inactive join code"
        )

    # 2. Check if user has a fantasy team for this league's season
    fantasy_team = db.query(FantasyTeam).filter(
        FantasyTeam.user_id == current_user.id,
        FantasyTeam.season_id == league.season_id
    ).first()
    if not fantasy_team:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You don't have a fantasy team for season {league.season_id}"
        )

    # 3. Check if already a member
    existing_membership = db.query(PrivateLeagueMembership).filter(
        PrivateLeagueMembership.private_league_id == league.id,
        PrivateLeagueMembership.fantasy_team_id == fantasy_team.id
    ).first()
    if existing_membership:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already a member of this league"
        )

    # 4. Check max_teams limit if set
    if league.max_teams:
        current_members_count = db.query(func.count(PrivateLeagueMembership.id)).filter(
            PrivateLeagueMembership.private_league_id == league.id
        ).scalar()
        if current_members_count >= league.max_teams:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This private league has reached its maximum number of teams"
            )

    # 5. Add membership
    new_membership = PrivateLeagueMembership(
        private_league_id=league.id,
        fantasy_team_id=fantasy_team.id
    )
    db.add(new_membership)
    db.commit()

    return {"message": f"Successfully joined league '{league.name}'"}


@router.get("/private/{league_id}/leaderboard", response_model=List[LeaderboardEntry])
def get_private_league_leaderboard(
    league_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)  # authenticated, but leaderboard may be public? We'll keep auth.
):
    """Get the current leaderboard for a private league (cumulative points)."""
    # 1. Verify league exists
    league = db.query(PrivateLeague).filter(PrivateLeague.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="Private league not found")

    # 2. Build leaderboard:
    # For each fantasy team in the league, get its latest cumulative points.
    # Latest means the team_score row for the most recent closed matchday (or the highest matchday_id).
    # We use a subquery to get the latest TeamScore per fantasy_team where matchday is closed.
    subquery = (
        db.query(
            TeamScore.fantasy_team_id,
            TeamScore.cumulative_points,
            func.row_number().over(
                partition_by=TeamScore.fantasy_team_id,
                order_by=Matchday.id.desc()
            ).label("rn")
        )
        .join(Matchday, TeamScore.matchday_id == Matchday.id)
        .filter(Matchday.status == "closed")  # only count finalized matchdays
        .subquery()
    )

    latest_scores = db.query(
        subquery.c.fantasy_team_id,
        subquery.c.cumulative_points
    ).filter(subquery.c.rn == 1).subquery()

    # Now join with memberships, fantasy teams, and users
    results = (
        db.query(
            FantasyTeam.name.label("fantasy_team_name"),
            User.username,
            latest_scores.c.cumulative_points.label("total_points")
        )
        .join(PrivateLeagueMembership, PrivateLeagueMembership.fantasy_team_id == FantasyTeam.id)
        .join(User, FantasyTeam.user_id == User.id)
        .join(latest_scores, latest_scores.c.fantasy_team_id == FantasyTeam.id)
        .filter(PrivateLeagueMembership.private_league_id == league_id)
        .order_by(latest_scores.c.cumulative_points.desc())
        .all()
    )

    # If a fantasy team has no closed matchday scores yet, they won't appear.
    # Optionally we could include them with 0 points.
    # We'll add a fallback: include all members with 0 if missing.
    all_members = (
        db.query(FantasyTeam, User)
        .join(PrivateLeagueMembership, PrivateLeagueMembership.fantasy_team_id == FantasyTeam.id)
        .join(User, FantasyTeam.user_id == User.id)
        .filter(PrivateLeagueMembership.private_league_id == league_id)
        .all()
    )
    # Convert results to dict for easy lookup
    points_map = {r.fantasy_team_name: r.total_points for r in results}
    leaderboard = []
    for ft, user in all_members:
        leaderboard.append(LeaderboardEntry(
            fantasy_team_name=ft.name,
            username=user.username,
            total_points=points_map.get(ft.name, 0)
        ))
    # Sort again after adding zeros
    leaderboard.sort(key=lambda x: x.total_points, reverse=True)
    return leaderboard


@router.get("/private/my-leagues", response_model=List[MembershipResponse])
def get_my_private_leagues(
    season_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get all private leagues that the current user's fantasy team(s) have joined.
    Optionally filter by season_id.
    """
    # Find all fantasy teams of the current user
    query = db.query(FantasyTeam).filter(FantasyTeam.user_id == current_user.id)
    if season_id:
        query = query.filter(FantasyTeam.season_id == season_id)
    fantasy_teams = query.all()
    if not fantasy_teams:
        return []

    fantasy_team_ids = [ft.id for ft in fantasy_teams]

    # Get memberships with league details (including join_code)
    memberships = (
        db.query(
            PrivateLeagueMembership.private_league_id,
            PrivateLeague.name.label("private_league_name"),
            PrivateLeague.season_id,
            PrivateLeague.join_code,
            PrivateLeagueMembership.joined_at
        )
        .join(PrivateLeague, PrivateLeague.id == PrivateLeagueMembership.private_league_id)
        .filter(PrivateLeagueMembership.fantasy_team_id.in_(fantasy_team_ids))
        .all()
    )

    return [
        MembershipResponse(
            private_league_id=m.private_league_id,
            private_league_name=m.private_league_name,
            season_id=m.season_id,
            joined_at=m.joined_at.isoformat(),
            join_code=m.join_code                 # <-- added
        )
        for m in memberships
    ]