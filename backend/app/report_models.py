"""Private report records and the trace from reviewed findings to vital rows."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .crypto import decrypt_value, encrypt_value
from .db import Base
from .models import utcnow
from sqlalchemy.types import TypeDecorator


class EncryptedText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt_value(value)

    def process_result_value(self, value, dialect):
        return None if value is None else decrypt_value(value)


class MedicalReport(Base):
    __tablename__ = "medical_reports"
    __table_args__ = (UniqueConstraint("resident_id", "sha256", name="uq_report_resident_sha256"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("residents.id"), index=True)
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    storage_key: Mapped[str] = mapped_column(String(64), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    mime_type: Mapped[str] = mapped_column(String(40))
    filename: Mapped[str] = mapped_column(String(180))
    page_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="uploaded", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    requested_token_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    candidate_json: Mapped[str | None] = mapped_column(EncryptedText(), nullable=True)
    reviewed_json: Mapped[str | None] = mapped_column(EncryptedText(), nullable=True)
    confirmed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ReportVitalLink(Base):
    __tablename__ = "report_vital_links"
    __table_args__ = (
        UniqueConstraint("report_id", "item_id", name="uq_report_item_import"),
        UniqueConstraint("vital_id", name="uq_report_vital"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("medical_reports.id"), index=True)
    item_id: Mapped[str] = mapped_column(String(80))
    vital_id: Mapped[int | None] = mapped_column(ForeignKey("vitals.id", ondelete="SET NULL"), nullable=True)
