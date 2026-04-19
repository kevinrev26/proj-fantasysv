from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from .database import get_db, Base, engine
from .worker import add_numbers_task
from .config import settings

app = FastAPI(title="Fantasy Football API")

@app.on_event("startup")
def init_db():
    Base.metadata.create_all(bind=engine)

@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}

@app.get("/test-celery")
def test_celery(a: int = 1, b: int = 2):
    task = add_numbers_task.delay(a, b)
    return {"task_id": task.id}
