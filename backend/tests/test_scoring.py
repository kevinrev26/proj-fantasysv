import pytest
from app.scoring import calculate_player_points, apply_wildcard_multiplier, validate_wildcard_constraint

# ---------- Helper to test final_points easily ----------
def assert_points(position, minutes, goals, assists, goals_conceded,
                  yellow, red, own_goals, pen_missed, pen_saved,
                  expected_final, expected_base=None, expected_bonus=None):
    result = calculate_player_points(
        position=position,
        minutes_played=minutes,
        goals=goals,
        assists=assists,
        goals_conceded=goals_conceded,
        yellow_cards=yellow,
        red_cards=red,
        own_goals=own_goals,
        penalties_missed=pen_missed,
        penalties_saved=pen_saved
    )
    assert result["final_points"] == expected_final
    if expected_base is not None:
        assert result["base_points"] == expected_base
    if expected_bonus is not None:
        assert result["bonus_points"] == expected_bonus
    return result

# ---------- Minutes played ----------
def test_zero_minutes():
    assert_points("FW", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    # Also check base/bonus are zero
    res = calculate_player_points("FW", 0, 0, 0, 0, 0, 0, 0, 0, 0)
    assert res["base_points"] == 0
    assert res["bonus_points"] == 0

def test_minutes_1_59():
    # 1+ min = +1 only
    assert_points("FW", 30, 0, 0, 0, 0, 0, 0, 0, 0, 1)
    # 59 min same
    assert_points("FW", 59, 0, 0, 0, 0, 0, 0, 0, 0, 1)

def test_minutes_60_plus():
    # 60+ min = +3 total
    assert_points("FW", 60, 0, 0, 0, 0, 0, 0, 0, 0, 3)
    assert_points("FW", 90, 0, 0, 0, 0, 0, 0, 0, 0, 3)

# ---------- Goals per position ----------
def test_goals_fw():
    # 1 goal = +6
    assert_points("FW", 90, 1, 0, 0, 0, 0, 0, 0, 0, 3 + 6)
    # 2 goals = +12
    assert_points("FW", 90, 2, 0, 0, 0, 0, 0, 0, 0, 3 + 12)

def test_goals_mf():
    assert_points("MF", 90, 1, 0, 0, 0, 0, 0, 0, 0, 3 + 8)

def test_goals_df():
    assert_points("DF", 90, 1, 0, 0, 0, 0, 0, 0, 0, 3 + 10 + 4)

def test_goals_gk():
    # GK goals not in spec, but keep existing logic (15 pts each)
    assert_points("GK", 90, 1, 0, 0, 0, 0, 0, 0, 0, 3 + 15 + 8)

# ---------- Assists ----------
def test_assists():
    # Assist = +4 each
    assert_points("FW", 90, 0, 1, 0, 0, 0, 0, 0, 0, 3 + 4)
    assert_points("MF", 90, 0, 2, 0, 0, 0, 0, 0, 0, 3 + 8)

# ---------- Clean sheets ----------
def test_clean_sheet_gk():
    # 60+ min, 0 conceded -> +8
    assert_points("GK", 90, 0, 0, 0, 0, 0, 0, 0, 0, 3 + 8)
    # <60 min -> no clean sheet (even if 0 conceded)
    assert_points("GK", 45, 0, 0, 0, 0, 0, 0, 0, 0, 1)  # only 1 min point
    # concedes 1 -> no clean sheet
    assert_points("GK", 90, 0, 0, 1, 0, 0, 0, 0, 0, 3 - 1)  # -1 for conceded

def test_clean_sheet_df():
    # 60+ min, 0 conceded -> +4
    assert_points("DF", 90, 0, 0, 0, 0, 0, 0, 0, 0, 3 + 4)
    # <60 min -> no clean sheet
    assert_points("DF", 45, 0, 0, 0, 0, 0, 0, 0, 0, 1)

def test_no_clean_sheet_for_mf_fw():
    # Midfielders and forwards do not get clean sheet points
    assert_points("MF", 90, 0, 0, 0, 0, 0, 0, 0, 0, 3)
    assert_points("FW", 90, 0, 0, 0, 0, 0, 0, 0, 0, 3)

# ---------- Goals conceded penalties ----------
def test_gk_goals_conceded():
    # -1 per goal
    assert_points("GK", 90, 0, 0, 1, 0, 0, 0, 0, 0, 3 - 1)
    assert_points("GK", 90, 0, 0, 3, 0, 0, 0, 0, 0, 3 - 3)

def test_df_goals_conceded():
    # 2+ goals -> -1 (once)
    assert_points("DF", 90, 0, 0, 2, 0, 0, 0, 0, 0, 3 - 1)  # clean sheet lost anyway
    # but if conceded 1 -> no penalty
    assert_points("DF", 90, 0, 0, 1, 0, 0, 0, 0, 0, 3)  # no clean sheet, no penalty
    # conceded 4 -> still -1 only
    assert_points("DF", 90, 0, 0, 4, 0, 0, 0, 0, 0, 3 - 1)

def test_df_goals_conceded_no_clean_sheet_interaction():
    # Conceded 2+ -> -1, regardless of clean sheet
    res = calculate_player_points("DF", 90, 0, 0, 2, 0, 0, 0, 0, 0)
    # base: minutes 3 + no clean sheet (goals_conceded>0) + DF conceded penalty -1
    assert res["base_points"] == 3 - 1
    assert res["final_points"] == 2

# ---------- Penalty saved (GK) ----------
def test_penalty_saved_gk():
    # +8 each
    assert_points("GK", 90, 0, 0, 0, 0, 0, 0, 0, 1, 3 + 8 + 8)  # clean sheet +8 too
    assert_points("GK", 90, 0, 0, 1, 0, 0, 0, 0, 2, 3 - 1 + 16)

def test_penalty_saved_non_gk():
    # No bonus for other positions
    assert_points("DF", 90, 0, 0, 0, 0, 0, 0, 0, 1, 3 + 4)  # only clean sheet, no penalty save

# ---------- Cards ----------
def test_yellow_card():
    assert_points("FW", 90, 0, 0, 0, 1, 0, 0, 0, 0, 3 - 1)
    assert_points("FW", 90, 0, 0, 0, 2, 0, 0, 0, 0, 3 - 2)

def test_red_card():
    assert_points("FW", 90, 0, 0, 0, 0, 1, 0, 0, 0, 3 - 3)
    assert_points("FW", 90, 0, 0, 0, 0, 2, 0, 0, 0, 3 - 6)

def test_yellow_and_red():
    assert_points("FW", 90, 0, 0, 0, 1, 1, 0, 0, 0, 3 - 1 - 3)

# ---------- Own goal & penalty missed ----------
def test_own_goal():
    assert_points("FW", 90, 0, 0, 0, 0, 0, 1, 0, 0, 3 - 2)
    assert_points("FW", 90, 0, 0, 0, 0, 0, 2, 0, 0, 3 - 4)

def test_penalty_missed():
    assert_points("FW", 90, 0, 0, 0, 0, 0, 0, 1, 0, 3 - 2)
    assert_points("FW", 90, 0, 0, 0, 0, 0, 0, 2, 0, 3 - 4)

# ---------- Hat trick bonus ----------
def test_hat_trick_bonus():
    # 3 goals = base goal points x3 + bonus 5
    # FW: 3*6 = 18 base + minutes 3 = 21 base, +5 bonus = 26 final
    assert_points("FW", 90, 3, 0, 0, 0, 0, 0, 0, 0, 3 + 18 + 5, expected_bonus=5)
    # 4 goals -> still only +5 once
    assert_points("FW", 90, 4, 0, 0, 0, 0, 0, 0, 0, 3 + 24 + 5, expected_bonus=5)
    # 2 goals -> no hat trick bonus
    res = calculate_player_points("FW", 90, 2, 0, 0, 0, 0, 0, 0, 0)
    assert res["bonus_points"] == 0

# ---------- Goal + assist combo bonus ----------
def test_combo_bonus():
    # 1 goal + 1 assist -> +2 bonus
    res = calculate_player_points("FW", 90, 1, 1, 0, 0, 0, 0, 0, 0)
    # base: minutes 3 + goal 6 + assist 4 = 13, bonus 2 -> final 15
    assert res["base_points"] == 3 + 6 + 4
    assert res["bonus_points"] == 2
    assert res["final_points"] == 3 + 6 + 4 + 2

    # Multiple goals+assists: still +2 only once
    res = calculate_player_points("FW", 90, 2, 2, 0, 0, 0, 0, 0, 0)
    assert res["bonus_points"] == 2

def test_hat_trick_and_combo():
    # 3 goals + 1 assist: hat trick bonus + combo bonus, both apply
    res = calculate_player_points("FW", 90, 3, 1, 0, 0, 0, 0, 0, 0)
    base_expected = 3 + 18 + 4  # minutes + goals + assist
    assert res["base_points"] == base_expected
    assert res["bonus_points"] == 5 + 2
    assert res["final_points"] == base_expected + 7

# ---------- Complex realistic scenarios ----------
def test_complex_gk():
    # 90 min, 0 goals, 1 assist, 2 conceded, 1 penalty saved, 1 yellow, clean sheet lost
    res = calculate_player_points("GK", 90, 0, 1, 2, 1, 0, 0, 0, 1)
    # base: minutes 3 + assist 4 - conceded 2 + penalty saved 8 - yellow 1 = 12
    # no clean sheet because conceded>0
    assert res["base_points"] == 3 + 4 - 2 + 8 - 1
    assert res["bonus_points"] == 0  # no hat trick, combo needs goal
    assert res["final_points"] == 12

def test_complex_df():
    # 90 min, 1 goal, 0 assists, 3 conceded (2+ -> -1), own goal, yellow
    res = calculate_player_points("DF", 90, 1, 0, 3, 1, 0, 1, 0, 0)
    # base: minutes 3 + goal 10 - DF conceded 1 - own goal 2 - yellow 1 = 9
    assert res["base_points"] == 3 + 10 - 1 - 2 - 1
    assert res["bonus_points"] == 0
    assert res["final_points"] == 9

def test_no_negative_final_points():
    # Very poor performance: own goal, red card, many concessions
    res = calculate_player_points("DF", 60, 0, 0, 5, 0, 1, 1, 0, 0)
    # minutes 3 - DF conceded 1 - red 3 - own goal 2 = -3
    assert res["final_points"] == -3

def test_apply_wildcard_multiplier():
    score = {"base_points": 10, "bonus_points": 2, "final_points": 12}
    # Non-wildcard: unchanged
    result = apply_wildcard_multiplier(score, is_wildcard=False)
    assert result == score
    # Wildcard: final_points doubled
    result = apply_wildcard_multiplier(score, is_wildcard=True)
    assert result["base_points"] == 10
    assert result["bonus_points"] == 2
    assert result["final_points"] == 24

def test_validate_wildcard_constraint():
    players = [{"is_x2_joker": False}, {"is_x2_joker": False}]
    assert validate_wildcard_constraint(players) is True

    players = [{"is_x2_joker": True}, {"is_x2_joker": False}]
    assert validate_wildcard_constraint(players) is True

    players = [{"is_x2_joker": True}, {"is_x2_joker": True}]
    assert validate_wildcard_constraint(players) is False

    players = []  # empty list is fine
    assert validate_wildcard_constraint(players) is True
