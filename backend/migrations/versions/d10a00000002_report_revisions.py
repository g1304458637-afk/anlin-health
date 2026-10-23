"""Append-only report revisions and explicit stale import tracking."""
from alembic import op
import sqlalchemy as sa
from app.report_models import EncryptedText

revision = "d10a00000002"
down_revision = "d10a00000001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "report_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("medical_reports.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("actor_name", EncryptedText(), nullable=False),
        sa.Column("reason", EncryptedText(), nullable=False),
        sa.Column("review_json", EncryptedText(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("report_id", "revision", name="uq_report_revision"),
    )
    op.create_index("ix_report_revisions_report_id", "report_revisions", ["report_id"])
    with op.batch_alter_table("report_vital_links") as batch:
        batch.add_column(sa.Column("imported_revision", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("imported_snapshot_json", EncryptedText(), nullable=True))
        batch.add_column(sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("flagged_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM report_revisions")):
        raise RuntimeError("Cannot discard report revision history")
    with op.batch_alter_table("report_vital_links") as batch:
        batch.drop_column("flagged_at")
        batch.drop_column("needs_review")
        batch.drop_column("imported_snapshot_json")
        batch.drop_column("imported_revision")
    op.drop_index("ix_report_revisions_report_id", table_name="report_revisions")
    op.drop_table("report_revisions")
