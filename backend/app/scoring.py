from typing import Dict

def calculate_player_points(
    position: str,
    minutes_played: int,
    goals: int,
    assists: int,
    goals_conceded: int,
    yellow_cards: int,
    red_cards: int,
    own_goals: int,
    penalties_missed: int,
    penalties_saved: int
) -> Dict[str, int]:
    """
    Pure Python function to calculate fantasy points based on the scoring table.
    """
    base_points = 0
    bonus_points = 0
    position = position.upper()
    
    if minutes_played == 0:
        return {"base_points": 0, "bonus_points": 0, "final_points": 0}

    # 1. Minutes Played
    if minutes_played >= 1:
        base_points += 1
    if minutes_played >= 60:
        base_points += 2  # Total 3 points for >= 60 mins

    # 2. Goals
    goal_multiplier = 0
    if position == "FW":
        goal_multiplier = 6
    elif position == "MF":
        goal_multiplier = 8
    elif position == "DF":
        goal_multiplier = 10
    elif position == "GK":
        goal_multiplier = 15
        
    base_points += (goals * goal_multiplier)

    # 3. Assists
    base_points += (assists * 4)

    # 4. Clean Sheets (Requires 90 minutes played and 0 goals conceded)
    if minutes_played >= 90 and goals_conceded == 0:
        if position == "GK":
            base_points += 8
        elif position == "DF":
            base_points += 4

    # 5. Goals Conceded
    if position == "GK":
        base_points -= goals_conceded
    elif position == "DF":
        # -1 for every 2 goals conceded
        base_points -= (goals_conceded // 2)

    # 6. Penalties Saved
    if position == "GK":
        base_points += (penalties_saved * 8)

    # 7. Cards
    base_points -= (yellow_cards * 1)
    base_points -= (red_cards * 3)

    # 8. Penalties Missed and Own Goals
    base_points -= (penalties_missed * 2)
    base_points -= (own_goals * 2)

    # 9. Bonus Points
    # Hat trick (+5 bonus)
    if goals >= 3:
        bonus_points += 5
    
    # Combo bonus: Goal + assist in same match (+2 bonus)
    if goals >= 1 and assists >= 1:
        bonus_points += 2

    return {
        "base_points": base_points,
        "bonus_points": bonus_points,
        "final_points": base_points + bonus_points
    }
