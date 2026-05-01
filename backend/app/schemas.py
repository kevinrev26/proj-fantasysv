from pydantic import BaseModel, EmailStr
from datetime import date, datetime
from typing import Optional

class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    id: int
    username: str
    email: EmailStr
    role: str
    
    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str

class SeasonCreate(BaseModel):
    name: str
    start_date: date
    end_date: date

class MatchdayCreate(BaseModel):
    name: str
    deadline_utc: Optional[datetime] = None

class LeagueCreate(BaseModel):
    name: str

class TeamCreate(BaseModel):
    name: str

class PlayerCreate(BaseModel):
    name: str
    position: str
    tier: str
    credit_value: int

class PlayerUpdate(BaseModel):
    is_active: Optional[bool] = None
    credit_value: Optional[int] = None
    tier: Optional[str] = None

class TournamentPhaseCreate(BaseModel):
    name: str
