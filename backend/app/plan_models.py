"""Recurring plans and their dated tasks, separate from one-off reminders."""
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
def utcnow():
    return datetime.now(timezone.utc)


class HealthPlan(Base):
    __tablename__ = "health_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("residents.id"), index=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(30))
    frequency: Mapped[str] = mapped_column(String(10))
    weekdays: Mapped[list] = mapped_column(JSON, default=list)
    local_time: Mapped[str] = mapped_column(String(5))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PlanOccurrence(Base):
    __tablename__ = "plan_occurrences"
    __table_args__ = (UniqueConstraint("plan_id", "scheduled_date", name="uq_plan_scheduled_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("health_plans.id"), index=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("residents.id"), index=True)
    scheduled_date: Mapped[date] = mapped_column(Date)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acted_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
