"""Resident booking requests: community staff visit or doctor-visit accompaniment.

Doctors are not system users in this pilot; a booking is an offline-coordinated
request that staff confirm, complete or cancel in the console.
"""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import audit, auth_user, check_access, is_owner, require_roles
from ..models import Appointment, Resident, User
from ..reporting import visible_resident_ids

router = APIRouter(prefix="/api", tags=["appointments"])

_KINDS = {"staff": "社区工作人员上门", "doctor": "医生门诊陪同"}
_SLOTS = {"morning": "上午", "afternoon": "下午"}
_MAX_DAYS_AHEAD = 60


class AppointmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern="^(staff|doctor)$")
    preferred_date: date
    time_slot: str = Field(pattern="^(morning|afternoon)$")
    note: str | None = Field(default=None, max_length=300)


class AppointmentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_note: str | None = Field(default=None, max_length=300)


def _out(db: Session, a: Appointment) -> dict:
    resident = db.get(Resident, a.resident_id)
    handler = db.get(User, a.handled_by_id) if a.handled_by_id else None
    return {"id": a.id, "resident_id": a.resident_id, "resident_name": resident.name if resident else None,
            "kind": a.kind, "kind_label": _KINDS.get(a.kind, a.kind),
            "preferred_date": a.preferred_date.isoformat(), "time_slot": a.time_slot,
            "time_slot_label": _SLOTS.get(a.time_slot, a.time_slot),
            "note": a.note, "status": a.status, "response_note": a.response_note,
            "handled_by": handler.display_name if handler else None,
            "created_at": a.created_at, "updated_at": a.updated_at}


def _validate_date(preferred_date: date) -> None:
    today = datetime.now(timezone.utc).date()
    if preferred_date < today or preferred_date > today + timedelta(days=_MAX_DAYS_AHEAD):
        raise HTTPException(422, f"预约日期需在今天起的 {_MAX_DAYS_AHEAD} 天内")


@router.post("/residents/{resident_id}/appointments", status_code=201)
def create_appointment(resident_id: int, body: AppointmentIn,
                       user: User = Depends(auth_user), db: Session = Depends(get_db)):
    """Residents book for themselves only."""
    resident = check_access(db, user, db.get(Resident, resident_id), scope="profile", write=True)
    if not is_owner(user, resident):
        raise HTTPException(403, "仅档案本人可预约")
    _validate_date(body.preferred_date)
    booking = Appointment(resident_id=resident_id, requester_id=user.id, **body.model_dump())
    db.add(booking)
    db.flush()
    audit(db, user, "appointment_created", "appointment", booking.id,
          {"kind": booking.kind, "date": booking.preferred_date.isoformat(), "slot": booking.time_slot})
    db.commit()
    return _out(db, booking)


@router.get("/residents/{resident_id}/appointments")
def my_appointments(resident_id: int, response: Response,
                    user: User = Depends(auth_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    resident = check_access(db, user, db.get(Resident, resident_id), scope="profile")
    if not is_owner(user, resident):
        raise HTTPException(403, "仅档案本人可查看预约")
    rows = db.scalars(select(Appointment).where(Appointment.resident_id == resident_id)
                      .order_by(Appointment.created_at.desc(), Appointment.id.desc()).limit(50)).all()
    return [_out(db, a) for a in rows]


@router.post("/residents/{resident_id}/appointments/{appointment_id}/cancel")
def cancel_appointment(resident_id: int, appointment_id: int,
                       user: User = Depends(auth_user), db: Session = Depends(get_db)):
    resident = check_access(db, user, db.get(Resident, resident_id), scope="profile", write=True)
    if not is_owner(user, resident):
        raise HTTPException(403, "仅档案本人可取消预约")
    booking = db.get(Appointment, appointment_id)
    if not booking or booking.resident_id != resident_id:
        raise HTTPException(404, "预约不存在")
    if booking.status not in {"pending", "confirmed"}:
        raise HTTPException(409, "当前状态不能取消")
    changed = db.execute(update(Appointment)
                         .where(Appointment.id == booking.id, Appointment.status.in_(["pending", "confirmed"]))
                         .values(status="cancelled", updated_at=datetime.now(timezone.utc)))
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "预约状态已变化")
    audit(db, user, "appointment_cancelled", "appointment", booking.id)
    db.commit()
    db.refresh(booking)
    return _out(db, booking)


@router.get("/community/appointments")
def community_appointments(response: Response, status: str = Query(default="pending",
                           pattern="^(pending|confirmed|completed|cancelled|all)$"),
                           limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
                           user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    visible = visible_resident_ids(user.community_id)
    query = select(Appointment).where(Appointment.resident_id.in_(visible))
    if status != "all":
        query = query.where(Appointment.status == status)
    rows = db.scalars(query.order_by(Appointment.updated_at.desc(), Appointment.id.desc())
                      .offset(offset).limit(limit)).all()
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    return {"items": [_out(db, a) for a in rows], "total": total, "limit": limit, "offset": offset}


def _staff_action(appointment_id: int, user: User, db: Session, action: str, response_note: str | None):
    booking = db.get(Appointment, appointment_id)
    if not booking:
        raise HTTPException(404, "预约不存在")
    resident = db.get(Resident, booking.resident_id)
    if not resident or resident.community_id != user.community_id:
        raise HTTPException(404, "预约不存在")
    transitions = {"confirm": ({"pending"}, "confirmed"), "complete": ({"confirmed"}, "completed"),
                   "cancel": ({"pending", "confirmed"}, "cancelled")}
    allowed, target = transitions[action]
    if booking.status not in allowed:
        raise HTTPException(409, f"当前状态（{booking.status}）不能{action}")
    changed = db.execute(update(Appointment)
                         .where(Appointment.id == booking.id, Appointment.status.in_(allowed))
                         .values(status=target, handled_by_id=user.id,
                                 response_note=response_note if response_note is not None else booking.response_note,
                                 updated_at=datetime.now(timezone.utc)))
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "预约状态已变化，请刷新")
    audit(db, user, f"appointment_{action}ed", "appointment", booking.id)
    db.commit()
    db.refresh(booking)
    return _out(db, booking)


@router.post("/community/appointments/{appointment_id}/confirm")
def confirm_appointment(appointment_id: int, body: AppointmentAction,
                        user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    return _staff_action(appointment_id, user, db, "confirm", body.response_note)


@router.post("/community/appointments/{appointment_id}/complete")
def complete_appointment(appointment_id: int, body: AppointmentAction,
                         user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    return _staff_action(appointment_id, user, db, "complete", body.response_note)


@router.post("/community/appointments/{appointment_id}/cancel")
def cancel_appointment_staff(appointment_id: int, body: AppointmentAction,
                             user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    return _staff_action(appointment_id, user, db, "cancel", body.response_note)
