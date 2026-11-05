"""Overlay schedule modes fixed | window | random.

Format 1 (20261104_0022) stored the window mode (an overlay may show inside start..start+duration) as "random"; the new
"random" mode ignores overlay times (product overlays show while their product is pinned).
- live_overlay_schedules: rows with mode "random" become "window", then the column `mode_version` (2) is added. The
  column marks the conversion as done: app/live_overlays.py ensure_schema runs the same at startup only while the
  column is missing, so whichever runs first converts the rows exactly once.
Downgrade maps the modes back (random → fixed, window → random) and drops the column.

Revision ID: 20261105_0023
Revises: 20261104_0022
"""
from alembic import op
import sqlalchemy as sa

revision = "20261105_0023"
down_revision = "20261104_0022"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "mode_version" not in {c["name"] for c in sa.inspect(bind).get_columns("live_overlay_schedules")}:
        op.execute("UPDATE live_overlay_schedules SET mode = 'window' WHERE mode = 'random'")
        op.add_column("live_overlay_schedules", sa.Column("mode_version", sa.Integer, nullable=False, server_default="2"))


def downgrade():
    op.execute("UPDATE live_overlay_schedules SET mode = 'fixed' WHERE mode = 'random'")
    op.execute("UPDATE live_overlay_schedules SET mode = 'random' WHERE mode = 'window'")
    op.drop_column("live_overlay_schedules", "mode_version")
