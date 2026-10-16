"""Many-to-many media presenters

Revision ID: 20261017_0007
Revises: 20261016_0006
"""
from alembic import op
import sqlalchemy as sa
revision = "20261017_0007"
down_revision = "20261016_0006"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("media_presenters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="CASCADE"), nullable=False),
        sa.Column("presenter_id", sa.Integer(), sa.ForeignKey("presenters.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("media_id", "presenter_id", name="uq_media_presenter"))
    op.create_index("ix_media_presenters_media_id", "media_presenters", ["media_id"])
    op.create_index("ix_media_presenters_presenter_id", "media_presenters", ["presenter_id"])
    # Carry over the old single presenter link; media.presenter_id stays as a deprecated column.
    op.execute("INSERT INTO media_presenters (media_id, presenter_id) "
               "SELECT id, presenter_id FROM media WHERE presenter_id IN (SELECT id FROM presenters)")

def downgrade():
    op.drop_index("ix_media_presenters_presenter_id", table_name="media_presenters")
    op.drop_index("ix_media_presenters_media_id", table_name="media_presenters")
    op.drop_table("media_presenters")
