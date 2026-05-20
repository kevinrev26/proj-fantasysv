import time
import uuid
import structlog
import sentry_sdk
import redis
from fastapi import FastAPI, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from .database import get_db, Base, engine
from .worker import add_numbers_task
from .config import settings
from .logger import setup_logging

setup_logging()
logger = structlog.get_logger()

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
    )

from .routers import auth, squad, admin, league
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Fantasy Football API")
logger.info("FastAPI application initialized")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(squad.router)
app.include_router(admin.router)
app.include_router(league.router)


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id=request_id,
        method=request.method,
        path=request.url.path,
    )
    
    start_time = time.time()
    try:
        response = await call_next(request)
        process_time = time.time() - start_time
        logger.info(
            "Request completed",
            status_code=response.status_code,
            process_time=f"{process_time:.4f}s"
        )
        return response
    except Exception as e:
        process_time = time.time() - start_time
        logger.exception(
            "Request failed",
            process_time=f"{process_time:.4f}s",
            error=str(e)
        )
        raise

from . import models

@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    logger.debug("Health check endpoint called")
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
        logger.debug("Database health check passed")
    except Exception as e:
        logger.error("DB health check failed", error=str(e))
        db_status = "error"
        
    redis_status = "ok"
    try:
        r = redis.from_url(settings.REDIS_URL)
        r.ping()
        logger.debug("Redis health check passed")
    except Exception as e:
        logger.error("Redis health check failed", error=str(e))
        redis_status = "error"
        
    status = "ok" if db_status == "ok" and redis_status == "ok" else "error"
    logger.info("Health check completed", status=status, db=db_status, redis=redis_status)
    return {"status": status, "db": db_status, "redis": redis_status}

@app.get("/test-celery")
def test_celery(a: int = 1, b: int = 2):
    logger.info("Testing Celery task", a=a, b=b)
    task = add_numbers_task.delay(a, b)
    return {"task_id": task.id}

@app.get("/matchday/{matchday_id}/fixtures")
def get_fixtures(matchday_id: int, db: Session = Depends(get_db)):
    """Return all fixtures for a matchday."""
    matchday = db.query(models.Matchday).filter(models.Matchday.id == matchday_id).first()
    if not matchday:
        raise HTTPException(status_code=404, detail="Matchday not found")
    return {
        "fixtures": [
            {
                "id": f.id,
                "matchday_id": f.matchday_id,
                "home_team_id": f.home_team_id,
                "away_team_id": f.away_team_id,
                "kickoff_utc": f.kickoff_utc.isoformat(),
                "finished": f.finished,
            }
            for f in matchday.fixtures
        ]
    }

@app.get("/seasons")
def get_seasons(db: Session = Depends(get_db)):
    seasons = db.query(models.Season).all()
    return {"seasons": seasons}

@app.get("/seasons/{season_id}/leagues")
def get_leagues(season_id: int, db: Session = Depends(get_db)):
    leagues = db.query(models.League).filter(models.League.season_id == season_id).all()
    return {"leagues": leagues}

@app.get("/seasons/{season_id}/matchdays")
def get_matchdays(season_id: int, db: Session = Depends(get_db)):
    mds = db.query(models.Matchday).filter(
        models.Matchday.season_id == season_id
    ).order_by(models.Matchday.id).all()
    return {"matchdays": [
        {
            "id": md.id,
            "name": md.name,
            "season_id": md.season_id,
            "status": md.status.value,
            "is_locked": md.is_locked,
            "lock_at_utc": md.lock_at_utc.isoformat() if md.lock_at_utc else None,
            "locked_at": md.locked_at.isoformat() if md.locked_at else None,
            "task_status": md.task_status,
            "fixture_count": len(md.fixtures),
        }
        for md in mds
    ]}

@app.get("/seasons/{season_id}/teams")
def get_teams_for_season(season_id: int, db: Session = Depends(get_db)):
    """Return all teams belonging to any league in a season (for fixture dropdowns)."""
    teams = (
        db.query(models.Team)
        .join(models.League, models.Team.league_id == models.League.id)
        .filter(models.League.season_id == season_id)
        .order_by(models.Team.name)
        .all()
    )
    return {"teams": [{"id": t.id, "name": t.name, "league_id": t.league_id} for t in teams]}

@app.get("/matchday/{matchday_id}", status_code=200)
def get_matchday(matchday_id: int, db: Session = Depends(get_db)):
    """Return a single matchday with its fixtures and lock state."""
    md = db.query(models.Matchday).filter(models.Matchday.id == matchday_id).first()
    if not md:
        raise HTTPException(status_code=404, detail="Matchday not found")
    return {
        "id": md.id,
        "name": md.name,
        "season_id": md.season_id,
        "status": md.status.value,
        "is_locked": md.is_locked,
        "lock_at_utc": md.lock_at_utc.isoformat() if md.lock_at_utc else None,
        "locked_at": md.locked_at.isoformat() if md.locked_at else None,
        "task_status": md.task_status,
        "fixture_count": len(md.fixtures),
    }

# New endpoint to retrieve FixtureResult
@app.get("/matchday/{matchday_id}/fixture/{fixture_id}/result")
def get_fixture_result(matchday_id: int, fixture_id: int, db: Session = Depends(get_db)):
    """Return the FixtureResult for a given matchday and fixture."""
    fixture_result = db.query(models.FixtureResult).filter(
        models.FixtureResult.fixture_id == fixture_id
    ).first()
    if not fixture_result:
        raise HTTPException(status_code=404, detail="FixtureResult not found")
    return {
        "id": fixture_result.id,
        "fixture_id": fixture_result.fixture_id,
        "home_goals": fixture_result.home_goals,
        "away_goals": fixture_result.away_goals,
        "extra_time_played": fixture_result.extra_time_played,
        "home_extra_goals": fixture_result.home_extra_goals,
        "away_extra_goals": fixture_result.away_extra_goals,
        "penalty_shootout": fixture_result.penalty_shootout,
        "home_penalties": fixture_result.home_penalties,
        "away_penalties": fixture_result.away_penalties,
        "winner_team_id": fixture_result.winner_team_id,
        "verified_at": fixture_result.verified_at.isoformat() if fixture_result.verified_at else None,
        "source": fixture_result.source,
    }
