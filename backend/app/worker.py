import sentry_sdk
import structlog
from celery import Celery
from celery.signals import setup_logging as celery_setup_logging
from sentry_sdk.integrations.celery import CeleryIntegration
from .config import settings
from .logger import setup_logging

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
