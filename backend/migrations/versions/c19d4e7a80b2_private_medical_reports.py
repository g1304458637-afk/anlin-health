"""Private medical report blobs, review state, and imported vital trace.

Revision ID: c19d4e7a80b2
Revises: 6b3a2d87c4e1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.report_models import EncryptedText

revision: str = "c19d4e7a80b2"
down_revision: Union[str, None] = "6b3a2d87c4e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "medical_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("resident_id", sa.Integer(), sa.ForeignKey("residents.id"), nullable=False),
        sa.Column("uploader_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("storage_key", sa.String(64), nullable=False, unique=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("mime_type", sa.String(40), nullable=False),
        sa.Column("filename", sa.String(180), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("requested_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("requested_token_version", sa.Integer()),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error", sa.String(300)),
        sa.Column("candidate_json", EncryptedText()),
        sa.Column("reviewed_json", EncryptedText()),
        sa.Column("confirmed_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("resident_id", "sha256", name="uq_report_resident_sha256"),
    )
    op.create_index("ix_medical_reports_resident_id", "medical_reports", ["resident_id"])
    op.create_index("ix_medical_reports_status", "medical_reports", ["status"])
    op.create_table(
        "report_vital_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("medical_reports.id"), nullable=False),
        sa.Column("item_id", sa.String(80), nullable=False),
        sa.Column("vital_id", sa.Integer(), sa.ForeignKey("vitals.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("report_id", "item_id", name="uq_report_item_import"),
        sa.UniqueConstraint("vital_id", name="uq_report_vital"),
    )
    op.create_index("ix_report_vital_links_report_id", "report_vital_links", ["report_id"])


def downgrade() -> None:
    # Blob files and reviewed health data require an operator retention decision.
    count = op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM medical_reports"))
    if count:
        raise RuntimeError("Cannot downgrade while private reports exist")
    op.drop_index("ix_report_vital_links_report_id", table_name="report_vital_links")
    op.drop_table("report_vital_links")
    op.drop_index("ix_medical_reports_status", table_name="medical_reports")
    op.drop_index("ix_medical_reports_resident_id", table_name="medical_reports")
    op.drop_table("medical_reports")
