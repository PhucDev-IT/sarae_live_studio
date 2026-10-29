"""AI Timeline Analyzer: analysis jobs and their cache.

Additive only; app/timeline_ai/api.py ensure_schema creates the same at startup. Downgrade drops both tables (past
analyses and cached transcripts are lost; timelines made from them are kept).

Revision ID: 20261029_0017
Revises: 20261029_0016
"""
from alembic import op
import sqlalchemy as sa

revision = "20261029_0017"
down_revision = "20261029_0016"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("timeline_analyses"):
        op.create_table("timeline_analyses",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("owner_id", sa.String(64), nullable=False),
            sa.Column("session_id", sa.Integer, nullable=False),
            sa.Column("media_id", sa.String(32), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("phase", sa.String(200), nullable=False),
            sa.Column("progress", sa.Float, nullable=False),
            sa.Column("cache_key", sa.String(64), nullable=False),
            sa.Column("cached", sa.Integer, nullable=False),
            sa.Column("config", sa.Text, nullable=False),
            sa.Column("result", sa.Text, nullable=True),
            sa.Column("error", sa.Text, nullable=True),
            sa.Column("timeline_id", sa.Integer, nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("updated_at", sa.DateTime, nullable=False))
        op.create_index("ix_timeline_analyses_owner_id", "timeline_analyses", ["owner_id"])
        op.create_index("ix_timeline_analyses_session_id", "timeline_analyses", ["session_id"])
    if not sa.inspect(bind).has_table("timeline_ai_cache"):
        op.create_table("timeline_ai_cache",
            sa.Column("cache_key", sa.String(64), primary_key=True),
            sa.Column("kind", sa.String(20), nullable=False),
            sa.Column("owner_id", sa.String(64), nullable=False),
            sa.Column("data", sa.Text, nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False))
        op.create_index("ix_timeline_ai_cache_owner_id", "timeline_ai_cache", ["owner_id"])


def downgrade():
    op.drop_table("timeline_ai_cache")
    op.drop_table("timeline_analyses")
