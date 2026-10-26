"""LIVE sessions: playback policy (video order, automatic product rotation).

Additive only (one nullable column); app/shop_schema.py applies the same at startup.

Revision ID: 20261023_0015
Revises: 20261023_0014
"""
from alembic import op
import sqlalchemy as sa

revision = "20261023_0015"
down_revision = "20261023_0014"
branch_labels = None
depends_on = None


def upgrade():
    if "playback_policy" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("live_sessions")}:
        op.add_column("live_sessions", sa.Column("playback_policy", sa.Text, nullable=True))


def downgrade():
    op.drop_column("live_sessions", "playback_policy")
