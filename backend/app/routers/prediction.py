# prediction.py
from datetime import datetime, timezone
from typing import List, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, asc, desc, func, or_
from sqlalchemy.orm import Session, joinedload

from .. import models
from ..database import get_db
from ..dependencies import get_current_user

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/prediction", tags=["Prediction Scores"])

# ---------------------------------------------------------------------------
# Pydantic schemas — request bodies
# ---------------------------------------------------------------------------


class PredictionCreate(BaseModel):
    fixture_id: int
    predicted_home_goals: int = Field(ge=0)
    predicted_away_goals: int = Field(ge=0)
    predicted_extra_time_home_goals: int = Field(default=0, ge=0)
    predicted_extra_time_away_goals: int = Field(default=0, ge=0)
    predicted_penalty_home_goals: int = Field(default=0, ge=0)
    predicted_penalty_away_goals: int = Field(default=0, ge=0)
    is_joker: bool = False


class PredictionUpdate(BaseModel):
    predicted_home_goals: Optional[int] = Field(None, ge=0)
    predicted_away_goals: Optional[int] = Field(None, ge=0)
    predicted_extra_time_home_goals: Optional[int] = Field(None, ge=0)
    predicted_extra_time_away_goals: Optional[int] = Field(None, ge=0)
    predicted_penalty_home_goals: Optional[int] = Field(None, ge=0)
    predicted_penalty_away_goals: Optional[int] = Field(None, ge=0)
    is_joker: Optional[bool] = None


class PredictionBatchItem(BaseModel):
    # same fields as PredictionCreate but without fixture_id? Actually fixture_id is required
    fixture_id: int
    predicted_home_goals: int = Field(ge=0)
    predicted_away_goals: int = Field(ge=0)
    predicted_extra_time_home_goals: int = Field(default=0, ge=0)
    predicted_extra_time_away_goals: int = Field(default=0, ge=0)
    predicted_penalty_home_goals: int = Field(default=0, ge=0)
    predicted_penalty_away_goals: int = Field(default=0, ge=0)
    is_joker: bool = False


class PredictionBatchRequest(BaseModel):
    predictions: List[PredictionBatchItem]


# ---------------------------------------------------------------------------
# Pydantic schemas — nested response objects
# ---------------------------------------------------------------------------


class FixtureResultResponse(BaseModel):
    """Final score and extra-time / penalty details for a finished fixture."""

    home_goals: int
    away_goals: int
    extra_time_played: bool
    home_extra_goals: int
    away_extra_goals: int
    penalty_shootout: bool
    home_penalties: int
    away_penalties: int
    winner: Optional[str]  # "home" | "away" | None (draw)

    class Config:
        from_attributes = True


class PredictionScoreInline(BaseModel):
    """Points breakdown embedded directly in a prediction response."""

    points_earned: int
    exact_score_points: int
    correct_outcome_points: int
    joker_multiplier_applied: bool
    calculated_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Pydantic schemas — response models
# ---------------------------------------------------------------------------


class PredictionResponse(BaseModel):
    """
    Standard prediction response used by POST / PATCH / batch endpoints and
    by FixtureForPrediction.  Includes fixture result and score when available
    so the frontend never needs a second request.
    """

    id: int
    fixture_id: int
    predicted_home_goals: int
    predicted_away_goals: int
    is_joker: bool
    created_at: datetime
    updated_at: datetime
    # Fixture info
    matchday_id: int
    matchday_name: str
    fixture_home_team: str
    fixture_away_team: str
    kickoff_utc: datetime
    fixture_finished: bool
    # Populated once the fixture has a result
    fixture_result: Optional[FixtureResultResponse] = None
    # Populated once the scoring worker has run
    score: Optional[PredictionScoreInline] = None
    predicted_extra_time_home_goals: int
    predicted_extra_time_away_goals: int
    predicted_penalty_home_goals: int
    predicted_penalty_away_goals: int

    class Config:
        from_attributes = True


class PredictionDetailResponse(BaseModel):
    """
    Rich prediction response returned by GET /prediction/ (history tab).

    Every prediction includes:
      - result   : fixture final score, extra-time, penalties, and computed
                   winner.  null until admin enters the result.
      - score    : points breakdown (exact_score_points, correct_outcome_points,
                   joker_multiplier_applied, points_earned).  null until the
                   scoring worker runs after the fixture result is available.
      - fixture_finished : whether the fixture is marked finished in the DB,
                   useful for distinguishing "not played yet" from "played but
                   result not entered".
    """

    id: int
    fixture_id: int
    predicted_home_goals: int
    predicted_away_goals: int
    is_joker: bool
    created_at: datetime
    updated_at: datetime
    # Fixture info
    matchday_id: int
    matchday_name: str
    fixture_home_team: str
    fixture_away_team: str
    kickoff_utc: datetime
    fixture_finished: bool
    # Enriched data — null when not yet available
    result: Optional[FixtureResultResponse] = None
    score: Optional[PredictionScoreInline] = None
    predicted_extra_time_home_goals: int
    predicted_extra_time_away_goals: int
    predicted_penalty_home_goals: int
    predicted_penalty_away_goals: int

    class Config:
        from_attributes = True


class PredictionScoreResponse(BaseModel):
    """Standalone score response (kept for backward-compat / admin use)."""

    prediction_id: int
    points_earned: int
    exact_score_points: int
    correct_outcome_points: int
    joker_multiplier_applied: bool


class LeaderboardEntry(BaseModel):
    user_id: int
    username: str
    matchday_id: int
    matchday_name: str
    total_points: int


class TotalLeaderboardEntry(BaseModel):
    user_id: int
    username: str
    total_points: int
    rank: Optional[int] = None


class MatchdaySummary(BaseModel):
    matchday_id: int
    matchday_name: str
    total_points: int
    joker_used: bool
    joker_applied_to_fixture_id: Optional[int]
    predictions_made: int
    predictions_scored: int  # fixtures that already have a result


class FixtureForPrediction(BaseModel):
    fixture_id: int
    home_team: str
    away_team: str
    kickoff_utc: datetime
    matchday_id: int
    matchday_name: str
    is_knockout: bool
    existing_prediction: Optional[PredictionResponse] = None


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def check_prediction_deadline(
    db: Session, fixture_id: int, raise_exception: bool = True
) -> bool:
    """Return True if prediction can be made/updated (kickoff not passed)."""
    fixture = db.query(models.Fixture).filter(models.Fixture.id == fixture_id).first()
    if not fixture:
        if raise_exception:
            raise HTTPException(status_code=404, detail="Fixture not found")
        return False
    if fixture.kickoff_utc <= datetime.now(timezone.utc):
        if raise_exception:
            raise HTTPException(
                status_code=400,
                detail=f"Prediction deadline passed (kickoff at {fixture.kickoff_utc})",
            )
        return False
    return True


def validate_joker_usage(
    db: Session,
    user_id: int,
    matchday_id: int,
    fixture_id: int,
    is_joker: bool,
    exclude_prediction_id: Optional[int] = None,
) -> None:
    """Ensure user has at most one prediction with is_joker=True per matchday."""
    if not is_joker:
        return

    query = (
        db.query(models.Prediction)
        .join(models.Fixture, models.Prediction.fixture_id == models.Fixture.id)
        .filter(
            models.Prediction.user_id == user_id,
            models.Fixture.matchday_id == matchday_id,
            models.Prediction.is_joker == True,
        )
    )
    if exclude_prediction_id:
        query = query.filter(models.Prediction.id != exclude_prediction_id)

    existing_joker = query.first()
    if existing_joker:
        raise HTTPException(
            status_code=400,
            detail=f"Joker already used for matchday {matchday_id} on fixture {existing_joker.fixture_id}",
        )


def update_matchday_stats(db: Session, user_id: int, matchday_id: int) -> None:
    """Recalculate total points and joker usage for a user on a matchday."""
    predictions = (
        db.query(models.Prediction)
        .join(models.Fixture)
        .filter(
            models.Prediction.user_id == user_id,
            models.Fixture.matchday_id == matchday_id,
        )
        .options(joinedload(models.Prediction.score))
        .all()
    )

    total_points = sum((p.score.points_earned if p.score else 0) for p in predictions)
    joker_pred = next((p for p in predictions if p.is_joker), None)
    joker_used = joker_pred is not None
    joker_fixture_id = joker_pred.fixture_id if joker_pred else None

    stats = (
        db.query(models.PredictionMatchdayStats)
        .filter(
            models.PredictionMatchdayStats.user_id == user_id,
            models.PredictionMatchdayStats.matchday_id == matchday_id,
        )
        .first()
    )

    if stats:
        stats.total_points = total_points
        stats.joker_used = joker_used
        stats.joker_applied_to_fixture_id = joker_fixture_id
        stats.updated_at = datetime.utcnow()
    else:
        stats = models.PredictionMatchdayStats(
            user_id=user_id,
            matchday_id=matchday_id,
            total_points=total_points,
            joker_used=joker_used,
            joker_applied_to_fixture_id=joker_fixture_id,
        )
        db.add(stats)
    db.commit()


def get_fixture_matchday(db: Session, fixture_id: int) -> int:
    """Return matchday_id for a fixture."""
    fixture = db.query(models.Fixture).filter(models.Fixture.id == fixture_id).first()
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")
    return fixture.matchday_id


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/", response_model=PredictionResponse, status_code=status.HTTP_201_CREATED
)
def create_prediction(
    prediction_in: PredictionCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Create a single prediction for a fixture."""
    check_prediction_deadline(db, prediction_in.fixture_id)
    existing = (
        db.query(models.Prediction)
        .filter(
            models.Prediction.user_id == current_user.id,
            models.Prediction.fixture_id == prediction_in.fixture_id,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail="Prediction already exists for this fixture. Use PATCH to update.",
        )
    matchday_id = get_fixture_matchday(db, prediction_in.fixture_id)
    validate_joker_usage(
        db,
        current_user.id,
        matchday_id,
        prediction_in.fixture_id,
        prediction_in.is_joker,
    )

    new_pred = models.Prediction(
        user_id=current_user.id,
        fixture_id=prediction_in.fixture_id,
        predicted_home_goals=prediction_in.predicted_home_goals,
        predicted_away_goals=prediction_in.predicted_away_goals,
        predicted_extra_time_home_goals=prediction_in.predicted_extra_time_home_goals,
        predicted_extra_time_away_goals=prediction_in.predicted_extra_time_away_goals,
        predicted_penalty_home_goals=prediction_in.predicted_penalty_home_goals,
        predicted_penalty_away_goals=prediction_in.predicted_penalty_away_goals,
        is_joker=prediction_in.is_joker,
    )
    db.add(new_pred)
    db.commit()
    db.refresh(new_pred)
    return _build_prediction_response(db, new_pred)


@router.patch("/{prediction_id}", response_model=PredictionResponse)
def update_prediction(
    prediction_id: int,
    prediction_update: PredictionUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Update an existing prediction."""
    pred = (
        db.query(models.Prediction)
        .filter(
            models.Prediction.id == prediction_id,
            models.Prediction.user_id == current_user.id,
        )
        .first()
    )
    if not pred:
        raise HTTPException(status_code=404, detail="Prediction not found")

    check_prediction_deadline(db, pred.fixture_id)

    update_data = prediction_update.dict(exclude_unset=True)
    old_joker = pred.is_joker
    new_joker = update_data.get("is_joker", old_joker)

    if new_joker != old_joker:
        matchday_id = get_fixture_matchday(db, pred.fixture_id)
        validate_joker_usage(
            db,
            current_user.id,
            matchday_id,
            pred.fixture_id,
            new_joker,
            exclude_prediction_id=prediction_id,
        )

    for key, value in update_data.items():
        setattr(pred, key, value)
    pred.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(pred)
    return _build_prediction_response(db, pred)


@router.post("/batch", response_model=List[PredictionResponse])
def upsert_predictions_batch(
    batch: PredictionBatchRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Create or update multiple predictions in one request.
    For each item, if a prediction already exists for the user+fixture it is
    updated; otherwise it is created.  All changes are committed atomically.
    """
    responses = []
    joker_proposals: dict[int, int] = {}  # matchday_id -> fixture_id

    for item in batch.predictions:
        check_prediction_deadline(db, item.fixture_id)
        matchday_id = get_fixture_matchday(db, item.fixture_id)

        existing = (
            db.query(models.Prediction)
            .filter(
                models.Prediction.user_id == current_user.id,
                models.Prediction.fixture_id == item.fixture_id,
            )
            .first()
        )

        if existing:
            if item.is_joker and not existing.is_joker:
                joker_proposals[matchday_id] = item.fixture_id
            existing.predicted_home_goals = item.predicted_home_goals
            existing.predicted_away_goals = item.predicted_away_goals
            existing.predicted_extra_time_home_goals = (
                item.predicted_extra_time_home_goals
            )
            existing.predicted_extra_time_away_goals = (
                item.predicted_extra_time_away_goals
            )
            existing.predicted_penalty_home_goals = item.predicted_penalty_home_goals
            existing.predicted_penalty_away_goals = item.predicted_penalty_away_goals
            existing.is_joker = item.is_joker
            existing.updated_at = datetime.utcnow()
            responses.append(existing)
        else:
            new_pred = models.Prediction(
                user_id=current_user.id,
                fixture_id=item.fixture_id,
                predicted_home_goals=item.predicted_home_goals,
                predicted_away_goals=item.predicted_away_goals,
                predicted_extra_time_home_goals=item.predicted_extra_time_home_goals,
                predicted_extra_time_away_goals=item.predicted_extra_time_away_goals,
                predicted_penalty_home_goals=item.predicted_penalty_home_goals,
                predicted_penalty_away_goals=item.predicted_penalty_away_goals,
                is_joker=item.is_joker,
            )
            db.add(new_pred)
            responses.append(new_pred)
            if item.is_joker:
                joker_proposals[matchday_id] = item.fixture_id

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Batch commit failed: {str(e)}")

    final_responses = []
    for pred in responses:
        db.refresh(pred)
        final_responses.append(_build_prediction_response(db, pred))
    return final_responses


@router.get("/", response_model=List[PredictionDetailResponse])
def get_user_predictions(
    matchday_id: Optional[int] = Query(None, description="Filter by matchday ID"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Return all predictions for the current user, newest first.

    Optionally filter by ``matchday_id``.

    Each item includes:

    - **result** – ``FixtureResultResponse`` with the final score
      (home/away goals, extra-time details, penalties, computed winner).
      ``null`` until an admin enters the result.

    - **score** – ``PredictionScoreInline`` with the full points breakdown
      (``exact_score_points``, ``correct_outcome_points``,
      ``joker_multiplier_applied``, ``points_earned``, ``calculated_at``).
      ``null`` until the scoring worker runs after the result is available.

    - **fixture_finished** – ``true`` once the fixture is marked finished,
      useful to distinguish *not yet played* from *played but result pending*.
    """
    query = (
        db.query(models.Prediction)
        # 1. Join the fixture model so we can order by its attributes
        .join(models.Prediction.fixture)
        .filter(models.Prediction.user_id == current_user.id)
        # 2. Order by kickoff_utc ascending (closest chronological date first)
        .order_by(asc(models.Fixture.kickoff_utc))
        .options(
            joinedload(models.Prediction.fixture).joinedload(models.Fixture.matchday),
            joinedload(models.Prediction.fixture).joinedload(models.Fixture.home_team),
            joinedload(models.Prediction.fixture).joinedload(models.Fixture.away_team),
            joinedload(models.Prediction.fixture).joinedload(models.Fixture.result),
            joinedload(models.Prediction.score),
        )
    )
    if matchday_id:
        query = query.join(models.Fixture).filter(
            models.Fixture.matchday_id == matchday_id
        )

    predictions = query.order_by(models.Prediction.id.desc()).all()
    return [_build_prediction_detail_response(p) for p in predictions]


@router.get(
    "/leaderboard/matchday/{matchday_id}", response_model=List[LeaderboardEntry]
)
def get_leaderboard_by_matchday(
    matchday_id: int,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    """Leaderboard of total prediction points for a specific matchday."""
    rows = (
        db.query(
            models.PredictionMatchdayStats.user_id,
            models.User.username,
            models.PredictionMatchdayStats.total_points,
            models.Matchday.name.label("matchday_name"),
        )
        .join(models.User, models.User.id == models.PredictionMatchdayStats.user_id)
        .join(
            models.Matchday,
            models.Matchday.id == models.PredictionMatchdayStats.matchday_id,
        )
        .filter(models.PredictionMatchdayStats.matchday_id == matchday_id)
        .order_by(desc(models.PredictionMatchdayStats.total_points))
        .limit(limit)
        .all()
    )

    return [
        LeaderboardEntry(
            user_id=r.user_id,
            username=r.username,
            matchday_id=matchday_id,
            matchday_name=r.matchday_name,
            total_points=r.total_points,
        )
        for r in rows
    ]


@router.get("/leaderboard/total", response_model=List[TotalLeaderboardEntry])
def get_total_leaderboard(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    """Overall leaderboard summing prediction points across all matchdays."""
    total_points_subq = (
        db.query(
            models.PredictionMatchdayStats.user_id,
            func.sum(models.PredictionMatchdayStats.total_points).label("total_points"),
        )
        .group_by(models.PredictionMatchdayStats.user_id)
        .subquery()
    )

    results = (
        db.query(
            total_points_subq.c.user_id,
            models.User.username,
            total_points_subq.c.total_points,
        )
        .join(models.User, models.User.id == total_points_subq.c.user_id)
        .order_by(desc(total_points_subq.c.total_points))
        .limit(limit)
        .all()
    )

    return [
        TotalLeaderboardEntry(
            user_id=user_id,
            username=username,
            total_points=total_points,
            rank=idx,
        )
        for idx, (user_id, username, total_points) in enumerate(results, start=1)
    ]


@router.get("/fixtures", response_model=List[FixtureForPrediction])
def get_available_fixtures_for_prediction(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Returns:
    - Future fixtures (users can still make/edit predictions)
    - Past fixtures that already have a prediction from this user
    """

    logger.info(
        "Starting get_available_fixtures_for_prediction",
        user_id=current_user.id,
        username=current_user.username,
    )

    now = datetime.utcnow()

    fixtures = (
        db.query(models.Fixture)
        .outerjoin(
            models.Prediction,
            and_(
                models.Prediction.fixture_id == models.Fixture.id,
                models.Prediction.user_id == current_user.id,
            ),
        )
        .filter(
            or_(
                models.Fixture.kickoff_utc > now,
                models.Prediction.id.isnot(None),
            )
        )
        .options(
            joinedload(models.Fixture.matchday),
            joinedload(models.Fixture.home_team),
            joinedload(models.Fixture.away_team),
            joinedload(models.Fixture.result),
        )
        .order_by(models.Fixture.kickoff_utc.asc())
        .all()
    )

    fixture_ids = [f.id for f in fixtures]

    logger.info(
        "Loaded fixtures",
        fixture_count=len(fixtures),
        fixture_ids=fixture_ids,
    )

    existing_preds = {
        p.fixture_id: p
        for p in (
            db.query(models.Prediction)
            .filter(
                models.Prediction.user_id == current_user.id,
                models.Prediction.fixture_id.in_(fixture_ids),
            )
            .options(joinedload(models.Prediction.score))
            .all()
        )
    }

    result = []

    for fixture in fixtures:
        prediction = existing_preds.get(fixture.id)

        result.append(
            FixtureForPrediction(
                fixture_id=fixture.id,
                home_team=fixture.home_team.name,
                away_team=fixture.away_team.name,
                kickoff_utc=fixture.kickoff_utc,
                matchday_id=fixture.matchday_id,
                matchday_name=fixture.matchday.name,
                is_knockout=fixture.is_knockout,
                existing_prediction=(
                    _build_prediction_response(db, prediction) if prediction else None
                ),
            )
        )

    logger.info(
        "Returning fixtures",
        count=len(result),
        predictions_found=len(existing_preds),
    )

    return result


@router.get("/matchday-summary", response_model=List[MatchdaySummary])
def get_matchday_summary(
    db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)
):
    """Per-matchday stats for the current user: points, joker usage, progress."""
    stats = (
        db.query(models.PredictionMatchdayStats)
        .filter(models.PredictionMatchdayStats.user_id == current_user.id)
        .options(joinedload(models.PredictionMatchdayStats.matchday))
        .all()
    )

    summaries = []
    for stat in stats:
        predictions = (
            db.query(models.Prediction)
            .join(models.Fixture)
            .filter(
                models.Prediction.user_id == current_user.id,
                models.Fixture.matchday_id == stat.matchday_id,
            )
            .options(
                joinedload(models.Prediction.fixture).joinedload(models.Fixture.result)
            )
            .all()
        )
        pred_count = len(predictions)
        scored_count = sum(1 for p in predictions if p.fixture.result is not None)
        summaries.append(
            MatchdaySummary(
                matchday_id=stat.matchday_id,
                matchday_name=stat.matchday.name
                if stat.matchday
                else str(stat.matchday_id),
                total_points=stat.total_points,
                joker_used=stat.joker_used,
                joker_applied_to_fixture_id=stat.joker_applied_to_fixture_id,
                predictions_made=pred_count,
                predictions_scored=scored_count,
            )
        )
    return summaries


@router.delete("/{prediction_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_prediction(
    prediction_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Delete a prediction. Only allowed if the fixture deadline has not passed."""
    pred = (
        db.query(models.Prediction)
        .filter(
            models.Prediction.id == prediction_id,
            models.Prediction.user_id == current_user.id,
        )
        .first()
    )
    if not pred:
        raise HTTPException(status_code=404, detail="Prediction not found")

    check_prediction_deadline(db, pred.fixture_id)
    db.delete(pred)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_fixture_result(
    result: models.FixtureResult,
) -> Optional[FixtureResultResponse]:
    """Convert a FixtureResult ORM object to its Pydantic schema, or None."""
    if result is None:
        return None
    return FixtureResultResponse(
        home_goals=result.home_goals,
        away_goals=result.away_goals,
        extra_time_played=result.extra_time_played,
        home_extra_goals=result.home_extra_goals or 0,
        away_extra_goals=result.away_extra_goals or 0,
        penalty_shootout=result.penalty_shootout,
        home_penalties=result.home_penalties or 0,
        away_penalties=result.away_penalties or 0,
        winner=result.winner,
    )


def _build_score_inline(
    score: models.PredictionScore,
) -> Optional[PredictionScoreInline]:
    """Convert a PredictionScore ORM object to its inline schema, or None."""
    if score is None:
        return None
    return PredictionScoreInline(
        points_earned=score.points_earned,
        exact_score_points=score.exact_score_points or 0,
        correct_outcome_points=score.correct_outcome_points or 0,
        joker_multiplier_applied=score.joker_multiplier_applied,
        calculated_at=score.calculated_at,
    )


def _build_prediction_response(
    db: Session, prediction: models.Prediction
) -> Optional[PredictionResponse]:
    """
    Build a PredictionResponse from a Prediction ORM object.
    Used by write endpoints (POST / PATCH / batch) and by
    FixtureForPrediction.existing_prediction.

    Eager-loads fixture relationships if they are not already on the instance.
    """
    if not prediction:
        return None
    fixture = (
        db.query(models.Fixture)
        .filter(models.Fixture.id == prediction.fixture_id)
        .options(
            joinedload(models.Fixture.matchday),
            joinedload(models.Fixture.home_team),
            joinedload(models.Fixture.away_team),
            joinedload(models.Fixture.result),
        )
        .first()
    )
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found for prediction")

    # Re-query score in case it was just created or the relationship is stale
    score = (
        db.query(models.PredictionScore)
        .filter(models.PredictionScore.prediction_id == prediction.id)
        .first()
    )

    return PredictionResponse(
        id=prediction.id,
        fixture_id=prediction.fixture_id,
        predicted_home_goals=prediction.predicted_home_goals,
        predicted_away_goals=prediction.predicted_away_goals,
        is_joker=prediction.is_joker,
        created_at=prediction.created_at,
        updated_at=prediction.updated_at,
        matchday_id=fixture.matchday.id,
        matchday_name=fixture.matchday.name,
        fixture_home_team=fixture.home_team.name,
        fixture_away_team=fixture.away_team.name,
        kickoff_utc=fixture.kickoff_utc,
        fixture_finished=fixture.finished,
        fixture_result=_build_fixture_result(fixture.result),
        score=_build_score_inline(score),
        predicted_extra_time_home_goals=prediction.predicted_extra_time_home_goals,
        predicted_extra_time_away_goals=prediction.predicted_extra_time_away_goals,
        predicted_penalty_home_goals=prediction.predicted_penalty_home_goals,
        predicted_penalty_away_goals=prediction.predicted_penalty_away_goals,
    )


def _build_prediction_detail_response(
    prediction: models.Prediction,
) -> PredictionDetailResponse:
    """
    Build a PredictionDetailResponse from an already eagerly-loaded
    Prediction ORM object.

    Expects these relationships to already be loaded (via joinedload in the
    calling query):
      prediction.fixture.matchday
      prediction.fixture.home_team
      prediction.fixture.away_team
      prediction.fixture.result   ← FixtureResult (None if not yet entered)
      prediction.score            ← PredictionScore (None if worker hasn't run)
    """
    fixture = prediction.fixture
    return PredictionDetailResponse(
        id=prediction.id,
        fixture_id=prediction.fixture_id,
        predicted_home_goals=prediction.predicted_home_goals,
        predicted_away_goals=prediction.predicted_away_goals,
        is_joker=prediction.is_joker,
        created_at=prediction.created_at,
        updated_at=prediction.updated_at,
        matchday_id=fixture.matchday.id,
        matchday_name=fixture.matchday.name,
        fixture_home_team=fixture.home_team.name,
        fixture_away_team=fixture.away_team.name,
        kickoff_utc=fixture.kickoff_utc,
        fixture_finished=fixture.finished,
        result=_build_fixture_result(fixture.result),
        score=_build_score_inline(prediction.score),
    )
