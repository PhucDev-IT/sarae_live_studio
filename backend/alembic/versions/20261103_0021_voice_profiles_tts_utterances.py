"""G-OmniVoice voice profiles and TTS utterances (docs/TTS_SERVER.md section 17).

Additive only; app/voice_profiles.py and app/tts_orchestrator.py ensure_schema create the same at startup.
- voice_profiles: an OWNER's reference voice. The WAV lives in the Media Library storage provider (provider + object
  id + SHA-256), never in the database; transcript (Whisper or manual), revision, QC and the voice-server sync state.
- tts_utterances: one clip Sarae decided to say with G-OmniVoice, linking owner, Agent, LIVE session, reply, voice
  revision, TTS job and Agent command. No text and no read token are stored.
Downgrade drops both tables (the WAV objects stay in storage and can be removed by an operator).

Revision ID: 20261103_0021
Revises: 20261101_0020
"""
from alembic import op
import sqlalchemy as sa

revision = "20261103_0021"
down_revision = "20261101_0020"
branch_labels = None
depends_on = None


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "voice_profiles" not in tables:
        op.create_table(
            "voice_profiles",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("owner_id", sa.String(64), nullable=False, index=True),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("storage_provider", sa.String(20), nullable=False),
            sa.Column("storage_object_id", sa.String(200), nullable=False),
            sa.Column("wav_sha256", sa.String(64), nullable=False),
            sa.Column("wav_bytes", sa.Integer, nullable=False),
            sa.Column("sample_rate", sa.Integer, nullable=False),
            sa.Column("channels", sa.Integer, nullable=False),
            sa.Column("duration_s", sa.Float, nullable=False),
            sa.Column("transcript_source", sa.String(10), nullable=False, server_default="asr"),
            sa.Column("transcript_manual", sa.Text, nullable=True),
            sa.Column("transcript_asr", sa.Text, nullable=True),
            sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
            sa.Column("qc_json", sa.Text, nullable=False, server_default="{}"),
            sa.Column("tts_sync_state", sa.String(20), nullable=False, server_default="PENDING"),
            sa.Column("tts_synced_revision", sa.Integer, nullable=True),
            sa.Column("tts_rev", sa.String(32), nullable=True),
            sa.Column("tts_compat", sa.String(40), nullable=True),
            sa.Column("tts_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("tts_error", sa.String(200), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "tts_utterances" not in tables:
        op.create_table(
            "tts_utterances",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("owner_id", sa.String(64), nullable=False, index=True),
            sa.Column("agent_id", sa.String(80), nullable=False, index=True),
            sa.Column("session_id", sa.Integer, nullable=True, index=True),
            sa.Column("reply_id", sa.Integer, nullable=True),
            sa.Column("source", sa.String(20), nullable=False),
            sa.Column("part", sa.String(10), nullable=False),
            sa.Column("voice_id", sa.String(32), nullable=False),
            sa.Column("voice_rev", sa.Integer, nullable=False),
            sa.Column("text_sha256", sa.String(64), nullable=False),
            sa.Column("chars", sa.Integer, nullable=False),
            sa.Column("priority", sa.String(10), nullable=False),
            sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("state", sa.String(20), nullable=False, server_default="NEW", index=True),
            sa.Column("tts_job_id", sa.String(64), nullable=True),
            sa.Column("tts_job_state", sa.String(20), nullable=True),
            sa.Column("command_id", sa.String(64), nullable=True),
            sa.Column("error_code", sa.String(40), nullable=True),
            sa.Column("error_message", sa.String(200), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade():
    op.drop_table("tts_utterances")
    op.drop_table("voice_profiles")
