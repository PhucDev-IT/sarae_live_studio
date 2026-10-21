"""Centralized Media Library: system storage credential, media, storage objects, uploads, product↔media.

Additive only: no existing table (agent_media, product_live_videos, video_tags…) is changed — that data stays
LOCAL_LEGACY. Downgrade drops only these new tables; take a DB backup before downgrading (it deletes media metadata).

Revision ID: 20261021_0012
Revises: 20261018_0011
"""
from alembic import op
import sqlalchemy as sa

revision = "20261021_0012"
down_revision = "20261018_0011"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade():
    op.create_table("storage_credentials",
        sa.Column("provider", sa.String(20), primary_key=True),
        sa.Column("account_id", sa.String(100), nullable=False),
        sa.Column("account_email", sa.String(320), nullable=False),
        sa.Column("scopes", sa.Text, nullable=False),
        sa.Column("refresh_token_enc", sa.Text),
        sa.Column("access_token_enc", sa.Text),
        sa.Column("access_expires_at", TS),
        sa.Column("root_container_id", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_error_code", sa.String(60)),
        sa.Column("connected_by", sa.String(64), nullable=False),
        sa.Column("connected_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False))
    op.create_table("storage_oauth_states",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("admin_id", sa.String(64), nullable=False),
        sa.Column("verifier_enc", sa.Text, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("used_at", TS))
    op.create_table("storage_owner_containers",
        sa.Column("owner_id", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(20), primary_key=True),
        sa.Column("container_id", sa.String(200), nullable=False),
        sa.Column("created_at", TS, nullable=False))
    op.create_table("media_items",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("provider_file_id", sa.String(200), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64)),
        sa.Column("md5", sa.String(32)),
        sa.Column("duration_seconds", sa.Float),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("notes", sa.Text, nullable=False),
        sa.Column("tags", sa.Text, nullable=False),
        sa.Column("deleted_at", TS),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("provider", "provider_file_id", name="uq_media_items_provider_file"))
    op.create_index("ix_media_items_owner_id", "media_items", ["owner_id"])
    op.create_table("media_storage_objects",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("media_id", sa.String(32), sa.ForeignKey("media_items.id"), nullable=False),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("provider_file_id", sa.String(200), nullable=False),
        sa.Column("sha256", sa.String(64)),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(60)),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("retired_at", TS),
        sa.UniqueConstraint("provider", "provider_file_id", name="uq_media_objects_provider_file"))
    op.create_index("ix_media_storage_objects_media_id", "media_storage_objects", ["media_id"])
    op.create_index("ix_media_storage_objects_owner_id", "media_storage_objects", ["owner_id"])
    op.create_table("media_uploads",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("client_upload_id", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("received_bytes", sa.BigInteger, nullable=False),
        sa.Column("session_enc", sa.Text),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("media_id", sa.String(32)),
        sa.Column("error_code", sa.String(60)),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("owner_id", "client_upload_id", name="uq_media_uploads_client"))
    op.create_index("ix_media_uploads_owner_id", "media_uploads", ["owner_id"])
    op.create_table("product_media",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("product_id", sa.Integer, sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_id", sa.String(32), sa.ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sort_order", sa.Integer, nullable=False),
        sa.UniqueConstraint("product_id", "media_id", name="uq_product_media"))
    op.create_index("ix_product_media_owner_id", "product_media", ["owner_id"])
    op.create_index("ix_product_media_product_id", "product_media", ["product_id"])
    op.create_index("ix_product_media_media_id", "product_media", ["media_id"])


def downgrade():
    for table in ("product_media", "media_uploads", "media_storage_objects", "media_items",
                  "storage_owner_containers", "storage_oauth_states", "storage_credentials"):
        op.drop_table(table)
