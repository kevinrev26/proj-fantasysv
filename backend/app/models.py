import enum
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Date, Enum as SQLEnum
from sqlalchemy.orm import relationship
from .database import Base

class PlayerPosition(enum.Enum):
    GK = "GK"
    DF = "DF"
    MF = "MF"
    FW = "FW"

class PlayerTier(enum.Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"

class SeasonStatus(enum.Enum):
    active = "active"
    finished = "finished"

class MatchdayStatus(enum.Enum):
    scheduled = "scheduled"
    in_progress = "in_progress"
    closed = "closed"

class Season(Base):
    __tablename__ = "season"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    status = Column(SQLEnum(SeasonStatus), default=SeasonStatus.active, nullable=False)
    
    leagues = relationship("League", back_populates="season", cascade="all, delete-orphan")
    matchdays = relationship("Matchday", back_populates="season", cascade="all, delete-orphan")

class Matchday(Base):
    __tablename__ = "matchday"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    season_id = Column(Integer, ForeignKey("season.id"), nullable=False)
    status = Column(SQLEnum(MatchdayStatus), default=MatchdayStatus.scheduled, nullable=False)
    
    season = relationship("Season", back_populates="matchdays")

class League(Base):
    __tablename__ = "league"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    season_id = Column(Integer, ForeignKey("season.id"), nullable=False)
    
    season = relationship("Season", back_populates="leagues")
    teams = relationship("Team", back_populates="league", cascade="all, delete-orphan")

class Team(Base):
    __tablename__ = "team"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)  # fake name
    league_id = Column(Integer, ForeignKey("league.id"), nullable=False)
    
    league = relationship("League", back_populates="teams")
    players = relationship("Player", back_populates="team", cascade="all, delete-orphan")

class Player(Base):
    __tablename__ = "player"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)  # anonymized J. Doe
    position = Column(SQLEnum(PlayerPosition), nullable=False)
    tier = Column(SQLEnum(PlayerTier), nullable=False)
    credit_value = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    team_id = Column(Integer, ForeignKey("team.id"), nullable=False)
    
    team = relationship("Team", back_populates="players")
