"""AI LIVE replies: structured product facts and the persistent answer bank.

Additive only (one column, one table); app/shop_schema.py applies the same at startup. Downgrade drops them
(saved facts and answers are lost).

Revision ID: 20261023_0014
Revises: 20261022_0013
"""
from alembic import op
import sqlalchemy as sa

revision = "20261023_0014"
down_revision = "20261022_0013"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "ai_facts" not in {c["name"] for c in sa.inspect(bind).get_columns("products")}:
        op.add_column("products", sa.Column("ai_facts", sa.Text, nullable=True))
    if not sa.inspect(bind).has_table("ai_answers"):
        op.create_table("ai_answers",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("owner_id", sa.String(64), nullable=False),
            sa.Column("product_id", sa.Integer, nullable=False),
            sa.Column("product_version", sa.String(32), nullable=False),
            sa.Column("question", sa.String(300), nullable=False),
            sa.Column("question_norm", sa.String(300), nullable=False),
            sa.Column("intent", sa.String(30), nullable=False),
            sa.Column("slots", sa.Text, nullable=False),
            sa.Column("say", sa.Text, nullable=False),
            sa.Column("text", sa.Text, nullable=False),
            sa.Column("source", sa.String(20), nullable=False),
            sa.Column("approved", sa.Boolean, nullable=False),
            sa.Column("uses", sa.Integer, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("owner_id", "product_id", "product_version", "question_norm", name="uq_ai_answers_question"))
    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("ai_answers")}
    if "ix_ai_answers_owner_id" not in indexes:
        op.create_index("ix_ai_answers_owner_id", "ai_answers", ["owner_id"])
    if "ix_ai_answers_product_id" not in indexes:
        op.create_index("ix_ai_answers_product_id", "ai_answers", ["product_id"])


def downgrade():
    op.drop_table("ai_answers")
    op.drop_column("products", "ai_facts")
