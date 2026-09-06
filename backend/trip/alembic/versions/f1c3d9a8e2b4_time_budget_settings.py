"""add Smart Planner duration policy and day time settings

Revision ID: f1c3d9a8e2b4
Revises: e4b3f14f9a2c
Create Date: 2026-09-06 00:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "f1c3d9a8e2b4"
down_revision = "e4b3f14f9a2c"
branch_labels = None
depends_on = None


def upgrade():
    # Existing places already retain their optional explicit ``duration``.
    # The new category/day defaults make old rows immediately readable without
    # changing any existing route, allocation, or sequence behaviour.
    op.add_column(
        "category",
        sa.Column("default_duration", sa.Integer(), nullable=False, server_default=sa.text("60")),
    )
    op.add_column(
        "tripday",
        sa.Column("start_time", sa.String(length=5), nullable=False, server_default=sa.text("'09:00'")),
    )
    op.add_column(
        "tripday",
        sa.Column("end_time", sa.String(length=5), nullable=False, server_default=sa.text("'18:00'")),
    )
    op.add_column("tripitem", sa.Column("duration", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("tripitem", "duration")
    op.drop_column("tripday", "end_time")
    op.drop_column("tripday", "start_time")
    op.drop_column("category", "default_duration")
