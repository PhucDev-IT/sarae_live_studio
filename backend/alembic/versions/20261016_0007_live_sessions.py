"""Persist LIVE sessions and product queues.

Revision ID: 20261016_0007
Revises: 20261016_0006
"""
from alembic import op
import sqlalchemy as sa

revision = "20261016_0007"
down_revision = "20261016_0006"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("live_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("current_product_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True))
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
    op.drop_index("ix_live_session_events_session_id", table_name="live_session_events")
    op.drop_table("live_session_events")
    op.drop_index("ix_live_session_products_session_id", table_name="live_session_products")
    op.drop_table("live_session_products")
    op.drop_table("live_sessions")
