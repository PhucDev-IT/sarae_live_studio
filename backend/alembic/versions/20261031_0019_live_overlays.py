"""Overlay events of Single Video Timeline versions, and the owner images they show.

Additive only (two new tables); app/shop_schema.py and app/live_overlays.py ensure_schema create the same at startup.
- live_overlay_events: one overlay of a timeline version, on one video (`media_id`) from `start_ms` for `duration_ms`;
  box on the 1080×1920 canvas as fractions (x, y, width, height), z_index, opacity, animations, and its building blocks
  (`elements`, JSON). Deleted with its timeline.
- live_overlay_assets: images an owner uploaded for overlays (PNG/JPEG/WebP ≤ 5 MB, content checked), soft-deleted.
Downgrade drops both tables (pin markers and timelines are not touched).

Revision ID: 20261031_0019
Revises: 20261030_0018
"""
from alembic import op
import sqlalchemy as sa

revision = "20261031_0019"
down_revision = "20261030_0018"
branch_labels = None
depends_on = None


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "live_overlay_assets" not in tables:
        op.create_table(
            "live_overlay_assets",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("owner_id", sa.String(64), nullable=False, index=True),
            sa.Column("filename", sa.String(255), nullable=False, server_default=""),
            sa.Column("mime_type", sa.String(30), nullable=False),
            sa.Column("size_bytes", sa.Integer, nullable=False),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("width", sa.Integer, nullable=False),
            sa.Column("height", sa.Integer, nullable=False),
            sa.Column("content", sa.LargeBinary, nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("deleted_at", sa.DateTime, nullable=True),
        )
    if "live_overlay_events" not in tables:
        op.create_table(
            "live_overlay_events",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("owner_id", sa.String(64), nullable=False, index=True),
            sa.Column("timeline_id", sa.Integer, sa.ForeignKey("live_timelines.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("media_id", sa.String(32), nullable=False),
            sa.Column("template", sa.String(40), nullable=False, server_default=""),
            sa.Column("label", sa.String(200), nullable=False, server_default=""),
            sa.Column("start_ms", sa.Integer, nullable=False),
            sa.Column("duration_ms", sa.Integer, nullable=False),
            sa.Column("product_id", sa.Integer, nullable=True),
            sa.Column("x", sa.Float, nullable=False),
            sa.Column("y", sa.Float, nullable=False),
            sa.Column("width", sa.Float, nullable=False),
            sa.Column("height", sa.Float, nullable=False),
            sa.Column("z_index", sa.Integer, nullable=False, server_default="0"),
            sa.Column("opacity", sa.Float, nullable=False, server_default="1"),
            sa.Column("animation_in", sa.String(20), nullable=False, server_default="fade"),
            sa.Column("animation_in_ms", sa.Integer, nullable=False, server_default="300"),
            sa.Column("animation_out", sa.String(20), nullable=False, server_default="fade"),
            sa.Column("animation_out_ms", sa.Integer, nullable=False, server_default="300"),
            sa.Column("elements", sa.Text, nullable=False, server_default="[]"),
            sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("seq", sa.Integer, nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("updated_at", sa.DateTime, nullable=False),
        )


def downgrade():
    op.drop_table("live_overlay_events")
    op.drop_table("live_overlay_assets")
