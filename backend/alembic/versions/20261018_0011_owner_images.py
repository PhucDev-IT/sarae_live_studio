"""Owner of each uploaded product/presenter photo, for signed image URLs.

Revision ID: 20261018_0011
Revises: 20261018_0010
"""
from alembic import op
import sqlalchemy as sa

revision = "20261018_0011"
down_revision = "20261018_0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("owner_images",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_owner_images_owner_id", "owner_images", ["owner_id"])


def downgrade():
    op.drop_index("ix_owner_images_owner_id", table_name="owner_images")
    op.drop_table("owner_images")
