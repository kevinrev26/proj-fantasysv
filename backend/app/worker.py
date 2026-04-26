import sentry_sdk
import structlog
from celery import Celery
from celery.signals import setup_logging as celery_setup_logging
from sentry_sdk.integrations.celery import CeleryIntegration
from .config import settings
from .logger import setup_logging
from .models import FantasyPlayer, PlayerScore, TeamScore
from .scoring import calculate_player_points, apply_wildcard_multiplier, validate_wildcard_constraint

@celery_setup_logging.connect
def on_celery_setup_logging(**kwargs):
    setup_logging()

logger = structlog.get_logger()

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        integrations=[CeleryIntegration()],
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
    )

celery_app = Celery(
    "worker",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.worker"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=settings.BROKER_CONNECTION_RETRY_ON_STARTUP,
)

@celery_app.task(name="add_numbers")
def add_numbers_task(a: int, b: int) -> int:
    logger.info("Adding numbers", a=a, b=b)
    return a + b


def score_matchday_for_fantasy_team(matchday_id: int, fantasy_team_id: int, db_session):
    # 1. Fetch all FantasyPlayer records for this fantasy team and matchday
    fantasy_players = db_session.query(FantasyPlayer).filter(
        FantasyPlayer.fantasy_team_id == fantasy_team_id,
        FantasyPlayer.matchday_id == matchday_id  # you may need a matchday_id in FantasyPlayer
    ).all()

    # 2. Validate wildcard constraint
    if not validate_wildcard_constraint([{"is_x2_joker": fp.is_x2_joker} for fp in fantasy_players]):
        raise ValueError(f"Fantasy team {fantasy_team_id} has more than one wildcard for matchday {matchday_id}")

    total_team_points = 0

    for fp in fantasy_players:
        # 3. Get raw stats for the real player from PlayerScore (already computed)
        player_stats = db_session.query(PlayerScore).filter(
            PlayerScore.player_id == fp.player_id,
            PlayerScore.matchday_id == matchday_id
        ).first()

        if not player_stats:
            continue  # or default zeros

        # 4. Compute base + bonus using existing pure function
        raw = calculate_player_points(
            position=fp.player.position,
            minutes_played=player_stats.minutes_played,
            goals=player_stats.goals,
            assists=player_stats.assists,
            goals_conceded=player_stats.goals_conceded,  # you need this field in PlayerScore
            yellow_cards=player_stats.yellow_card,
            red_cards=player_stats.red_card,
            own_goals=player_stats.own_goal,
            penalties_missed=player_stats.penalty_missed,
            penalties_saved=player_stats.penalty_saved
        )

        # 5. Apply wildcard multiplier
        scored = apply_wildcard_multiplier(raw, fp.is_x2_joker)

        # 6. Store in PlayerScore? Actually PlayerScore is for real player stats.
        #    For fantasy team scoring, we store in TeamScore.
        #    But we need to store the wildcard‑affected final_points somewhere.
        #    The spec says "Result is stored as final_points in PlayerScore" – that seems incorrect
        #    because PlayerScore belongs to the real player, not the fantasy team.
        #    I think the intention is to store in TeamScore (or a separate FantasyPlayerScore table).
        #    Let's assume we have a FantasyPlayerScore table that records per‑matchday points for each fantasy player.

        # For now, accumulate team total
        total_team_points += scored["final_points"]

    # 7. Update or create TeamScore for this matchday
    team_score = db_session.query(TeamScore).filter(
        TeamScore.fantasy_team_id == fantasy_team_id,
        TeamScore.matchday_id == matchday_id
    ).first()
    if not team_score:
        team_score = TeamScore(
            fantasy_team_id=fantasy_team_id,
            matchday_id=matchday_id,
            points_this_matchday=total_team_points,
            cumulative_points=0  # compute later
        )
    else:
        team_score.points_this_matchday = total_team_points

    db_session.add(team_score)
    db_session.commit()