from typing import Dict

# Goal points per position (spec: FW=6, MF=8, DF=10; GK not specified, fallback to 15)
GOAL_POINTS: Dict[str, int] = {
    "FW": 6,
    "MF": 8,
    "DF": 10,
    "GK": 15,      # not required by spec, kept for potential completeness
}

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
    Pure function to calculate fantasy points based on the official scoring table.

    Returns:
        dict: {"base_points": int, "bonus_points": int, "final_points": int}
    """
    if minutes_played <= 0:
        return {"base_points": 0, "bonus_points": 0, "final_points": 0}

    position = position.upper()
    base_points = 0
    bonus_points = 0

    # 1. Minutes played
    base_points += 1                     # 1+ min
    if minutes_played >= 60:
        base_points += 2                 # additional 2 pts (total 3 for ≥60 min)

    # 2. Goals (per position multiplier)
    goal_multiplier = GOAL_POINTS.get(position, 0)
    base_points += goals * goal_multiplier

    # 3. Assists (4 pts each)
    base_points += assists * 4

    # 4. Clean sheets (require 0 goals conceded and at least 60 minutes played)
    if minutes_played >= 60 and goals_conceded == 0:
        if position == "GK":
            base_points += 8
        elif position == "DF":
            base_points += 4

    # 5. Goals conceded penalties
    if position == "GK":
        base_points -= goals_conceded          # -1 per goal conceded
    elif position == "DF" and goals_conceded >= 2:
        base_points -= 1                       # -1 if 2+ goals conceded (once)

    # 6. Penalties saved (GK only)
    if position == "GK":
        base_points += penalties_saved * 8

    # 7. Cards
    base_points -= yellow_cards * 1
    base_points -= red_cards * 3

    # 8. Penalty missed & own goal
    base_points -= penalties_missed * 2
    base_points -= own_goals * 2

    # 9. Bonus points
    # Hat trick (3+ goals): +5 bonus
    if goals >= 3:
        bonus_points += 5

    # Goal + assist combo: +2 bonus
    if goals >= 1 and assists >= 1:
        bonus_points += 2

    final_points = base_points + bonus_points
    return {
        "base_points": base_points,
        "bonus_points": bonus_points,
        "final_points": final_points
    }