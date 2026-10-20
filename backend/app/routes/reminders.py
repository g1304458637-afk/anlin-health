from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from ..db import get_db
from ..models import User, Resident, Reminder
from ..deps import auth_user, check_access, audit
from ..schemas import ReminderIn, ReminderOut

router = APIRouter(prefix="/api", tags=["reminders"])


@router.get("/residents/{resident_id}/reminders", response_model=list[ReminderOut])
def list_reminders(resident_id: int, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    check_access(db, user, db.get(Resident, resident_id), scope="reminders")
    return db.scalars(select(Reminder).where(Reminder.resident_id == resident_id).order_by(Reminder.due_date.asc(), Reminder.id.desc()).limit(100)).all()


@router.post("/residents/{resident_id}/reminders", response_model=ReminderOut, status_code=201)
def create_reminder(resident_id: int, body: ReminderIn, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    check_access(db, user, db.get(Resident, resident_id), scope="reminders", write=True)
    reminder = Reminder(resident_id=resident_id, actor_id=user.id, **body.model_dump())
    db.add(reminder)
    db.flush()
    audit(db, user, "create_reminder", "reminder", reminder.id)
    db.commit()
    return reminder


@router.patch("/residents/{resident_id}/reminders/{reminder_id}/complete", response_model=ReminderOut)
def complete_reminder(resident_id: int, reminder_id: int, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    check_access(db, user, db.get(Resident, resident_id), scope="reminders", write=True)
    reminder = db.get(Reminder, reminder_id)
    if not reminder or reminder.resident_id != resident_id:
        raise HTTPException(404, "提醒不存在")
    if reminder.completed_at is None:
        reminder.completed_at = datetime.now(timezone.utc)
        audit(db, user, "complete_reminder", "reminder", reminder.id)
        db.commit()
    return reminder
