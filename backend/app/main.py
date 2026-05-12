import time
import uuid
import structlog
import sentry_sdk
import redis
from fastapi import FastAPI, Depends, Request, Response
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

from .routers import auth, squad, admin
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Fantasy Football API")
logger.info("FastAPI application initialized")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(squad.router)
app.include_router(admin.router)

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
