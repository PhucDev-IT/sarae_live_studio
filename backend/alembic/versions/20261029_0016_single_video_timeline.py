"""Single Video Timeline LIVE: session playback mode, main video, versioned pin timelines.

Additive only; app/shop_schema.py applies the same at startup. Every existing session becomes ADVANCED_LEGACY, so it
keeps the product-queue behaviour. Downgrade drops the new columns and tables (timelines are lost; sessions and their
history are kept).

Revision ID: 20261029_0016
Revises: 20261023_0015
"""
from alembic import op
import sqlalchemy as sa

revision = "20261029_0016"
down_revision = "20261023_0015"
branch_labels = None
depends_on = None

SESSION_COLUMNS = (
    sa.Column("playback_mode", sa.String(30), nullable=False, server_default="ADVANCED_LEGACY"),
    sa.Column("main_media_id", sa.String(32), nullable=True),
    sa.Column("video_loop", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("active_timeline_id", sa.Integer, nullable=True),
)


def upgrade():
    bind = op.get_bind()
    present = {c["name"] for c in sa.inspect(bind).get_columns("live_sessions")}
    for column in SESSION_COLUMNS:
        if column.name not in present:
            op.add_column("live_sessions", column.copy())
    if not sa.inspect(bind).has_table("live_timelines"):
        op.create_table("live_timelines",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("session_id", sa.Integer, sa.ForeignKey("live_sessions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("owner_id", sa.String(64), nullable=False),
            sa.Column("version", sa.Integer, nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("media_id", sa.String(32), nullable=False),
            sa.Column("media_sha256", sa.String(64), nullable=True),
            sa.Column("duration_ms", sa.Integer, nullable=True),
            sa.Column("note", sa.Text, nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("updated_at", sa.DateTime, nullable=False),
            sa.Column("approved_at", sa.DateTime, nullable=True),
            sa.Column("activated_at", sa.DateTime, nullable=True),
            sa.UniqueConstraint("session_id", "version", name="uq_live_timeline_version"))
        op.create_index("ix_live_timelines_session_id", "live_timelines", ["session_id"])
        op.create_index("ix_live_timelines_owner_id", "live_timelines", ["owner_id"])
    if not sa.inspect(bind).has_table("live_timeline_markers"):
        op.create_table("live_timeline_markers",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("timeline_id", sa.Integer, sa.ForeignKey("live_timelines.id", ondelete="CASCADE"), nullable=False),
            sa.Column("at_ms", sa.Integer, nullable=False),
            sa.Column("action", sa.String(20), nullable=False),
            sa.Column("product_id", sa.Integer, nullable=True),
            sa.Column("seq", sa.Integer, nullable=False),
            sa.Column("label", sa.String(200), nullable=False),
            sa.Column("source", sa.String(10), nullable=False),
            sa.Column("review_status", sa.String(20), nullable=False),
            sa.Column("confidence", sa.Float, nullable=True),
            sa.Column("evidence", sa.Text, nullable=True))
        op.create_index("ix_live_timeline_markers_timeline_id", "live_timeline_markers", ["timeline_id"])


def downgrade():
    op.drop_table("live_timeline_markers")
    op.drop_table("live_timelines")
    # In place (SQLite >= 3.35, as 20261023_0015): no table rebuild, so the partial unique indexes on live_sessions stay.
    for column in reversed(SESSION_COLUMNS):
        op.drop_column("live_sessions", column.name)
