"""Scope every customer table to an OWNER (multi-tenant SaaS).

Rebuilds the legacy single-tenant tables with an `owner_id` column. Existing rows are DROPPED: the
product decided to start clean before production, and old rows have no owner to be assigned to.

Revision ID: 20261018_0009
Revises: 20261017_0008
"""
from alembic import op
import sqlalchemy as sa

revision = "20261018_0009"
down_revision = "20261017_0008"
branch_labels = None
depends_on = None

LEGACY_TABLES = ["live_session_events", "live_session_products", "live_sessions", "live_playlists",
                 "media_presenters", "product_live_videos", "media", "presenters", "products"]


def owner_column():
    return sa.Column("owner_id", sa.String(64), nullable=False)


def upgrade():
    for table in LEGACY_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")

    op.create_table("products",
        sa.Column("id", sa.Integer(), primary_key=True),
        owner_column(),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "sku", name="uq_products_owner_sku"))
    op.create_index("ix_products_owner_id", "products", ["owner_id"])
    op.create_index("ix_products_name", "products", ["name"])

    op.create_table("presenters",
        sa.Column("id", sa.Integer(), primary_key=True),
        owner_column(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("avatar_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_presenters_owner_id", "presenters", ["owner_id"])
    op.create_index("ix_presenters_name", "presenters", ["name"])

    op.create_table("media",
        sa.Column("id", sa.Integer(), primary_key=True),
        owner_column(),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("stored_name", sa.String(255), nullable=False, unique=True),
        sa.Column("local_path", sa.Text(), nullable=True, unique=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("fps", sa.Float(), nullable=True),
        sa.Column("video_codec", sa.String(40), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True),
        sa.Column("presenter_id", sa.Integer(), sa.ForeignKey("presenters.id", ondelete="SET NULL"), nullable=True),
        sa.Column("tags", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_media_owner_id", "media", ["owner_id"])

    op.create_table("product_live_videos",
        sa.Column("id", sa.Integer(), primary_key=True),
        owner_column(),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.UniqueConstraint("product_id", "media_id", name="uq_product_live_video"))
    op.create_index("ix_product_live_videos_owner_id", "product_live_videos", ["owner_id"])
    op.create_index("ix_product_live_videos_product_id", "product_live_videos", ["product_id"])

    op.create_table("media_presenters",
        sa.Column("id", sa.Integer(), primary_key=True),
        owner_column(),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="CASCADE"), nullable=False),
        sa.Column("presenter_id", sa.Integer(), sa.ForeignKey("presenters.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("media_id", "presenter_id", name="uq_media_presenter"))
    op.create_index("ix_media_presenters_owner_id", "media_presenters", ["owner_id"])
    op.create_index("ix_media_presenters_media_id", "media_presenters", ["media_id"])
    op.create_index("ix_media_presenters_presenter_id", "media_presenters", ["presenter_id"])

    op.create_table("live_playlists",
        sa.Column("id", sa.Integer(), primary_key=True),
        owner_column(),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("media_ids", sa.Text(), nullable=False))
    op.create_index("ix_live_playlists_owner_id", "live_playlists", ["owner_id"])

    op.create_table("live_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        owner_column(),
        sa.Column("agent_id", sa.String(80), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("current_product_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True))
    op.create_index("ix_live_sessions_owner_id", "live_sessions", ["owner_id"])

    op.create_table("live_session_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("live_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.UniqueConstraint("session_id", "product_id", name="uq_live_session_product"))
    op.create_index("ix_live_session_products_session_id", "live_session_products", ["session_id"])

    op.create_table("live_session_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("live_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_live_session_events_session_id", "live_session_events", ["session_id"])


def downgrade():
    raise RuntimeError("20261018_0009 dropped single-tenant data; restore a backup of backend/data instead of downgrading")
