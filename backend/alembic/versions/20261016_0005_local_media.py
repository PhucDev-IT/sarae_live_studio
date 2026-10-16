"""Add local folder path to media records.

Revision ID: 20261016_0005
Revises: 20261016_0004
"""
from alembic import op
import sqlalchemy as sa

revision = "20261016_0005"
down_revision = "20261016_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("media", sa.Column("local_path", sa.Text(), nullable=True))
    op.create_index("ix_media_local_path", "media", ["local_path"], unique=True)


def downgrade():
    op.drop_index("ix_media_local_path", table_name="media")
    op.drop_column("media", "local_path")
