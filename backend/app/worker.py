import sentry_sdk
import structlog
from celery import Celery, shared_task
from celery.signals import setup_logging as celery_setup_logging
from sentry_sdk.integrations.celery import CeleryIntegration
from .database import SessionLocal
from .config import settings
from .logger import setup_logging
from .models import FantasyPlayer, PlayerScore, TeamScore, Matchday, MatchdayStatus, FantasyTeam, Player
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

@shared_task(
    name="recalculate_matchday_scores",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def recalculate_matchday_scores_task(self, matchday_id: int):
    db = SessionLocal()
    try:
        # Update task status to processing
        matchday = db.query(Matchday).filter(Matchday.id == matchday_id).first()
        if not matchday:
            return
        matchday.task_status = "processing"
        db.commit()
        
        # 1. Get all PlayerScore records for this matchday
        player_scores = db.query(PlayerScore).filter(
            PlayerScore.matchday_id == matchday_id
        ).all()
        if not player_scores:
            matchday.task_status = "done"  # nothing to score
            db.commit()
            return
        
        player_ids = [ps.player_id for ps in player_scores]
        
        # 2. Find all FantasyTeams that have any of these players
        #    (through FantasyPlayer -> Player)
        fantasy_teams = db.query(FantasyTeam).join(
            FantasyPlayer, FantasyPlayer.fantasy_team_id == FantasyTeam.id
        ).join(
            Player, Player.id == FantasyPlayer.player_id
        ).filter(
            Player.id.in_(player_ids),
            FantasyTeam.season_id == matchday.season_id
        ).distinct().all()
        
        # 3. For each fantasy team, compute TeamScore
        for fantasy_team in fantasy_teams:
            total_points = 0
            
            # Get all fantasy players of this team (for the season)
            fantasy_players = db.query(FantasyPlayer).filter(
                FantasyPlayer.fantasy_team_id == fantasy_team.id
            ).all()
            
            for fp in fantasy_players:
                # Find the corresponding PlayerScore for this player and matchday
                ps = next((p for p in player_scores if p.player_id == fp.player_id), None)
                if not ps:
                    continue  # player didn't play, no points
                
                # Get raw points from real player's stats
                raw = calculate_player_points(
                    position=fp.player.position,
                    minutes_played=ps.minutes_played,
                    goals=ps.goals,
                    assists=ps.assists,
                    goals_conceded=ps.goals_conceded,
                    yellow_cards=ps.yellow_card,
                    red_cards=ps.red_card,
                    own_goals=ps.own_goal,
                    penalties_missed=ps.penalty_missed,
                    penalties_saved=ps.penalty_saved
                )
                
                # Apply wildcard multiplier (if this fantasy player is the joker)
                # Note: This assumes is_x2_joker is set per matchday. 
                # If you need per‑matchday selection, you'll need a separate table.
                scored = apply_wildcard_multiplier(raw, fp.is_x2_joker)
                
                total_points += scored["final_points"]
                
                # Optional: store per‑player fantasy points in a new table for audit
                # For now we only keep team total.
            
            # Update or create TeamScore
            team_score = db.query(TeamScore).filter(
                TeamScore.fantasy_team_id == fantasy_team.id,
                TeamScore.matchday_id == matchday_id
            ).first()
            if not team_score:
                team_score = TeamScore(
                    fantasy_team_id=fantasy_team.id,
                    matchday_id=matchday_id
                )
            team_score.points_this_matchday = total_points
            # Cumulative points: we could compute by summing previous + current
            previous_total = db.query(TeamScore).filter(
                TeamScore.fantasy_team_id == fantasy_team.id,
                TeamScore.matchday_id < matchday_id
            ).with_entities(TeamScore.cumulative_points).order_by(
                TeamScore.matchday_id.desc()
            ).first()
            if previous_total:
                team_score.cumulative_points = previous_total[0] + total_points
            else:
                team_score.cumulative_points = total_points
            
            db.add(team_score)
        
        # 4. Mark matchday as closed (all matches processed)
        matchday.status = MatchdayStatus.closed
        matchday.task_status = "done"
        db.commit()
        
    except Exception as e:
        matchday.task_status = "failed"
        db.commit()
        # Retry the task
        raise self.retry(exc=e)
    finally:
        db.close()
