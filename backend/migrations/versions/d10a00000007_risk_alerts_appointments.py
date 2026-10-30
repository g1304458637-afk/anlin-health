"""Risk alert notices and resident appointment bookings."""
from alembic import op
import sqlalchemy as sa


revision = "d10a00000007"
down_revision = "d10a00000006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "risk_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("resident_id", sa.Integer(), nullable=False),
        sa.Column("vital_id", sa.Integer(), nullable=True),
        sa.Column("metric", sa.String(length=32), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("value_text", sa.String(length=120), nullable=False),
        sa.Column("message", sa.String(length=300), nullable=False),
        sa.Column("acknowledged_by_id", sa.Integer(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["resident_id"], ["residents.id"]),
        sa.ForeignKeyConstraint(["vital_id"], ["vitals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["acknowledged_by_id"], ["users.id"]),
    )
    op.create_index("ix_risk_alerts_resident_id", "risk_alerts", ["resident_id"])
    op.create_index("ix_risk_alerts_level", "risk_alerts", ["level"])
    op.create_index("ix_risk_alert_resident_created", "risk_alerts", ["resident_id", "created_at"])
    op.create_table(
        "appointments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("resident_id", sa.Integer(), nullable=False),
        sa.Column("requester_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("preferred_date", sa.Date(), nullable=False),
        sa.Column("time_slot", sa.String(length=16), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("handled_by_id", sa.Integer(), nullable=True),
        sa.Column("response_note", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["resident_id"], ["residents.id"]),
        sa.ForeignKeyConstraint(["requester_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["handled_by_id"], ["users.id"]),
    )
    op.create_index("ix_appointments_resident_id", "appointments", ["resident_id"])
    op.create_index("ix_appointments_status", "appointments", ["status"])
    op.create_index("ix_appointment_resident_created", "appointments", ["resident_id", "created_at"])


def downgrade():
    op.drop_table("appointments")
    op.drop_table("risk_alerts")
