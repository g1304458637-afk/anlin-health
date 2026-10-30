"""v0.8 second batch: alert loop, thresholds, trend, family proxy, capacity, todo, summary, export."""
import json
from datetime import date, datetime, timedelta, timezone

from app.models import FamilyGrant, Reminder, RiskAlert, Visit


def _bp(client, token, sys_, dia, cri):
    from datetime import datetime, timedelta, timezone as tz
    now = datetime.now(tz(timedelta(hours=8)))
    return client.post("/api/residents/1/vitals", headers=token("resident"),
                       json={"type": "blood_pressure", "systolic": sys_, "diastolic": dia,
                             "measured_at": now.isoformat(timespec="seconds"), "client_request_id": cri})


def _grant_write(client, token, db):
    from app.models import User
    fam = db.query(User).filter(User.username == "family").one()
    db.expire_all()
    return client.post("/api/residents/1/family-grants", headers=token("resident"),
                       json={"invite_code": fam.invite_code, "can_vitals": True,
                             "can_write_vitals": True, "expires_in_days": 30})


def test_alert_closed_loop_and_family_visibility(client, token, db):
    created = _bp(client, token, 185, 95, "loop-crit-0001")
    assert created.status_code == 201, created.text
    alert_id = db.query(RiskAlert).filter(RiskAlert.level == "critical").one().id
    acked = client.post(f"/api/community/risk-alerts/{alert_id}/ack", headers=token("staff"),
                        json={"outcome": "visited", "outcome_note": "已上门，老人状态稳定"})
    assert acked.status_code == 200
    # Family (with vitals grant) sees the handling outcome.
    assert _grant_write(client, token, db).status_code == 201
    seen = client.get("/api/residents/1/risk-alerts", headers=token("family")).json()["items"]
    target = next(a for a in seen if a["id"] == alert_id)
    assert target["outcome_label"] == "上门探访" and "上门" in target["outcome_note"]
    assert target["acknowledged_at"]


def test_alert_to_visit_creates_single_task(client, token, db):
    _bp(client, token, 183, 96, "to-visit-0001")
    alert_id = db.query(RiskAlert).filter(RiskAlert.acknowledged_at.is_(None)).one().id
    made = client.post(f"/api/community/risk-alerts/{alert_id}/to-visit", headers=token("staff"))
    assert made.status_code == 201, made.text
    visit = db.query(Visit).filter(Visit.id == made.json()["visit_id"]).one()
    assert visit.status == "planned" and f"[风险预警 #{alert_id}]" in visit.note
    dup = client.post(f"/api/community/risk-alerts/{alert_id}/to-visit", headers=token("staff"))
    assert dup.status_code == 409


def test_trend_alert_after_three_rising_elevated_readings(client, token, db):
    _bp(client, token, 142, 88, "trend-0001")
    _bp(client, token, 148, 90, "trend-0002")
    third = _bp(client, token, 155, 92, "trend-0003")
    assert third.status_code == 201
    trends = db.query(RiskAlert).filter(RiskAlert.metric == "blood_pressure_trend").all()
    assert len(trends) == 1 and trends[0].level == "warning"
    # A fourth reading does not duplicate the still-open trend alert.
    _bp(client, token, 160, 94, "trend-0004")
    assert db.query(RiskAlert).filter(RiskAlert.metric == "blood_pressure_trend").count() == 1


def test_per_resident_threshold_override(client, token, db):
    # 150/92 is a default warning; tighten the resident's thresholds to critical level.
    set_thr = client.patch("/api/community/residents/1/alert-thresholds", headers=token("staff"),
                           json={"blood_pressure": {"warning_sys": 145, "critical_sys": 150}})
    assert set_thr.status_code == 200, set_thr.text
    made = _bp(client, token, 150, 80, "override-0001")
    assert made.status_code == 201
    levels = {a.level for a in db.query(RiskAlert).filter(RiskAlert.metric == "blood_pressure").all()}
    assert "critical" in levels  # 150 hit the tightened critical_sys
    # Invalid keys rejected; other community staff cannot set thresholds.
    assert client.patch("/api/community/residents/1/alert-thresholds", headers=token("staff"),
                        json={"blood_pressure": {"hacker": 1}}).status_code == 422
    assert client.patch("/api/community/residents/1/alert-thresholds", headers=token("staff2"),
                        json={"blood_pressure": {"warning_sys": 120}}).status_code == 404


def test_family_proxy_entry_marks_recorder(client, token, db):
    assert _grant_write(client, token, db).status_code == 201
    from datetime import datetime, timedelta, timezone as tz
    now = datetime.now(tz(timedelta(hours=8)))
    made = client.post("/api/residents/1/vitals", headers=token("family"),
                       json={"type": "blood_pressure", "systolic": 126, "diastolic": 80,
                             "measured_at": now.isoformat(timespec="seconds"),
                             "client_request_id": "proxy-0000001"})
    assert made.status_code == 201, made.text
    from app.models import User
    family_display = db.query(User).filter(User.username == "family").one().display_name
    listing = client.get("/api/residents/1/vitals", headers=token("resident")).json()
    mine = next(v for v in listing if v["recorded_by"] == family_display)
    assert mine["systolic"] == 126
    # Family without the write flag stays read-only.
    db.query(FamilyGrant).update({"can_write_vitals": False})
    db.commit()
    second = client.post("/api/residents/1/vitals", headers=token("family"),
                         json={"type": "weight", "weight_kg": 60.0,
                               "measured_at": now.isoformat(timespec="seconds"),
                               "client_request_id": "proxy-0000002"})
    assert second.status_code == 404  # masked, no disclosure


def test_appointment_slot_capacity_and_confirm_reminder(client, token, db):
    from app.models import Appointment
    day = str(date.today() + timedelta(days=10))
    ids = []
    for i in range(3):
        r = client.post("/api/residents/1/appointments", headers=token("resident"),
                        json={"kind": "staff", "preferred_date": day, "time_slot": "morning",
                              "note": f"第{i+1}次"})
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    fourth = client.post("/api/residents/1/appointments", headers=token("resident"),
                         json={"kind": "staff", "preferred_date": day, "time_slot": "morning"})
    assert fourth.status_code == 409 and "已满" in fourth.json()["detail"]
    # Confirm the first: a resident-side appointment reminder is created automatically.
    assert client.post(f"/api/community/appointments/{ids[0]}/confirm", headers=token("staff"),
                       json={"response_note": "上午9点到"}).status_code == 200
    reminder = db.query(Reminder).filter(Reminder.type == "appointment").one()
    assert "预约#1" in reminder.title and reminder.due_date.isoformat() == day
    # Re-confirm does not duplicate the reminder.
    other = client.post("/api/residents/1/appointments", headers=token("resident"),
                        json={"kind": "doctor", "preferred_date": day, "time_slot": "afternoon"})
    assert other.status_code == 201
    other_id = other.json()["id"]
    assert client.post(f"/api/community/appointments/{other_id}/confirm", headers=token("staff"),
                       json={}).status_code == 200
    assert db.query(Reminder).filter(Reminder.type == "appointment").count() == 2


def test_today_todo_aggregates_prioritized(client, token, db):
    _bp(client, token, 187, 98, "todo-crit-0001")
    from datetime import date as _date, timedelta as _td
    client.post("/api/residents/1/appointments", headers=token("resident"),
                json={"kind": "staff", "preferred_date": str(_date.today() + _td(days=2)), "time_slot": "afternoon"})
    day_past = date.today() - timedelta(days=1)
    db.add(Visit(resident_id=1, staff_id=3, due_date=day_past, status="planned"))
    db.commit()
    todo = client.get("/api/community/today-todo", headers=token("staff"))
    assert todo.status_code == 200, todo.text
    body = todo.json()
    kinds = [i["kind"] for i in body["items"]]
    assert "critical_alert" in kinds and "overdue_visit" in kinds and "pending_appointment" in kinds
    priorities = [i["priority"] for i in body["items"]]
    assert priorities == sorted(priorities)  # urgent first
    assert body["counts"]["critical_alert"] >= 1
    assert client.get("/api/community/today-todo", headers=token("admin")).status_code == 403


def test_weekly_report_and_csv_export(client, token, db):
    _bp(client, token, 130, 84, "report-bp-0001")
    report = client.get("/api/residents/1/weekly-report", headers=token("resident"))
    assert report.status_code == 200
    body = report.json()
    assert body["total_measurements"] >= 1 and body["measurements"]["blood_pressure"]["count"] >= 1
    assert "alerts" in body and "appointments" in body
    # Family with vitals grant can read it; staff2 cannot.
    assert client.get("/api/residents/1/weekly-report", headers=token("staff2")).status_code == 404
    export = client.get("/api/residents/1/export.csv", headers=token("resident"))
    assert export.status_code == 200
    text = export.content.decode("utf-8-sig")
    assert "测量记录" in text and "风险提示历史" in text and "预约历史" in text
    # Not exportable by family (owner-only) or other-community staff.
    assert client.get("/api/residents/1/export.csv", headers=token("family")).status_code == 404
    assert client.get("/api/residents/1/export.csv", headers=token("staff2")).status_code == 404
