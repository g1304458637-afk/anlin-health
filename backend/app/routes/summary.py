"""Resident-facing summaries: in-app weekly digest and CSV data export."""
import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import audit, auth_user, check_access
from ..models import Appointment, Resident, RiskAlert, User, Vital

router = APIRouter(prefix="/api", tags=["resident summary"])


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


@router.get("/residents/{resident_id}/weekly-report")
def weekly_report(resident_id: int, response: Response,
                  user: User = Depends(auth_user), db: Session = Depends(get_db)):
    """A gentle in-app digest of the last 7 days. Same access rule as vitals."""
    response.headers["Cache-Control"] = "no-store"
    check_access(db, user, db.get(Resident, resident_id), scope="vitals")
    since = _since(7)
    rows = db.scalars(select(Vital).where(
        Vital.resident_id == resident_id, Vital.deleted_at.is_(None), Vital.measured_at >= since)
        .order_by(Vital.measured_at)).all()

    def stats(metric, extractor):
        values = [extractor(r) for r in rows if r.type == metric and extractor(r) is not None]
        if not values:
            return None
        return {"count": len(values), "avg": round(sum(values) / len(values), 1),
                "min": min(values), "max": max(values)}

    bp = stats("blood_pressure", lambda r: r.systolic)
    bp_dia = stats("blood_pressure", lambda r: r.diastolic)
    if bp and bp_dia:
        bp["avg_dia"], bp["min_dia"], bp["max_dia"] = bp_dia["avg"], bp_dia["min"], bp_dia["max"]
    alerts = db.scalars(select(RiskAlert).where(
        RiskAlert.resident_id == resident_id, RiskAlert.created_at >= since)
        .order_by(RiskAlert.created_at.desc())).all()
    appointments = db.scalars(select(Appointment).where(
        Appointment.resident_id == resident_id, Appointment.created_at >= since)
        .order_by(Appointment.created_at.desc())).all()
    upcoming = db.scalar(select(Appointment).where(
        Appointment.resident_id == resident_id, Appointment.status.in_(["pending", "confirmed"]),
        Appointment.preferred_date >= datetime.now(timezone.utc).date())
        .order_by(Appointment.preferred_date).limit(1))
    audit(db, user, "read_weekly_report", "resident", resident_id)
    db.commit()
    return {"days": 7, "measurements": {"blood_pressure": bp, "blood_glucose": stats("blood_glucose", lambda r: r.glucose_mmol_l),
                                        "weight": stats("weight", lambda r: r.weight_kg)},
            "total_measurements": len(rows),
            "alerts": {"total": len(alerts),
                       "critical": sum(1 for a in alerts if a.level == "critical"),
                       "handled": sum(1 for a in alerts if a.acknowledged_at is not None)},
            "appointments": {"created": len(appointments),
                             "upcoming": ({"kind": upcoming.kind, "date": upcoming.preferred_date.isoformat(),
                                           "slot": upcoming.time_slot, "status": upcoming.status} if upcoming else None)}}


@router.get("/residents/{resident_id}/export.csv")
def export_csv(resident_id: int, response: Response,
               user: User = Depends(auth_user), db: Session = Depends(get_db)):
    """Data portability: the resident downloads their own history as CSV (UTF-8 BOM
    so Excel opens Chinese columns correctly). Owner-only."""
    resident = db.get(Resident, resident_id)
    if not resident or resident.owner_user_id != user.id:
        from fastapi import HTTPException
        raise HTTPException(404, "仅档案本人可导出数据")
    rows = db.scalars(select(Vital).where(
        Vital.resident_id == resident_id, Vital.deleted_at.is_(None))
        .order_by(Vital.measured_at)).all()
    actors = {u.id: u.display_name for u in db.scalars(select(User).where(
        User.id.in_({r.actor_id for r in rows} or {0}))).all()}
    alerts = db.scalars(select(RiskAlert).where(RiskAlert.resident_id == resident_id)
                        .order_by(RiskAlert.created_at)).all()
    appointments = db.scalars(select(Appointment).where(Appointment.resident_id == resident_id)
                              .order_by(Appointment.created_at)).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    context_map = {"fasting": "空腹", "before_meal": "餐前", "after_meal": "餐后", "unknown": "未记录"}
    writer.writerow(["# 测量记录（软删除的不包含在内）"])
    writer.writerow(["类型", "收缩压", "舒张压", "血糖(mmol/L)", "体重(kg)", "进餐情况", "测量时间(UTC)", "记录人"])
    for r in rows:
        writer.writerow([r.type, r.systolic or "", r.diastolic or "", r.glucose_mmol_l or "",
                         r.weight_kg or "", context_map.get(r.measurement_context or "", ""),
                         r.measured_at.isoformat(), actors.get(r.actor_id, "")])
    writer.writerow([])
    writer.writerow(["# 风险提示历史"])
    writer.writerow(["级别", "指标", "数值", "触发时间(UTC)", "处理状态", "处理方式", "处理备注"])
    for a in alerts:
        writer.writerow([a.level, a.metric, a.value_text, a.created_at.isoformat(),
                         "已处理" if a.acknowledged_at else "未处理",
                         a.outcome or "", a.outcome_note or ""])
    writer.writerow([])
    writer.writerow(["# 预约历史"])
    writer.writerow(["类型", "希望日期", "时段", "状态", "社区回复", "创建时间(UTC)"])
    for a in appointments:
        writer.writerow([a.kind, a.preferred_date.isoformat(), a.time_slot, a.status,
                         a.response_note or "", a.created_at.isoformat()])

    buffer.seek(0)
    audit(db, user, "export_health_data", "resident", resident_id)
    db.commit()
    from urllib.parse import quote
    ascii_fallback = f"anlin-health-resident-{resident_id}-data.csv"
    utf8_name = quote(f"安邻健康-{resident.name}-健康数据.csv")
    return StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f"attachment; filename={ascii_fallback}; filename*=UTF-8''{utf8_name}",
                                      "Cache-Control": "no-store"})
