"""add user onboarding fields

Revision ID: a1b2c3d4e5f6
Revises: e33a86c6be5e
Create Date: 2026-05-02 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'e33a86c6be5e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('user', sa.Column('is_active', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('user', sa.Column('onboarding_complete', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('user', sa.Column('activation_token', sa.String(), nullable=True))

    # Existing admin/seed users should be active and have onboarding complete
    op.execute("UPDATE \"user\" SET is_active = true, onboarding_complete = true WHERE role = 'admin'")


def downgrade() -> None:
    op.drop_column('user', 'activation_token')
    op.drop_column('user', 'onboarding_complete')
    op.drop_column('user', 'is_active')
