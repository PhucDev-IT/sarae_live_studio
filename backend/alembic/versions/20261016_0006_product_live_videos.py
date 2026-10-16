"""Product LIVE selected videos

Revision ID: 20261016_0006
Revises: 20261016_0005
"""
from alembic import op
import sqlalchemy as sa
revision = "20261016_0006"
down_revision = "20261016_0005"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("product_live_videos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.UniqueConstraint("product_id", "media_id", name="uq_product_live_video"))
    op.create_index("ix_product_live_videos_product_id", "product_live_videos", ["product_id"])

def downgrade():
    op.drop_index("ix_product_live_videos_product_id", table_name="product_live_videos")
    op.drop_table("product_live_videos")
