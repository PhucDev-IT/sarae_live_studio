"""Media Library: cover thumbnails and presenter links.

Additive only (two new tables). Downgrade drops them (cover images and presenter links of cloud media are lost).

Revision ID: 20261022_0013
Revises: 20261021_0012
"""
from alembic import op
import sqlalchemy as sa

revision = "20261022_0013"
down_revision = "20261021_0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("media_thumbnails",
        sa.Column("media_id", sa.String(32), sa.ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("mime_type", sa.String(30), nullable=False),
        sa.Column("content", sa.LargeBinary, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_media_thumbnails_owner_id", "media_thumbnails", ["owner_id"])
    op.create_table("media_item_presenters",
        sa.Column("media_id", sa.String(32), sa.ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("presenter_id", sa.Integer, sa.ForeignKey("presenters.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False))
    op.create_index("ix_media_item_presenters_owner_id", "media_item_presenters", ["owner_id"])


def downgrade():
    op.drop_table("media_item_presenters")
    op.drop_table("media_thumbnails")
