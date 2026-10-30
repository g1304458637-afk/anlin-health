from datetime import datetime, timezone, date
from sqlalchemy import String, Integer, Boolean, DateTime, Date, ForeignKey, JSON, Text, Float, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .crypto import EncryptedString
from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Community(Base):
    __tablename__ = "communities"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(16), default="resident", index=True)
    community_id: Mapped[int | None] = mapped_column(ForeignKey("communities.id"), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    wechat_openid: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    invite_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Brute-force defenses for password login.
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Optional TOTP factor; presence forces the code at password login.
    totp_secret: Mapped[str | None] = mapped_column(EncryptedString(200), nullable=True)
    totp_pending_secret: Mapped[str | None] = mapped_column(EncryptedString(200), nullable=True)
    totp_pending_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    totp_pending_failures: Mapped[int] = mapped_column(Integer, default=0)
    # Highest TOTP counter step consumed for the active factor; a shown code
    # cannot be replayed inside the accept window.
    totp_last_used_counter: Mapped[int] = mapped_column(Integer, default=0)
    # Raised on password change / global sign-out; older tokens stop working immediately.
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    password_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Resident(Base):
    __tablename__ = "residents"
    id: Mapped[int] = mapped_column(primary_key=True)
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"), index=True)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), unique=True, nullable=True, index=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(80))
    birth_year: Mapped[int] = mapped_column(Integer)
    sex: Mapped[str] = mapped_column(String(12), default="undisclosed")
    # Encrypted at rest when FIELD_ENCRYPTION_KEY is configured.
    phone: Mapped[str | None] = mapped_column(EncryptedString(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ClaimRequest(Base):
    """Offline-verified archive claim: name+phone alone never grants ownership."""
    __tablename__ = "claim_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("residents.id"), index=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending/approved/rejected
    stated_name: Mapped[str] = mapped_column(String(80))
    stated_phone: Mapped[str | None] = mapped_column(EncryptedString(200), nullable=True)
    name_matched: Mapped[bool] = mapped_column(Boolean, default=False)
    phone_matched: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConsentEvent(Base):
    __tablename__ = "consent_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("residents.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    purpose: Mapped[str] = mapped_column(String(64), default="community_care")
    granted: Mapped[bool] = mapped_column(Boolean)
    method: Mapped[str] = mapped_column(String(30))
    evidence_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Evidence review chain for paper-assisted events; resident-self events are auto-approved.
    review_status: Mapped[str] = mapped_column(String(16), default="approved")
    review_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FamilyGrant(Base):
    __tablename__ = "family_grants"
    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("residents.id"), index=True)
    grantee_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    can_profile: Mapped[bool] = mapped_column(Boolean, default=True)
    can_vitals: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Vital(Base):
    __tablename__ = "vitals"
    __table_args__ = (
        UniqueConstraint("resident_id", "actor_id", "client_request_id", name="uq_vital_client_request"),
        Index("ix_vital_resident_type_time", "resident_id", "type", "measured_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("residents.id"), index=True)
    type: Mapped[str] = mapped_column(String(32))
    systolic: Mapped[int | None] = mapped_column(Integer, nullable=True)
    diastolic: Mapped[int | None] = mapped_column(Integer, nullable=True)
    glucose_mmol_l: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    measurement_context: Mapped[str | None] = mapped_column(String(30), nullable=True)
    client_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    # Soft delete: health entries disappear from every view but remain auditable;
    # the original values are snapshotted into the audit log on deletion.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Questionnaire(Base):
    __tablename__ = "questionnaires"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer, default=1)
    category: Mapped[str] = mapped_column(String(30))
    questions: Mapped[list] = mapped_column(JSON)
    published: Mapped[bool] = mapped_column(Boolean, default=True)


class QuestionnaireResponse(Base):
    __tablename__ = "questionnaire_responses"
    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("residents.id"), index=True)
    questionnaire_id: Mapped[int] = mapped_column(ForeignKey("questionnaires.id"))
    questionnaire_version: Mapped[int] = mapped_column(Integer)
    answers: Mapped[dict] = mapped_column(JSON)
    completed_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Reminder(Base):
    __tablename__ = "reminders"
    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("residents.id"), index=True)
    type: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(120))
    due_date: Mapped[date] = mapped_column(Date, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))


class Visit(Base):
    __tablename__ = "visits"
    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("residents.id"), index=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    due_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="planned")
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[int] = mapped_column(Integer)
    # Optional JSON snapshot of the affected values (e.g. deleted vitals), so
    # "who removed which reading" stays answerable after the fact.
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RateEvent(Base):
    """Sliding-window hit for app.ratelimit; keys are SHA-256 hashes, never raw IPs."""
    __tablename__ = "rate_events"
    __table_args__ = (Index("ix_rate_window", "bucket", "key_hash", "at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bucket: Mapped[str] = mapped_column(String(64))
    key_hash: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RiskAlert(Base):
    """Non-diagnostic threshold notice raised when a reading is out of the
    common reference range. Values live on the linked vital; this row is the
    notification surface for staff and family."""
    __tablename__ = "risk_alerts"
    __table_args__ = (Index("ix_risk_alert_resident_created", "resident_id", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("residents.id"), index=True)
    vital_id: Mapped[int | None] = mapped_column(ForeignKey("vitals.id", ondelete="SET NULL"), nullable=True)
    metric: Mapped[str] = mapped_column(String(32))
    level: Mapped[str] = mapped_column(String(16), index=True)  # warning / critical
    value_text: Mapped[str] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(String(300))
    acknowledged_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Appointment(Base):
    """Resident booking request (community staff visit or doctor-visit accompaniment).
    Doctors are not system users in the pilot; a booking is an offline-coordinated request."""
    __tablename__ = "appointments"
    __table_args__ = (Index("ix_appointment_resident_created", "resident_id", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("residents.id"), index=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(16))  # staff / doctor
    preferred_date: Mapped[date] = mapped_column(Date)
    time_slot: Mapped[str] = mapped_column(String(16))  # morning / afternoon
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending/confirmed/completed/cancelled
    handled_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    response_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SingleUseToken(Base):
    """Server-side one-time tokens (e.g. WeChat login codes), stored hashed.

    Replacing the per-process dict keeps replay protection intact when the app
    runs more than one worker.
    """
    __tablename__ = "single_use_tokens"
    __table_args__ = (Index("ix_single_use_kind_created", "kind", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


Index("ix_family_active", FamilyGrant.resident_id, FamilyGrant.grantee_id)

# Register private report tables for create_all and Alembic metadata discovery.
from .report_models import MedicalReport, ReportVitalLink  # noqa: E402,F401
from .plan_models import HealthPlan, PlanOccurrence  # noqa: E402,F401
