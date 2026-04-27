from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from pydantic import BaseModel
from ..database import get_db
from .. import models

router = APIRouter(prefix="/squad", tags=["squad"])

def get_current_user_id(request: Request) -> int:
    # Dummy user logic as auth is not implemented yet
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        if token.startswith("dummy-token-user-"):
            try:
                return int(token.split("-")[-1])
            except:
                pass
    return 1  # Default to user id 1

class PlayerSlot(BaseModel):
    player_id: int
    slot: str  # "starter" or "bench"
    formation_position: str
    is_x2_joker: bool = False

class SquadUpdateRequest(BaseModel):
    players: List[PlayerSlot]
    formation_id: str

@router.get("/players")
def get_all_players(db: Session = Depends(get_db)):
    players = db.query(models.Player).filter(models.Player.is_active == True).all()
    # Mocking the response slightly to match frontend expectations if needed,
    # but the frontend map can handle it
    result = []
    for p in players:
        result.append({
            "id": p.id,
            "name": p.name,
            "pos": p.position.name,
            "tier": p.tier.name,
            "club": p.team.name if p.team else "Unknown",
            "rating": 80,  # Mock rating
            "cost": p.credit_value,
            "active": p.is_active
        })
    return {"players": result}

@router.get("/")
def get_squad(request: Request, db: Session = Depends(get_db)):
    user_id = get_current_user_id(request)
    
    # Get active season
    season = db.query(models.Season).filter(models.Season.status == models.SeasonStatus.active).first()
    if not season:
        raise HTTPException(status_code=404, detail="No active season found")
        
    team = db.query(models.FantasyTeam).filter(
        models.FantasyTeam.user_id == user_id,
        models.FantasyTeam.season_id == season.id
    ).first()
    
    # Get current (upcoming) matchday
    matchday = db.query(models.Matchday).filter(
        models.Matchday.season_id == season.id,
        models.Matchday.status != models.MatchdayStatus.closed
    ).order_by(models.Matchday.id.asc()).first()
    
    # Get System config for free transfers
    conf = db.query(models.SystemConfig).filter(models.SystemConfig.key == "free_transfers_per_matchday").first()
    free_transfers_allowed = int(conf.value) if conf else 1
    
    transfers_used = 0
    if matchday and team:
        # Count transfers made for this matchday
        transfers_used = db.query(func.count(models.Transfer.id)).filter(
            models.Transfer.fantasy_team_id == team.id,
            models.Transfer.matchday_id == matchday.id
        ).scalar()
        
    free_remaining = max(0, free_transfers_allowed - transfers_used)
    
    if not team:
        return {
            "squad": [],
            "budget": 100,
            "free_transfers_remaining": free_transfers_allowed,
            "is_locked": False if matchday and matchday.status == models.MatchdayStatus.scheduled else True
        }
        
    players = []
    total_cost = 0
    for fp in team.fantasy_players:
        cp = fp.player
        players.append({
            "player_id": cp.id,
            "slot": fp.slot.name,
            "formation_position": fp.formation_position,
            "is_x2_joker": fp.is_x2_joker
        })
        total_cost += cp.credit_value
        
    return {
        "squad": players,
        "budget": 100 - total_cost,
        "free_transfers_remaining": free_remaining,
        "is_locked": False if matchday and matchday.status == models.MatchdayStatus.scheduled else True
    }

@router.put("/")
def update_squad(payload: SquadUpdateRequest, request: Request, db: Session = Depends(get_db)):
    user_id = get_current_user_id(request)
    
    season = db.query(models.Season).filter(models.Season.status == models.SeasonStatus.active).first()
    if not season:
        raise HTTPException(status_code=404, detail="No active season")
        
    matchday = db.query(models.Matchday).filter(
        models.Matchday.season_id == season.id,
        models.Matchday.status != models.MatchdayStatus.closed
    ).order_by(models.Matchday.id.asc()).first()
    
    if not matchday:
        raise HTTPException(status_code=400, detail="No upcoming matchday")
        
    if matchday.status == models.MatchdayStatus.in_progress:
        raise HTTPException(status_code=400, detail="Matchday in progress. Squads are locked.")
        
    team = db.query(models.FantasyTeam).filter(
        models.FantasyTeam.user_id == user_id,
        models.FantasyTeam.season_id == season.id
    ).first()
    
    # If no team exists, create one
    if not team:
        team = models.FantasyTeam(
            name=f"Team {user_id}",
            user_id=user_id,
            season_id=season.id
        )
        db.add(team)
        db.flush()
        
    # Get current squad players
    current_fps = {fp.player_id: fp for fp in db.query(models.FantasyPlayer).filter(models.FantasyPlayer.fantasy_team_id == team.id).all()}
    
    new_player_ids = set([p.player_id for p in payload.players])
    old_player_ids = set(current_fps.keys())
    
    removed_ids = old_player_ids - new_player_ids
    added_ids = new_player_ids - old_player_ids
    
    # Calculate transfers cost
    conf = db.query(models.SystemConfig).filter(models.SystemConfig.key == "free_transfers_per_matchday").first()
    free_transfers_allowed = int(conf.value) if conf else 1
    
    transfers_used_already = db.query(func.count(models.Transfer.id)).filter(
        models.Transfer.fantasy_team_id == team.id,
        models.Transfer.matchday_id == matchday.id
    ).scalar()
    
    available_free = max(0, free_transfers_allowed - transfers_used_already)
    paid_transfers_incurred = 0
    total_penalty = 0
    
    # We do a naive pairing if they are not equal, but let's just compute total cost based on added players
    # Eliminated players don't count towards transfer limit
    # Find which removed players were eliminated
    if removed_ids:
        removed_players = db.query(models.Player).filter(models.Player.id.in_(list(removed_ids))).all()
        for p in removed_players:
            # Check if player's team is eliminated
            if p.team and p.team.eliminated_in_phase_id is not None:
                # Free remove! One incoming player should be paired with this without using free transfer limit
                if added_ids:
                    # just logically pop one added_id
                    list(added_ids).pop()
                pass
            else:
                # Regular transfer
                if available_free > 0:
                    available_free -= 1
                else:
                    paid_transfers_incurred += 1
                    total_penalty += 4
    
    # Wait, the number of added_ids should be exactly the number of removed_ids unless squad size changes.
    # In case of initialization (old_squad = 0), free transfers should not apply (cost 0).
    is_initialization = len(old_player_ids) == 0
    if is_initialization:
        total_penalty = 0
        paid_transfers_incurred = 0
        
    # Process the changes
    db.query(models.FantasyPlayer).filter(models.FantasyPlayer.fantasy_team_id == team.id).delete()
    
    # Add new players
    for slot_data in payload.players:
        fp_enum = models.FantasySlot.starter if slot_data.slot == "starter" else models.FantasySlot.bench
        new_fp = models.FantasyPlayer(
            fantasy_team_id=team.id,
            player_id=slot_data.player_id,
            slot=fp_enum,
            formation_position=slot_data.formation_position,
            is_x2_joker=slot_data.is_x2_joker
        )
        db.add(new_fp)

    # Record Transfer history and penalty
    if not is_initialization and (added_ids or removed_ids):
        # We'll just create generic transfer records linking first added with first removed
        list_rem = list(removed_ids)
        list_add = list(added_ids)
        
        for i in range(max(len(list_rem), len(list_add))):
            r_id = list_rem[i] if i < len(list_rem) else None
            a_id = list_add[i] if i < len(list_add) else None
            
            # This is a bit of an approximation if lengths differ, which they shouldn't for 15
            if r_id and a_id:
                # Calculate cost for this specific transfer
                t = models.Transfer(
                    fantasy_team_id=team.id,
                    matchday_id=matchday.id,
                    player_out_id=r_id,
                    player_in_id=a_id,
                    cost=4 if paid_transfers_incurred > 0 else 0
                )
                if paid_transfers_incurred > 0:
                    paid_transfers_incurred -= 1
                db.add(t)

    # Update TeamScore transfer_penalty for the upcoming matchday
    if total_penalty > 0:
        ts = db.query(models.TeamScore).filter(
            models.TeamScore.fantasy_team_id == team.id,
            models.TeamScore.matchday_id == matchday.id
        ).first()
        if not ts:
            ts = models.TeamScore(
                fantasy_team_id=team.id,
                matchday_id=matchday.id,
                points_this_matchday=0,
                transfer_penalty=0
            )
            db.add(ts)
        ts.transfer_penalty += total_penalty
        
    db.commit()
    
    return {"status": "success", "penalty_incurred": total_penalty}
