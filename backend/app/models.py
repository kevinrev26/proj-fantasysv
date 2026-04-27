import enum
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Date, DateTime, Enum as SQLEnum
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

class FantasySlot(enum.Enum):
    starter = "starter"
    bench = "bench"

class PhaseName(enum.Enum):
    group = "group"
    quarterfinal = "quarterfinal"
    semifinal = "semifinal"
    final = "final"

class UserRole(enum.Enum):
    user = "user"
    admin = "admin"

class User(Base):
    __tablename__ = "user"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(SQLEnum(UserRole), default=UserRole.user, nullable=False)
    
    fantasy_teams = relationship("FantasyTeam", back_populates="user", cascade="all, delete-orphan")

class Season(Base):
    __tablename__ = "season"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    status = Column(SQLEnum(SeasonStatus), default=SeasonStatus.active, nullable=False)
    
    leagues = relationship("League", back_populates="season", cascade="all, delete-orphan")
    matchdays = relationship("Matchday", back_populates="season", cascade="all, delete-orphan")
    fantasy_teams = relationship("FantasyTeam", back_populates="season", cascade="all, delete-orphan")
    tournament_phases = relationship("TournamentPhase", back_populates="season", cascade="all, delete-orphan")

class Matchday(Base):
    __tablename__ = "matchday"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    season_id = Column(Integer, ForeignKey("season.id"), nullable=False)
    status = Column(SQLEnum(MatchdayStatus), default=MatchdayStatus.scheduled, nullable=False)
    deadline_utc = Column(DateTime(timezone=True), nullable=True)
    task_id = Column(String, nullable=True)
    task_status = Column(String, default="pending")
    
    season = relationship("Season", back_populates="matchdays")
    player_scores = relationship("PlayerScore", back_populates="matchday", cascade="all, delete-orphan")
    team_scores = relationship("TeamScore", back_populates="matchday", cascade="all, delete-orphan")

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
    eliminated_in_phase_id = Column(Integer, ForeignKey("tournament_phase.id"), nullable=True)
    
    league = relationship("League", back_populates="teams")
    eliminated_in_phase = relationship("TournamentPhase", back_populates="eliminated_teams")
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
    fantasy_players = relationship("FantasyPlayer", back_populates="player", cascade="all, delete-orphan")
    scores = relationship("PlayerScore", back_populates="player", cascade="all, delete-orphan")

class FantasyTeam(Base):
    __tablename__ = "fantasy_team"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    season_id = Column(Integer, ForeignKey("season.id"), nullable=False)
    
    user = relationship("User", back_populates="fantasy_teams")
    season = relationship("Season", back_populates="fantasy_teams")
    fantasy_players = relationship("FantasyPlayer", back_populates="fantasy_team", cascade="all, delete-orphan")
    team_scores = relationship("TeamScore", back_populates="fantasy_team", cascade="all, delete-orphan")

class FantasyPlayer(Base):
    __tablename__ = "fantasy_player"
    
    id = Column(Integer, primary_key=True, index=True)
    fantasy_team_id = Column(Integer, ForeignKey("fantasy_team.id"), nullable=False)
    player_id = Column(Integer, ForeignKey("player.id"), nullable=False)
    slot = Column(SQLEnum(FantasySlot), default=FantasySlot.starter, nullable=False)
    formation_position = Column(String, nullable=False)
    is_x2_joker = Column(Boolean, default=False, nullable=False)
    
    fantasy_team = relationship("FantasyTeam", back_populates="fantasy_players")
    player = relationship("Player", back_populates="fantasy_players")

class PlayerScore(Base):
    __tablename__ = "player_score"
    
    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("player.id"), nullable=False)
    matchday_id = Column(Integer, ForeignKey("matchday.id"), nullable=False)
    
    goals = Column(Integer, default=0, nullable=False)
    assists = Column(Integer, default=0, nullable=False)
    minutes_played = Column(Integer, default=0, nullable=False)
    yellow_card = Column(Integer, default=0, nullable=False)
    red_card = Column(Integer, default=0, nullable=False)
    own_goal = Column(Integer, default=0, nullable=False)
    penalty_missed = Column(Integer, default=0, nullable=False)
    penalty_saved = Column(Integer, default=0, nullable=False)
    goals_conceded = Column(Integer, default=0, nullable=False)    
    base_points = Column(Integer, default=0, nullable=False)
    bonus_points = Column(Integer, default=0, nullable=False)
    final_points = Column(Integer, default=0, nullable=False)
    
    player = relationship("Player", back_populates="scores")
    matchday = relationship("Matchday", back_populates="player_scores")

class TeamScore(Base):
    __tablename__ = "team_score"
    
    id = Column(Integer, primary_key=True, index=True)
    fantasy_team_id = Column(Integer, ForeignKey("fantasy_team.id"), nullable=False)
    matchday_id = Column(Integer, ForeignKey("matchday.id"), nullable=False)
    
    points_this_matchday = Column(Integer, default=0, nullable=False)
    cumulative_points = Column(Integer, default=0, nullable=False)
    transfer_penalty = Column(Integer, default=0, nullable=False)
    
    fantasy_team = relationship("FantasyTeam", back_populates="team_scores")
    matchday = relationship("Matchday", back_populates="team_scores")

class TournamentPhase(Base):
    __tablename__ = "tournament_phase"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(SQLEnum(PhaseName), nullable=False)
    season_id = Column(Integer, ForeignKey("season.id"), nullable=False)
    
    season = relationship("Season", back_populates="tournament_phases")
    eliminated_teams = relationship("Team", back_populates="eliminated_in_phase")

class SystemConfig(Base):
    __tablename__ = "system_config"
    
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True, nullable=False)
    value = Column(String, nullable=False)

class Transfer(Base):
    __tablename__ = "transfer"
    
    id = Column(Integer, primary_key=True, index=True)
    fantasy_team_id = Column(Integer, ForeignKey("fantasy_team.id"), nullable=False)
    matchday_id = Column(Integer, ForeignKey("matchday.id"), nullable=False)
    player_in_id = Column(Integer, ForeignKey("player.id"), nullable=False)
    player_out_id = Column(Integer, ForeignKey("player.id"), nullable=False)
    cost = Column(Integer, default=0, nullable=False)
    
    fantasy_team = relationship("FantasyTeam")
    matchday = relationship("Matchday")
    player_in = relationship("Player", foreign_keys=[player_in_id])
    player_out = relationship("Player", foreign_keys=[player_out_id])
