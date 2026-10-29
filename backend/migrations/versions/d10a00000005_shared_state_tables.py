"""Shared-state tables: cross-process rate limiting and one-time token replay guard."""
from alembic import op
import sqlalchemy as sa


revision = "d10a00000005"
down_revision = "d10a00000004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rate_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bucket", sa.String(length=64), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rate_window", "rate_events", ["bucket", "key_hash", "at"])
    op.create_table(
        "single_use_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_single_use_token_hash"),
    )
    op.create_index("ix_single_use_kind_created", "single_use_tokens", ["kind", "created_at"])


def downgrade():
    op.drop_index("ix_single_use_kind_created", table_name="single_use_tokens")
    op.drop_table("single_use_tokens")
    op.drop_index("ix_rate_window", table_name="rate_events")
    op.drop_table("rate_events")
