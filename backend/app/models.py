import enum
from datetime import date

import structlog
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.orm import relationship

from .database import Base

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


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
    round_of_64 = "round_of_64"
    round_of_32 = "round_of_32"
    round_of_16 = "round_of_16"
    group = "group"
    quarterfinal = "quarterfinal"
    semifinal = "semifinal"
    final = "final"


class UserRole(enum.Enum):
    user = "user"
    admin = "admin"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class PrivateLeague(Base):
    """A user-created private league (group of fantasy teams)."""

    __tablename__ = "private_league"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    season_id = Column(Integer, ForeignKey("season.id"), nullable=False)
    created_by_user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    join_code = Column(String(5), unique=True, nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    max_teams = Column(Integer, nullable=True)  # optional limit

    # Relationships
    season = relationship("Season", back_populates="private_leagues")
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    memberships = relationship(
        "PrivateLeagueMembership",
        back_populates="private_league",
        cascade="all, delete-orphan",
    )


class User(Base):
    __tablename__ = "user"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(SQLEnum(UserRole), default=UserRole.user, nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)
    onboarding_complete = Column(Boolean, default=False, nullable=False)
    activation_token = Column(String, nullable=True)

    fantasy_teams = relationship(
        "FantasyTeam", back_populates="user", cascade="all, delete-orphan"
    )

    created_private_leagues = relationship(
        "PrivateLeague", foreign_keys=[PrivateLeague.created_by_user_id]
    )
    predictions = relationship(
        "Prediction", back_populates="user", cascade="all, delete-orphan"
    )


class Season(Base):
    __tablename__ = "season"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    status = Column(SQLEnum(SeasonStatus), default=SeasonStatus.active, nullable=False)

    leagues = relationship(
        "League", back_populates="season", cascade="all, delete-orphan"
    )
    matchdays = relationship(
        "Matchday", back_populates="season", cascade="all, delete-orphan"
    )
    fantasy_teams = relationship(
        "FantasyTeam", back_populates="season", cascade="all, delete-orphan"
    )
    tournament_phases = relationship(
        "TournamentPhase", back_populates="season", cascade="all, delete-orphan"
    )

    private_leagues = relationship("PrivateLeague", back_populates="season")


class Matchday(Base):
    """
    Global Round.

    Lock lifecycle
    --------------
    1. Admin (or worker) calls initialize_matchday_lock() when fixtures are known.
       → lock_at_utc is computed and persisted.
    2. A periodic task calls process_matchday_lock() every minute.
       → When now_utc >= lock_at_utc it flips is_locked=True and status=in_progress.
    3. Lock is released only when status transitions to `closed` (scoring complete).

    deadline_utc is kept as an alias / backward-compat synonym for lock_at_utc.
    Callers that still reference deadline_utc will continue to work; new code
    should prefer lock_at_utc.
    """

    __tablename__ = "matchday"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    season_id = Column(Integer, ForeignKey("season.id"), nullable=False)
    status = Column(
        SQLEnum(MatchdayStatus),
        default=MatchdayStatus.scheduled,
        nullable=False,
    )

    # ------------------------------------------------------------------
    # Lock fields  (NEW)
    # ------------------------------------------------------------------
    lock_at_utc = Column(DateTime(timezone=True), nullable=True)
    """Computed once from fixtures: earliest_kickoff - offset. Persisted."""

    is_locked = Column(Boolean, default=False, nullable=False)
    """Flipped to True by process_matchday_lock(); never reset to False here."""

    locked_at = Column(DateTime(timezone=True), nullable=True)
    """Audit timestamp: when is_locked was set to True."""

    # ------------------------------------------------------------------
    # Legacy / backward-compat
    # ------------------------------------------------------------------
    @property
    def deadline_utc(self):
        """Alias for lock_at_utc.  Kept so existing callers don't break."""
        return self.lock_at_utc

    @deadline_utc.setter
    def deadline_utc(self, value):
        self.lock_at_utc = value

    # ------------------------------------------------------------------
    # Worker / Celery fields (pre-existing)
    # ------------------------------------------------------------------
    task_id = Column(String, nullable=True)
    task_status = Column(String, default="pending")

    season = relationship("Season", back_populates="matchdays")
    fixtures = relationship(
        "Fixture", back_populates="matchday", cascade="all, delete-orphan"
    )
    player_scores = relationship(
        "PlayerScore", back_populates="matchday", cascade="all, delete-orphan"
    )
    team_scores = relationship(
        "TeamScore", back_populates="matchday", cascade="all, delete-orphan"
    )


class Fixture(Base):
    """
    A real match that belongs to a Matchday.
    The earliest kickoff_utc across all fixtures in a matchday drives lock_at_utc.
    """

    __tablename__ = "fixture"

    id = Column(Integer, primary_key=True, index=True)
    matchday_id = Column(Integer, ForeignKey("matchday.id"), nullable=False)
    home_team_id = Column(Integer, ForeignKey("team.id"), nullable=False)
    away_team_id = Column(Integer, ForeignKey("team.id"), nullable=False)
    kickoff_utc = Column(DateTime(timezone=True), nullable=False)
    finished = Column(Boolean, default=False, nullable=False)
    is_knockout = Column(Boolean, default=False, nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    matchday = relationship("Matchday", back_populates="fixtures")
    home_team = relationship("Team", foreign_keys=[home_team_id])
    away_team = relationship("Team", foreign_keys=[away_team_id])
    result = relationship("FixtureResult", uselist=False, back_populates="fixture")
    predictions = relationship(
        "Prediction", back_populates="fixture", cascade="all, delete-orphan"
    )


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
    name = Column(String, index=True, nullable=False)
    league_id = Column(Integer, ForeignKey("league.id"), nullable=False)
    eliminated_in_phase_id = Column(
        Integer, ForeignKey("tournament_phase.id"), nullable=True
    )

    league = relationship("League", back_populates="teams")
    eliminated_in_phase = relationship(
        "TournamentPhase", back_populates="eliminated_teams"
    )
    players = relationship(
        "Player", back_populates="team", cascade="all, delete-orphan"
    )


class Player(Base):
    __tablename__ = "player"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    position = Column(SQLEnum(PlayerPosition), nullable=False)
    tier = Column(SQLEnum(PlayerTier), nullable=False)
    credit_value = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    team_id = Column(Integer, ForeignKey("team.id"), nullable=False)

    team = relationship("Team", back_populates="players")
    fantasy_players = relationship(
        "FantasyPlayer", back_populates="player", cascade="all, delete-orphan"
    )
    scores = relationship(
        "PlayerScore", back_populates="player", cascade="all, delete-orphan"
    )


class FantasyTeam(Base):
    __tablename__ = "fantasy_team"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    season_id = Column(Integer, ForeignKey("season.id"), nullable=False)
    # Optimistic-lock / audit field (NEW)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=True,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user = relationship("User", back_populates="fantasy_teams")
    season = relationship("Season", back_populates="fantasy_teams")
    fantasy_players = relationship(
        "FantasyPlayer", back_populates="fantasy_team", cascade="all, delete-orphan"
    )
    team_scores = relationship(
        "TeamScore", back_populates="fantasy_team", cascade="all, delete-orphan"
    )
    leaderboard_weekly_entries = relationship(
        "LeaderboardWeeklyEntry",
        back_populates="fantasy_team",
        cascade="all, delete-orphan",
    )

    private_league_memberships = relationship(
        "PrivateLeagueMembership", back_populates="fantasy_team"
    )


class FantasyPlayer(Base):
    __tablename__ = "fantasy_player"

    id = Column(Integer, primary_key=True, index=True)
    fantasy_team_id = Column(Integer, ForeignKey("fantasy_team.id"), nullable=False)
    player_id = Column(Integer, ForeignKey("player.id"), nullable=False)
    slot = Column(SQLEnum(FantasySlot), default=FantasySlot.starter, nullable=False)
    formation_position = Column(String, nullable=False)
    is_x2_joker = Column(Boolean, default=False, nullable=False)
    # Optimistic-lock / audit field (NEW)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=True,
        server_default=func.now(),
        onupdate=func.now(),
    )

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


class FixtureResult(Base):
    __tablename__ = "fixture_result"

    id = Column(Integer, primary_key=True, index=True)
    fixture_id = Column(Integer, ForeignKey("fixture.id"), nullable=False, unique=True)

    # Core result – total goals after any extra time (regulation + extra)
    home_goals = Column(Integer, default=0, nullable=False)
    away_goals = Column(Integer, default=0, nullable=False)

    # Extra time details (only relevant for knockout matches)
    extra_time_played = Column(Boolean, default=False)
    home_extra_goals = Column(Integer, default=0)
    away_extra_goals = Column(Integer, default=0)

    # Penalty shootout details
    penalty_shootout = Column(Boolean, default=False)
    home_penalties = Column(Integer, default=0)
    away_penalties = Column(Integer, default=0)

    # Optional: pre‑computed winner (avoid repeated logic)
    winner_team_id = Column(Integer, ForeignKey("team.id"), nullable=True)

    # Audit & data quality
    verified_at = Column(DateTime(timezone=True), nullable=True)
    source = Column(String, default="admin")  # e.g., "admin", "csv_import", "api"

    # Relationships
    fixture = relationship("Fixture", back_populates="result")
    winner_team = relationship("Team", foreign_keys=[winner_team_id])

    @property
    def is_knockout_match(self) -> bool:
        """Return True if extra time or penalties are meaningful."""
        return self.extra_time_played or self.penalty_shootout

    @property
    def winner(self):
        """Return 'home', 'away', or None if draw (no penalties)."""
        if self.penalty_shootout:
            return "home" if self.home_penalties > self.away_penalties else "away"
        if self.home_goals > self.away_goals:
            return "home"
        if self.away_goals > self.home_goals:
            return "away"
        return None  # draw after regulation + extra time


class LeaderboardWeeklyEntry(Base):
    __tablename__ = "leaderboard_weekly_entry"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True, default=date.today)
    fantasy_team_id = Column(Integer, ForeignKey("fantasy_team.id"), nullable=False)
    total_points = Column(Integer, default=0, nullable=False)

    fantasy_team = relationship(
        "FantasyTeam", back_populates="leaderboard_weekly_entries"
    )


class PrivateLeagueMembership(Base):
    """Links a fantasy team to a private league."""

    __tablename__ = "private_league_membership"

    id = Column(Integer, primary_key=True, index=True)
    private_league_id = Column(Integer, ForeignKey("private_league.id"), nullable=False)
    fantasy_team_id = Column(Integer, ForeignKey("fantasy_team.id"), nullable=False)
    joined_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    private_league = relationship("PrivateLeague", back_populates="memberships")
    fantasy_team = relationship("FantasyTeam")

    # Ensure a fantasy team can join a given private league only once
    __table_args__ = (
        UniqueConstraint(
            "private_league_id",
            "fantasy_team_id",
            name="uq_private_league_fantasy_team",
        ),
    )


class Prediction(Base):
    __tablename__ = "prediction"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    fixture_id = Column(Integer, ForeignKey("fixture.id"), nullable=False)

    predicted_home_goals = Column(Integer, nullable=False)
    predicted_away_goals = Column(Integer, nullable=False)

    # If True, this prediction earns double points for its matchday (only one per user per matchday)
    is_joker = Column(Boolean, default=False, nullable=False)
    predicted_extra_time_home_goals = Column(Integer, default=0, nullable=False)
    predicted_extra_time_away_goals = Column(Integer, default=0, nullable=False)

    predicted_penalty_home_goals = Column(Integer, default=0, nullable=False)
    predicted_penalty_away_goals = Column(Integer, default=0, nullable=False)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user = relationship("User", back_populates="predictions")
    fixture = relationship("Fixture", back_populates="predictions")
    score = relationship(
        "PredictionScore",
        uselist=False,
        back_populates="prediction",
        cascade="all, delete-orphan",
    )

    # Ensure a user can predict a fixture only once
    __table_args__ = (
        UniqueConstraint("user_id", "fixture_id", name="uq_user_fixture_prediction"),
    )


class PredictionScore(Base):
    """
    Once a fixture result is known, you calculate how many points the user gets for their prediction. Storing the result separately allows:
        Recalculation if scoring rules change.
        Audit trail of when points were awarded.
    """

    __tablename__ = "prediction_score"

    id = Column(Integer, primary_key=True, index=True)
    prediction_id = Column(
        Integer, ForeignKey("prediction.id"), nullable=False, unique=True
    )
    points_earned = Column(Integer, default=0, nullable=False)
    calculated_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    correct_penalty_winner_points = Column(Integer, default=0)
    exact_penalty_points = Column(Integer, default=0)
    # Optional: breakdown for transparency
    exact_score_points = Column(Integer, default=0)
    correct_outcome_points = Column(Integer, default=0)
    joker_multiplier_applied = Column(Boolean, default=False)

    prediction = relationship("Prediction", back_populates="score")


class PredictionMatchdayStats(Base):
    __tablename__ = "prediction_matchday_stats"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    matchday_id = Column(Integer, ForeignKey("matchday.id"), nullable=False)

    total_points = Column(Integer, default=0, nullable=False)
    joker_used = Column(Boolean, default=False, nullable=False)
    joker_applied_to_fixture_id = Column(
        Integer, ForeignKey("fixture.id"), nullable=True
    )

    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user = relationship("User")
    matchday = relationship("Matchday")
    joker_fixture = relationship("Fixture")

    __table_args__ = (
        UniqueConstraint("user_id", "matchday_id", name="uq_user_matchday_stats"),
    )


class PlayerPoints(Base):
    """For storing how many points the FantasyPlayer has scored during a matchday."""

    __tablename__ = "player_points"

    id = Column(Integer, primary_key=True, index=True)
    fantasy_player_id = Column(Integer, ForeignKey("fantasy_player.id"), nullable=False)
    matchday_id = Column(Integer, ForeignKey("matchday.id"), nullable=False)
    points = Column(Integer, nullable=False)

    # Relationships
    fantasy_player = relationship("FantasyPlayer", back_populates="player_points")
    matchday = relationship("Matchday", back_populates="player_points")

    # Ensure unique combination of fantasy_player_id and matchday_id
    __table_args__ = (UniqueConstraint("fantasy_player_id", "matchday_id"),)
