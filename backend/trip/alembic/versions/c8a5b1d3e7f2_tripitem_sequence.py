"""add stable tripitem sequence

Revision ID: c8a5b1d3e7f2
Revises: 204ff7948b78
Create Date: 2026-09-06 00:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "c8a5b1d3e7f2"
down_revision = "204ff7948b78"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tripitem",
        sa.Column("sequence", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )

    connection = op.get_bind()
    item_rows = connection.execute(
        sa.text("SELECT id, day_id FROM tripitem ORDER BY day_id, time, id")
    ).mappings()
    next_sequence_by_day: dict[int, int] = {}
    updates: list[dict[str, int]] = []
    for item in item_rows:
        day_id = item["day_id"]
        sequence = next_sequence_by_day.get(day_id, 0)
        updates.append({"id": item["id"], "sequence": sequence})
        next_sequence_by_day[day_id] = sequence + 1

    if updates:
        connection.execute(
            sa.text("UPDATE tripitem SET sequence = :sequence WHERE id = :id"), updates
        )


def downgrade():
    op.drop_column("tripitem", "sequence")
