"""add trip planner settings

Revision ID: e4b3f14f9a2c
Revises: c8a5b1d3e7f2
Create Date: 2026-09-06 00:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "e4b3f14f9a2c"
down_revision = "c8a5b1d3e7f2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tripplannersettings",
        sa.Column("trip_id", sa.Integer(), nullable=False),
        sa.Column("requested_days", sa.Integer(), nullable=False),
        sa.Column("start_lat", sa.Float(), nullable=True),
        sa.Column("start_lng", sa.Float(), nullable=True),
        sa.Column("end_lat", sa.Float(), nullable=True),
        sa.Column("end_lng", sa.Float(), nullable=True),
        sa.Column("return_to_start", sa.Boolean(), nullable=False),
        sa.Column("allowed_profiles", sa.JSON(), nullable=False),
        sa.Column("objective", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["trip_id"], ["trip.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("trip_id"),
    )

    connection = op.get_bind()
    trip_ids = connection.execute(sa.text("SELECT id FROM trip")).scalars().all()
    if trip_ids:
        settings = sa.table(
            "tripplannersettings",
            sa.column("trip_id", sa.Integer()),
            sa.column("requested_days", sa.Integer()),
            sa.column("return_to_start", sa.Boolean()),
            sa.column("allowed_profiles", sa.JSON()),
            sa.column("objective", sa.String()),
        )
        connection.execute(
            settings.insert(),
            [
                {
                    "trip_id": trip_id,
                    "requested_days": 1,
                    "return_to_start": False,
                    "allowed_profiles": ["car"],
                    "objective": "duration",
                }
                for trip_id in trip_ids
            ],
        )


def downgrade():
    op.drop_table("tripplannersettings")
