from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from .. import models, schemas, security
from ..database import get_db
from ..security import get_current_user_id_from_token
from ..config import settings
import redis
import structlog

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)

# ── Redis client (same instance used by the rest of the app) ─────────────────
def get_redis():
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def _blocklist_key(jti: str) -> str:
    return f"blocklist:{jti}"


def is_token_blocklisted(token: str) -> bool:
    """Return True if the token has been invalidated via logout."""
    try:
        payload = security.decode_token(token)
        if not payload:
            return True
        jti = payload.get("jti")
        if not jti:
            # Tokens issued before logout support are not in the blocklist;
            # treat as valid so we don't break existing sessions.
            return False
        r = get_redis()
        return r.exists(_blocklist_key(jti)) == 1
    except Exception as e:
        logger.warning("Blocklist check failed", error=str(e))
        return False


# ── Register ─────────────────────────────────────────────────────────────────
@router.post("/register", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED)
def register_user(user: schemas.UserCreate, db: Session = Depends(get_db)):
    if not (3 <= len(user.username) <= 20):
        raise HTTPException(status_code=422, detail="Username must be 3-20 characters")

    if not user.username.isalnum():
        raise HTTPException(status_code=422, detail="Username must be alphanumeric")

    if len(user.password) < 8:
        raise HTTPException(status_code=422, detail="Password must be 8+ characters")

    existing_email = db.query(models.User).filter(models.User.email == user.email).first()
    if existing_email:
        raise HTTPException(status_code=400, detail="Email already registered")

    existing_username = db.query(models.User).filter(models.User.username == user.username).first()
    if existing_username:
        raise HTTPException(status_code=400, detail="Username already taken")

    hashed_password = security.hash_password(user.password)
    new_user = models.User(
        username=user.username,
        email=user.email,
        hashed_password=hashed_password,
        is_active=True,
        onboarding_complete=False,
        activation_token=None,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


# ── Activate ─────────────────────────────────────────────────────────────────
@router.post("/activate", response_model=schemas.UserResponse)
def activate_account(payload: schemas.ActivateAccountRequest, db: Session = Depends(get_db)):
    """Activate a user account using the one-time token."""
    user = db.query(models.User).filter(
        models.User.activation_token == payload.token,
        models.User.is_active == False
    ).first()

    if not user:
        raise HTTPException(status_code=400, detail="Invalid or already used activation token.")

    user.is_active = True
    user.activation_token = None
    db.commit()
    db.refresh(user)
    return user


# ── Onboarding ────────────────────────────────────────────────────────────────
@router.post("/onboarding", response_model=schemas.TeamResponse)
def complete_onboarding(
    payload: schemas.SetTeamNameRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """Complete onboarding by creating a fantasy team."""
    user_id = get_current_user_id_from_token(request)
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account not yet activated")

    team_name = payload.team_name.strip()
    if len(team_name) < 2 or len(team_name) > 50:
        raise HTTPException(status_code=400, detail="Team name must be between 2 and 50 characters")

    league = db.query(models.League).first()
    if not league:
        raise HTTPException(status_code=400, detail="No league found")

    fantasy_team = models.FantasyTeam(
        name=team_name,
        user_id=user_id,
        season_id=league.season_id
    )
    db.add(fantasy_team)
    db.commit()
    db.refresh(fantasy_team)

    user.onboarding_complete = True
    db.commit()
    db.refresh(user)

    return {"team_id": fantasy_team.id, "team_name": fantasy_team.name}


# ── Me ────────────────────────────────────────────────────────────────────────
@router.get("/me", response_model=schemas.UserResponse)
def get_current_user_profile(request: Request, db: Session = Depends(get_db)):
    """Get the current user's profile."""
    user_id = get_current_user_id_from_token(request)
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# ── Login ─────────────────────────────────────────────────────────────────────
@router.post("/login", response_model=schemas.Token)
def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = db.query(models.User).filter(
        (models.User.email == form_data.username) | (models.User.username == form_data.username)
    ).first()

    if not user or not security.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account not activated. Please check your email.",
        )

    access_token  = security.create_access_token_for_user(user)
    refresh_token = security.create_refresh_token_for_user(user)

    logger.info("User logged in", user_id=user.id, username=user.username)

    return {
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "token_type":    "bearer",
    }


# ── Logout ────────────────────────────────────────────────────────────────────
@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request):
    """
    Invalidate the current access token by storing its JTI in Redis
    until the token's natural expiry time.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header missing")

    token = auth_header.split(" ", 1)[1]
    payload = security.decode_token(token)

    if not payload:
        # Already expired or invalid — nothing to blocklist
        return

    jti = payload.get("jti")
    exp = payload.get("exp")

    if jti and exp:
        try:
            import time
            ttl = max(int(exp - time.time()), 1)  # seconds until natural expiry
            r = get_redis()
            r.setex(_blocklist_key(jti), ttl, "1")
            logger.info("Token blocklisted on logout", jti=jti, ttl=ttl)
        except Exception as e:
            logger.error("Failed to blocklist token", error=str(e))
            # Don't fail the logout — client will clear its token anyway
    else:
        logger.warning("Logout token missing jti/exp — cannot blocklist", payload=payload)
