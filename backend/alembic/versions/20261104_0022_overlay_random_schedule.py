"""Random overlay schedule of a timeline version.

Additive only; app/shop_schema.py and app/live_overlays.py ensure_schema apply the same at startup.
- live_overlay_schedules: one row per version (none = fixed / every 120 s / gap 20 s): `mode` fixed or random,
  `every_ms` (slot length: at most one appearance per slot) and `min_gap_ms` (pause between two appearances).
  Deleted with its timeline.
- live_overlay_events.show_ms: how long one appearance lasts in random mode (start..start+duration is then the window
  the overlay may show in). Existing overlays get 6000.
Downgrade drops the table and the column (versions are fixed again).

Revision ID: 20261104_0022
Revises: 20261103_0021
"""
from alembic import op
import sqlalchemy as sa

revision = "20261104_0022"
down_revision = "20261103_0021"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "live_overlay_schedules" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "live_overlay_schedules",
            sa.Column("timeline_id", sa.Integer, sa.ForeignKey("live_timelines.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("owner_id", sa.String(64), nullable=False, index=True),
            sa.Column("mode", sa.String(10), nullable=False, server_default="fixed"),
            sa.Column("every_ms", sa.Integer, nullable=False, server_default="120000"),
            sa.Column("min_gap_ms", sa.Integer, nullable=False, server_default="20000"),
            sa.Column("updated_at", sa.DateTime, nullable=False),
        )
    if "show_ms" not in {c["name"] for c in sa.inspect(bind).get_columns("live_overlay_events")}:
        op.add_column("live_overlay_events", sa.Column("show_ms", sa.Integer, nullable=False, server_default="6000"))


def downgrade():
    op.drop_column("live_overlay_events", "show_ms")
    op.drop_table("live_overlay_schedules")
