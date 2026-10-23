"""Recurring health plans and independently tracked dated tasks.

Revision ID: d10a00000001
Revises: c19d4e7a80b2
"""
from alembic import op
import sqlalchemy as sa

revision = "d10a00000001"
down_revision = "c19d4e7a80b2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("health_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("resident_id", sa.Integer(), sa.ForeignKey("residents.id"), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("frequency", sa.String(10), nullable=False),
        sa.Column("weekdays", sa.JSON(), nullable=False),
        sa.Column("local_time", sa.String(5), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_health_plans_resident_id", "health_plans", ["resident_id"])
    op.create_index("ix_health_plans_status", "health_plans", ["status"])
    op.create_table("plan_occurrences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("health_plans.id"), nullable=False),
        sa.Column("resident_id", sa.Integer(), sa.ForeignKey("residents.id"), nullable=False),
        sa.Column("scheduled_date", sa.Date(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("cancellation_reason", sa.String(20), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acted_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("plan_id", "scheduled_date", name="uq_plan_scheduled_date"))
    for column in ("plan_id", "resident_id", "due_at", "status"):
        op.create_index("ix_plan_occurrences_" + column, "plan_occurrences", [column])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM health_plans")):
        raise RuntimeError("Cannot downgrade while health plans exist; export and review retention first")
    op.drop_table("plan_occurrences")
    op.drop_table("health_plans")
