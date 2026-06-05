"""add_knockout_phase

Revision ID: 13c20a7edb44
Revises: fdfb994017cb
Create Date: 2026-06-05 18:04:47.433940

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "13c20a7edb44"
down_revision: Union[str, None] = "fdfb994017cb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add fixture.is_knockout as nullable first
    op.add_column("fixture", sa.Column("is_knockout", sa.Boolean(), nullable=True))
    op.execute("UPDATE fixture SET is_knockout = false WHERE is_knockout IS NULL")
    op.alter_column("fixture", "is_knockout", nullable=False)

    # 2. Add prediction extra time & penalty columns as nullable first
    op.add_column(
        "prediction",
        sa.Column("predicted_extra_time_home_goals", sa.Integer(), nullable=True),
    )
    op.add_column(
        "prediction",
        sa.Column("predicted_extra_time_away_goals", sa.Integer(), nullable=True),
    )
    op.add_column(
        "prediction",
        sa.Column("predicted_penalty_home_goals", sa.Integer(), nullable=True),
    )
    op.add_column(
        "prediction",
        sa.Column("predicted_penalty_away_goals", sa.Integer(), nullable=True),
    )

    # Set default 0 for existing rows
    op.execute(
        "UPDATE prediction SET predicted_extra_time_home_goals = 0 WHERE predicted_extra_time_home_goals IS NULL"
    )
    op.execute(
        "UPDATE prediction SET predicted_extra_time_away_goals = 0 WHERE predicted_extra_time_away_goals IS NULL"
    )
    op.execute(
        "UPDATE prediction SET predicted_penalty_home_goals = 0 WHERE predicted_penalty_home_goals IS NULL"
    )
    op.execute(
        "UPDATE prediction SET predicted_penalty_away_goals = 0 WHERE predicted_penalty_away_goals IS NULL"
    )

    # Now make them NOT NULL
    op.alter_column("prediction", "predicted_extra_time_home_goals", nullable=False)
    op.alter_column("prediction", "predicted_extra_time_away_goals", nullable=False)
    op.alter_column("prediction", "predicted_penalty_home_goals", nullable=False)
    op.alter_column("prediction", "predicted_penalty_away_goals", nullable=False)

    # 3. Add prediction_score columns (nullable is fine, no need to alter)
    op.add_column(
        "prediction_score",
        sa.Column("correct_penalty_winner_points", sa.Integer(), nullable=True),
    )
    op.add_column(
        "prediction_score",
        sa.Column("exact_penalty_points", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("prediction_score", "exact_penalty_points")
    op.drop_column("prediction_score", "correct_penalty_winner_points")
    op.drop_column("prediction", "predicted_penalty_away_goals")
    op.drop_column("prediction", "predicted_penalty_home_goals")
    op.drop_column("prediction", "predicted_extra_time_away_goals")
    op.drop_column("prediction", "predicted_extra_time_home_goals")
    op.drop_column("fixture", "is_knockout")
