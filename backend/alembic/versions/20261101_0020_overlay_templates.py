"""Overlay templates saved by an owner, reusable in every session.

Additive only (one new table); app/live_overlays.py ensure_schema creates the same at startup.
- live_overlay_templates: name, free `template` label, description and the arrangement (`content` JSON: layout,
  elements, animations, duration). Not tied to a product.
Downgrade drops the table (overlays made from a template keep their own copy).

Revision ID: 20261101_0020
Revises: 20261031_0019
"""
from alembic import op
import sqlalchemy as sa

revision = "20261101_0020"
down_revision = "20261031_0019"
branch_labels = None
depends_on = None


def upgrade():
    if "live_overlay_templates" not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table(
            "live_overlay_templates",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("owner_id", sa.String(64), nullable=False, index=True),
            sa.Column("name", sa.String(80), nullable=False),
            sa.Column("template", sa.String(40), nullable=False, server_default=""),
            sa.Column("description", sa.String(300), nullable=False, server_default=""),
            sa.Column("content", sa.Text, nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("updated_at", sa.DateTime, nullable=False),
        )


def downgrade():
    op.drop_table("live_overlay_templates")
