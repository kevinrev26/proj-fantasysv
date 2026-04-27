from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models, schemas
from ..worker import recalculate_matchday_scores_task
from pydantic import BaseModel
from typing import List

router = APIRouter(prefix="/admin", tags=["admin"])

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
def get_matchday_players(matchday_id: int, db: Session = Depends(get_db)):
    # Fetch matchday and its season
    matchday = db.query(models.Matchday).filter(models.Matchday.id == matchday_id).first()
    if not matchday:
        raise HTTPException(404, "Matchday not found")
    
    # Get all teams that have players in this matchday (depends on your schedule)
    # Simplified: assume you have a MatchPlayer table or you can get players from tournament_phase
    # For demonstration, we return all players from all teams in the season that are still active.
    players = db.query(models.Player).join(models.Team).filter(
        models.Player.is_active == True,
        models.Team.league_id == ... # You need a way to link matchday to specific matches
    ).all()
    
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
