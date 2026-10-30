"""Non-diagnostic threshold notices for manual readings.

Thresholds are common adult reference values, deliberately conservative about
wording: every message says the notice is NOT a diagnosis and suggests
re-measuring / seeking care. A resident's own numbers never trigger automatic
treatment advice.

Deduplication: at most one alert per (resident, metric, level) per 24 hours so
a series of high readings does not spam staff and family.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Resident, RiskAlert, Vital

BP_CRITICAL_SYS, BP_CRITICAL_DIA = 180, 110
BP_WARNING_SYS, BP_WARNING_DIA = 140, 90
GLUCOSE_CRITICAL_HIGH = 16.7
GLUCOSE_WARNING_LOW, GLUCOSE_WARNING_HIGH = 3.9, 11.0
WEIGHT_DELTA_KG = 2.0
WEIGHT_DELTA_DAYS = 7

_NOT_DIAGNOSIS = "本提示来自常见参考范围，不构成医疗诊断。"


def _value_text(vital: Vital) -> str:
    if vital.type == "blood_pressure":
        return f"{vital.systolic}/{vital.diastolic} mmHg"
    if vital.type == "blood_glucose":
        return f"{vital.glucose_mmol_l} mmol/L"
    return f"{vital.weight_kg} kg"


def evaluate_vital(db: Session, vital: Vital) -> list[dict]:
    """Return alert payloads for one reading: dicts with metric/level/value_text/message."""
    findings: list[dict] = []
    if vital.type == "blood_pressure" and vital.systolic is not None and vital.diastolic is not None:
        if vital.systolic >= BP_CRITICAL_SYS or vital.diastolic >= BP_CRITICAL_DIA:
            findings.append({"metric": "blood_pressure", "level": "critical",
                "message": f"本次血压 {_value_text(vital)} 达到需要尽快关注的水平"
                           f"（收缩压≥{BP_CRITICAL_SYS} 或舒张压≥{BP_CRITICAL_DIA}），建议尽快复测；如感到不适应及时就医或拨打120。{_NOT_DIAGNOSIS}"})
        elif vital.systolic >= BP_WARNING_SYS or vital.diastolic >= BP_WARNING_DIA:
            findings.append({"metric": "blood_pressure", "level": "warning",
                "message": f"本次血压 {_value_text(vital)} 高于常见关注范围"
                           f"（≥{BP_WARNING_SYS}/{BP_WARNING_DIA}），建议按平时习惯复测并留意变化。{_NOT_DIAGNOSIS}"})
    elif vital.type == "blood_glucose" and vital.glucose_mmol_l is not None:
        value = vital.glucose_mmol_l
        if value >= GLUCOSE_CRITICAL_HIGH:
            findings.append({"metric": "blood_glucose", "level": "critical",
                "message": f"本次血糖 {_value_text(vital)} 明显偏高，建议尽快复测并咨询医生；如伴明显不适应及时就医。{_NOT_DIAGNOSIS}"})
        elif value < GLUCOSE_WARNING_LOW or value >= GLUCOSE_WARNING_HIGH:
            state = "偏低" if value < GLUCOSE_WARNING_LOW else "高于常见参考范围"
            suffix = "；如伴心慌、出汗等不适请立即进食并联系医生" if value < GLUCOSE_WARNING_LOW else ""
            findings.append({"metric": "blood_glucose", "level": "warning",
                "message": f"本次血糖 {_value_text(vital)} {state}，建议复测并留意{('进餐情况' if vital.measurement_context in (None, 'unknown') else '进餐情况记录')}。{suffix}。{_NOT_DIAGNOSIS}"})
    elif vital.type == "weight" and vital.weight_kg is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=WEIGHT_DELTA_DAYS)
        previous = db.scalar(select(Vital).where(
            Vital.resident_id == vital.resident_id, Vital.type == "weight",
            Vital.deleted_at.is_(None), Vital.id != vital.id,
            Vital.measured_at >= cutoff, Vital.measured_at <= vital.measured_at)
            .order_by(Vital.measured_at.desc()).limit(1))
        if previous is not None and previous.weight_kg is not None:
            delta = vital.weight_kg - previous.weight_kg
            if abs(delta) >= WEIGHT_DELTA_KG:
                direction = "上升" if delta > 0 else "下降"
                findings.append({"metric": "weight", "level": "warning",
                    "message": f"本次体重 {_value_text(vital)}，较近期记录{direction}约 {abs(delta):.1f} kg"
                               f"（{WEIGHT_DELTA_DAYS} 天内）。短时间明显波动值得留意，建议规律测量观察。{_NOT_DIAGNOSIS}"})
    for finding in findings:
        finding["value_text"] = _value_text(vital)
    return findings


def raise_alerts(db: Session, resident: Resident, vital: Vital, actor: object) -> list[RiskAlert]:
    """Evaluate one reading and persist deduplicated alerts. Caller commits."""
    from .deps import audit
    created = []
    for finding in evaluate_vital(db, vital):
        recent_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        # Dedup only against still-open alerts: once staff acknowledged one,
        # a NEW out-of-range reading must raise a fresh alert.
        duplicate = db.scalar(select(RiskAlert.id).where(
            RiskAlert.resident_id == resident.id, RiskAlert.metric == finding["metric"],
            RiskAlert.level == finding["level"], RiskAlert.acknowledged_at.is_(None),
            RiskAlert.created_at > recent_cutoff))
        if duplicate:
            continue
        alert = RiskAlert(resident_id=resident.id, vital_id=vital.id,
                          **finding)
        db.add(alert)
        db.flush()
        audit(db, actor, "risk_alert_raised", "risk_alert", alert.id,
              {"metric": finding["metric"], "level": finding["level"], "value": finding["value_text"]})
        created.append(alert)
    return created
