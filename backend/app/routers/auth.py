import secrets
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from .. import models, schemas, security
from ..database import get_db

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)


def get_current_user_id_from_dummy_token(request: Request) -> int:
    """Extract user ID from dummy bearer token. Used until JWT is implemented."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer dummy-token-user-"):
        try:
            return int(auth_header.split("-")[-1])
        except ValueError:
            pass
    return 1  # fallback


@router.post("/register", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED)
def register_user(user: schemas.UserCreate, db: Session = Depends(get_db)):
    existing_email = db.query(models.User).filter(models.User.email == user.email).first()
    if existing_email:
        raise HTTPException(status_code=400, detail="Email already registered")
        
    existing_username = db.query(models.User).filter(models.User.username == user.username).first()
    if existing_username:
        raise HTTPException(status_code=400, detail="Username already taken")

    hashed_password = security.get_password_hash(user.password)
    activation_token = secrets.token_urlsafe(32)
    
    new_user = models.User(
        username=user.username,
        email=user.email,
        hashed_password=hashed_password,
        is_active=False,
        onboarding_complete=False,
        activation_token=activation_token,
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    # TODO: Send activation email with token. For now, log it to stdout.
    print(f"[ACTIVATION] User {new_user.email} — token: {activation_token}")
    print(f"[ACTIVATION] URL: /auth/activate?token={activation_token}")
    
    return new_user


@router.post("/activate", response_model=schemas.UserResponse)
def activate_account(payload: schemas.ActivateAccountRequest, db: Session = Depends(get_db)):
    """Activate a user account using the one-time token."""
    user = db.query(models.User).filter(
        models.User.activation_token == payload.token,
        models.User.is_active == False
    ).first()
    
    if not user:
        raise HTTPException(
            status_code=400,
            detail="Invalid or already used activation token."
        )
    
    user.is_active = True
    user.activation_token = None  # consume token
    db.commit()
    db.refresh(user)
    return user


@router.post("/onboarding/team-name", response_model=schemas.UserResponse)
def set_team_name(
    payload: schemas.SetTeamNameRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """Second onboarding step: set the user's fantasy team name."""
    user_id = get_current_user_id_from_dummy_token(request)
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account not yet activated")
    
    team_name = payload.team_name.strip()
    if len(team_name) < 2 or len(team_name) > 50:
        raise HTTPException(status_code=400, detail="Team name must be between 2 and 50 characters")

    season = db.query(models.Season).filter(models.Season.status == models.SeasonStatus.active).first()
    if season:
        team = db.query(models.FantasyTeam).filter(
            models.FantasyTeam.user_id == user_id,
            models.FantasyTeam.season_id == season.id
        ).first()
        if team:
            team.name = team_name
        else:
            team = models.FantasyTeam(
                name=team_name,
                user_id=user_id,
                season_id=season.id
            )
            db.add(team)

    user.onboarding_complete = True
    db.commit()
    db.refresh(user)
    return user


@router.get("/me", response_model=schemas.UserResponse)
def get_current_user_profile(
    request: Request,
    db: Session = Depends(get_db)
):
    """Get the current user's profile."""
    user_id = get_current_user_id_from_dummy_token(request)
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/login", response_model=schemas.Token)
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
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
        
    access_token = security.create_access_token(data={"sub": user.email, "role": user.role.value})
    refresh_token = security.create_refresh_token(data={"sub": user.email, "role": user.role.value})
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }


@router.get("/admin-only", response_model=schemas.UserResponse)
def admin_only_endpoint(current_admin: models.User = Depends(security.get_current_admin_user)):
    return current_admin
