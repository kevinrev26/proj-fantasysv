from typing import List

import sentry_sdk
import structlog
from celery import Celery
from celery.signals import setup_logging as celery_setup_logging
from sentry_sdk.integrations.celery import CeleryIntegration

from .config import settings
from .database import SessionLocal
from .logger import setup_logging
from .models import (
    FantasyPlayer,
    FantasyTeam,
    Matchday,
    Player,
    PlayerScore,
    TeamScore,
    LeaderboardWeeklyEntry,
)
from .scoring import (
    apply_wildcard_multiplier,
    calculate_player_points,
    validate_wildcard_constraint,
)

logger = structlog.get_logger()

@celery_setup_logging.connect
def on_celery_setup_logging(**kwargs):
    setup_logging()


# Ensure the broker URL explicitly uses the redis:// scheme so kombu never
# falls back to pyamqp when the Redis transport has a transient hiccup.
def _redis_url(url: str) -> str:
    """Guarantee the URL starts with redis:// (not amqp or anything else)."""
    if not url.startswith("redis://") and not url.startswith("rediss://"):
        raise RuntimeError(
            f"REDIS_URL must start with redis:// or rediss://, got: {url!r}"
        )
    return url


celery_app = Celery(
    "worker",
    broker=_redis_url(settings.REDIS_URL),
    backend=_redis_url(settings.REDIS_URL),
    include=["app.worker"],
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
    logger.info("Starting matchday scoring for fantasy team", 
                matchday_id=matchday_id, 
                fantasy_team_id=fantasy_team_id)
    # 1. Fetch all FantasyPlayer records for this fantasy team and matchday
    fantasy_players = (
        db_session.query(FantasyPlayer)
        .filter(
            FantasyPlayer.fantasy_team_id == fantasy_team_id,
            FantasyPlayer.matchday_id
            == matchday_id,  # you may need a matchday_id in FantasyPlayer
        )
        .all()
    )
    logger.debug("Fetched fantasy players", count=len(fantasy_players))

    # 2. Validate wildcard constraint
    if not validate_wildcard_constraint(
        [{"is_x2_joker": fp.is_x2_joker} for fp in fantasy_players]
    ):
        error_msg = f"Fantasy team {fantasy_team_id} has more than one wildcard for matchday {matchday_id}"
        logger.error("Wildcard constraint violated", error=error_msg)
        raise ValueError(error_msg)

    total_team_points = 0

    for fp in fantasy_players:
        logger.debug("Processing fantasy player", fantasy_player_id=fp.id)
        # 3. Get raw stats for the real player from PlayerScore (already computed)
        player_stats = (
            db_session.query(PlayerScore)
            .filter(
                PlayerScore.player_id == fp.player_id,
                PlayerScore.matchday_id == matchday_id,
            )
            .first()
        )

        if not player_stats:
            logger.warning("No player stats found", player_id=fp.player_id, matchday_id=matchday_id)
            continue  # or default zeros

        # 4. Compute base + bonus using existing pure function
        raw = calculate_player_points(
            position=fp.player.position.value,
            minutes_played=player_stats.minutes_played,
            goals=player_stats.goals,
            assists=player_stats.assists,
            goals_conceded=player_stats.goals_conceded,  # you need this field in PlayerScore
            yellow_cards=player_stats.yellow_card,
            red_cards=player_stats.red_card,
            own_goals=player_stats.own_goal,
            penalties_missed=player_stats.penalty_missed,
            penalties_saved=player_stats.penalties_saved,
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
        logger.debug("Player scored", player_id=fp.player_id, final_points=scored["final_points"])

    # 7. Update or create TeamScore for this matchday
    team_score = (
        db_session.query(TeamScore)
        .filter(
            TeamScore.fantasy_team_id == fantasy_team_id,
            TeamScore.matchday_id == matchday_id,
        )
        .first()
    )
    if not team_score:
        team_score = TeamScore(
            fantasy_team_id=fantasy_team_id,
            matchday_id=matchday_id,
            points_this_matchday=total_team_points,
            cumulative_points=0,  # compute later
        )
        logger.info("Creating new team score", team_score_id=team_score.id)
    else:
        logger.info("Updating existing team score", team_score_id=team_score.id)

    team_score.points_this_matchday = total_team_points
    db_session.add(team_score)
    try:
        db_session.commit()
        logger.info("Matchday scoring completed successfully", 
                   matchday_id=matchday_id, 
                   fantasy_team_id=fantasy_team_id,
                   total_points=total_team_points)
    except Exception as e:
        logger.error("Failed to commit team score", 
                    matchday_id=matchday_id, 
                    fantasy_team_id=fantasy_team_id, 
                    error=str(e))
        db_session.rollback()
        raise


@celery_app.task(
    name="recalculate_matchday_scores",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def recalculate_matchday_scores_task(self, matchday_id: int):
    logger.info("Starting matchday score recalculation", matchday_id=matchday_id)
    db = SessionLocal()
    try:
        # Update task status to processing
        matchday = db.query(Matchday).filter(Matchday.id == matchday_id).first()
        if not matchday:
            logger.error("Matchday not found", matchday_id=matchday_id)
            return
        matchday.task_status = "processing"
        db.commit()

        # 1. Get all PlayerScore records for this matchday
        player_scores = (
            db.query(PlayerScore).filter(PlayerScore.matchday_id == matchday_id).all()
        )
        logger.debug("Retrieved player scores", count=len(player_scores))

        if not player_scores:
            matchday.task_status = "done"  # nothing to score
            db.commit()
            logger.info("No player scores to process, marking matchday as done", matchday_id=matchday_id)
            return

        player_ids = [ps.player_id for ps in player_scores]

        # 2. Find all FantasyTeams that have any of these players
        #    (through FantasyPlayer -> Player)
        fantasy_teams = (
            db.query(FantasyTeam)
            .join(FantasyPlayer, FantasyPlayer.fantasy_team_id == FantasyTeam.id)
            .join(Player, Player.id == FantasyPlayer.player_id)
            .filter(
                Player.id.in_(player_ids), FantasyTeam.season_id == matchday.season_id
            )
            .distinct()
            .all()
        )
        logger.debug("Retrieved fantasy teams", count=len(fantasy_teams))

        # 3. For each fantasy team, compute TeamScore
        for fantasy_team in fantasy_teams:
            logger.debug("Processing fantasy team", fantasy_team_id=fantasy_team.id)
            total_points = 0

            # Get all fantasy players of this team (for the season)
            fantasy_players = (
                db.query(FantasyPlayer)
                .filter(FantasyPlayer.fantasy_team_id == fantasy_team.id)
                .all()
            )

            for fp in fantasy_players:
                if not fp.player.is_active:
                    logger.debug("Player is inactive, skipping", player_id=fp.player_id)
                    continue
                # Find the corresponding PlayerScore for this player and matchday
                ps = next(
                    (p for p in player_scores if p.player_id == fp.player_id), None
                )
                if not ps:
                    logger.debug("No player score found, skipping", player_id=fp.player_id)
                    continue  # player didn't play, no points

                # Get raw points from real player's stats
                # fp.player.position is a PlayerPosition enum — scoring expects a str
                raw = calculate_player_points(
                    position=fp.player.position.value,
                    minutes_played=ps.minutes_played,
                    goals=ps.goals,
                    assists=ps.assists,
                    goals_conceded=ps.goals_conceded,
                    yellow_cards=ps.yellow_card,
                    red_cards=ps.red_card,
                    own_goals=ps.own_goal,
                    penalties_missed=ps.penalty_missed,
                    penalties_saved=ps.penalty_saved,
                )

                # Apply wildcard multiplier (if this fantasy player is the joker)
                # Note: This assumes is_x2_joker is set per matchday.
                # If you need per‑matchday selection, you'll need a separate table.
                scored = apply_wildcard_multiplier(raw, fp.is_x2_joker)

                total_points += scored["final_points"]
                logger.debug("Player scored for team", 
                            player_id=fp.player_id, 
                            team_id=fantasy_team.id, 
                            points=scored["final_points"])

            # Update or create TeamScore
            team_score = (
                db.query(TeamScore)
                .filter(
                    TeamScore.fantasy_team_id == fantasy_team.id,
                    TeamScore.matchday_id == matchday_id,
                )
                .first()
            )
            if not team_score:
                team_score = TeamScore(
                    fantasy_team_id=fantasy_team.id, matchday_id=matchday_id
                )
                logger.info("Creating new team score", team_score_id=team_score.id)

            team_score.points_this_matchday = total_points
            # Cumulative points: we could compute by summing previous + current
            previous_total = (
                db.query(TeamScore)
                .filter(
                    TeamScore.fantasy_team_id == fantasy_team.id,
                    TeamScore.matchday_id < matchday_id,
                )
                .with_entities(TeamScore.cumulative_points)
                .order_by(TeamScore.matchday_id.desc())
                .first()
            )
            if previous_total:
                team_score.cumulative_points = previous_total[0] + total_points
            else:
                team_score.cumulative_points = total_points

            leaderboard_entry = LeaderboardWeeklyEntry(
                fantasy_team_id = fantasy_team.id,
                total_points = total_points,
            )
            db.add(leaderboard_entry, team_score)
            logger.debug("Team score updated", team_score_id=team_score.id, points=total_points)

        # Removing closing matchday state here, because we are keep updating the scores per fixtures.
        # So matchday is not closed until we manually close from admin dashboard UI.

        matchday.task_status = "done"
        db.commit()
        logger.info("Matchday score recalculation completed successfully", matchday_id=matchday_id)

    except Exception as e:
        logger.error("Matchday score recalculation failed", matchday_id=matchday_id, error=str(e))
        db.commit()
        # Retry the task
        raise self.retry(exc=e)
    finally:
        db.close()
        logger.debug("Database session closed for recalculate_matchday_scores_task")


@celery_app.task(
    name="deactivate_players_for_teams",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def deactivate_players_for_teams_task(self, team_ids: List[int]):
    logger.info("Starting player deactivation for teams", team_ids=team_ids)
    db = SessionLocal()
    try:
        # Atomic bulk update
        updated_count = db.query(Player).filter(Player.team_id.in_(team_ids)).update(
            {Player.is_active: False}, synchronize_session=False
        )
        db.commit()
        logger.info("Player deactivation completed", team_ids=team_ids, updated_count=updated_count)
    except Exception as e:
        logger.error("Player deactivation failed", team_ids=team_ids, error=str(e))
        db.rollback()
        raise self.retry(exc=e)
    finally:
        db.close()
        logger.debug("Database session closed for deactivate_players_for_teams_task")
    return {"deactivated_teams": team_ids}


@celery_app.task(
    name="reactivate_players_for_teams",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def reactivate_players_for_teams_task(self, team_ids: List[int]):
    logger.info("Starting player reactivation for teams", team_ids=team_ids)
    db = SessionLocal()
    try:
        db.query(Player).filter(Player.team_id.in_(team_ids)).update(
            {Player.is_active: True}, synchronize_session=False
        )
        db.commit()
        logger.info("Player reactivation completed", team_ids=team_ids)
    except Exception as e:
        logger.error("Player reactivation failed", team_ids=team_ids, error=str(e))
        db.rollback()
        raise self.retry(exc=e)
    finally:
        db.close()
        logger.debug("Database session closed for reactivate_players_for_teams_task")
    return {"reactivated_teams": team_ids}
