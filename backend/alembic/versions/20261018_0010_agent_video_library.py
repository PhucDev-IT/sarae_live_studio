"""Videos live on the OWNER's Windows Agent: links and tags are keyed by (agent_id, media_id).

Drops server-side MP4 records (`media`) and server playlists (`live_playlists`, replaced by agent playlists), and
rebuilds the product/presenter video links. Rows are dropped: not in production yet.

Revision ID: 20261018_0010
Revises: 20261018_0009
"""
from alembic import op
import sqlalchemy as sa

revision = "20261018_0010"
down_revision = "20261018_0009"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("media_presenters", "product_live_videos", "media", "live_playlists"):
        op.execute(f"DROP TABLE IF EXISTS {table}")

    op.create_table("product_live_videos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.String(80), nullable=False),
        sa.Column("media_id", sa.String(64), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.UniqueConstraint("product_id", "agent_id", "media_id", name="uq_product_live_video"))
    op.create_index("ix_product_live_videos_owner_id", "product_live_videos", ["owner_id"])
    op.create_index("ix_product_live_videos_product_id", "product_live_videos", ["product_id"])

    op.create_table("media_presenters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("presenter_id", sa.Integer(), sa.ForeignKey("presenters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.String(80), nullable=False),
        sa.Column("media_id", sa.String(64), nullable=False),
        sa.UniqueConstraint("presenter_id", "agent_id", "media_id", name="uq_media_presenter"))
    op.create_index("ix_media_presenters_owner_id", "media_presenters", ["owner_id"])
    op.create_index("ix_media_presenters_presenter_id", "media_presenters", ["presenter_id"])

    op.create_table("video_tags",
        sa.Column("agent_id", sa.String(80), primary_key=True),
        sa.Column("media_id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("tags", sa.Text(), nullable=False))
    op.create_index("ix_video_tags_owner_id", "video_tags", ["owner_id"])


def downgrade():
    raise RuntimeError("20261018_0010 dropped server-side media records; restore a backup of backend/data instead of downgrading")
