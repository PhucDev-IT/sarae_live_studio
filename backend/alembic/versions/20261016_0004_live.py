"""OBS proof-of-concept playlists.

Revision ID: 20261016_0004
Revises: 20261016_0003
"""
from alembic import op
import sqlalchemy as sa

revision = "20261016_0004"
down_revision = "20261016_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "live_playlists",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("media_ids", sa.Text(), nullable=False),
    )


def downgrade():
    op.drop_table("live_playlists")
