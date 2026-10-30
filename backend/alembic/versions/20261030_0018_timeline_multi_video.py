"""Single Video Timeline with several videos: the session's video list and order, markers per video.

Additive only; app/shop_schema.py and app/live_timeline.py ensure_schema apply the same at startup.
- live_sessions.video_ids: JSON list of Media Library ids in the operator's order (empty = [main_media_id]);
- live_sessions.video_order: sequential / random / shuffle (as the ADVANCED_LEGACY video order);
- live_timeline_markers.media_id: the video a marker belongs to (its position is in that video). Existing markers
  get their timeline's video.
Downgrade drops the three columns (markers then belong to their timeline's video again).

Revision ID: 20261030_0018
Revises: 20261029_0017
"""
from alembic import op
import sqlalchemy as sa

revision = "20261030_0018"
down_revision = "20261029_0017"
branch_labels = None
depends_on = None

SESSION_COLUMNS = (
    sa.Column("video_ids", sa.Text, nullable=True),
    sa.Column("video_order", sa.String(20), nullable=False, server_default="sequential"),
)


def upgrade():
    bind = op.get_bind()
    present = {c["name"] for c in sa.inspect(bind).get_columns("live_sessions")}
    for column in SESSION_COLUMNS:
        if column.name not in present:
            op.add_column("live_sessions", column.copy())
    if "media_id" not in {c["name"] for c in sa.inspect(bind).get_columns("live_timeline_markers")}:
        op.add_column("live_timeline_markers", sa.Column("media_id", sa.String(32), nullable=True))
    op.execute("""UPDATE live_timeline_markers SET media_id =
        (SELECT media_id FROM live_timelines WHERE live_timelines.id = live_timeline_markers.timeline_id)
        WHERE media_id IS NULL""")


def downgrade():
    op.drop_column("live_timeline_markers", "media_id")
    for column in reversed(SESSION_COLUMNS):
        op.drop_column("live_sessions", column.name)
