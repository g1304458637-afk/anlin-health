"""Risk notice evaluation/surfacing and the appointment booking flow."""
from datetime import date, timedelta

from app.models import AuditLog, RiskAlert, Vital


def _vital(client, token, **overrides):
    body = {"measured_at": "2026-10-29T08:30:00+08:00"}
    body.update(overrides)
    return client.post("/api/residents/1/vitals", headers=token("resident"), json=body)


def test_critical_and_warning_thresholds_raise_alerts(client, token, db):
    created = _vital(client, token, type="blood_pressure", systolic=185, diastolic=95,
                     client_request_id="risk-crit-0001")
    assert created.status_code == 201, created.text
    alerts = db.query(RiskAlert).filter(RiskAlert.metric == "blood_pressure").all()
    assert len(alerts) == 1 and alerts[0].level == "critical"
    assert "不构成医疗诊断" in alerts[0].message
    # Same day, another critical reading -> deduplicated, no new row.
    again = _vital(client, token, type="blood_pressure", systolic=190, diastolic=100, client_request_id="risk-crit-0002")
    assert again.status_code == 201
    assert db.query(RiskAlert).filter(RiskAlert.metric == "blood_pressure").count() == 1
    # A warning-level reading inside 24h still creates its own (different level) alert.
    warn = _vital(client, token, type="blood_pressure", systolic=150, diastolic=92, client_request_id="risk-warn-0001")
    assert warn.status_code == 201
    levels = {a.level for a in db.query(RiskAlert).filter(RiskAlert.metric == "blood_pressure").all()}
    assert levels == {"critical", "warning"}
    # After staff acknowledge the critical alert, a new critical reading re-alerts.
    from datetime import datetime, timezone
    first = db.query(RiskAlert).filter(RiskAlert.level == "critical").one()
    first.acknowledged_at = datetime.now(timezone.utc)
    db.commit()
    third = _vital(client, token, type="blood_pressure", systolic=188, diastolic=105, client_request_id="risk-crit-0003")
    assert third.status_code == 201
    criticals = db.query(RiskAlert).filter(RiskAlert.level == "critical").count()
    assert criticals == 2


def test_glucose_low_and_weight_delta_rules(client, token, db):
    db.query(Vital).filter(Vital.resident_id == 1).delete()
    db.commit()
    low = _vital(client, token, type="blood_glucose", glucose_mmol_l=3.2,
                 client_request_id="risk-glu-low01")
    assert low.status_code == 201
    base = _vital(client, token, type="weight", weight_kg=62.0,
                  client_request_id="risk-wt-base1")
    assert base.status_code == 201
    drop = _vital(client, token, type="weight", weight_kg=59.5,
                  client_request_id="risk-wt-drop1")
    assert drop.status_code == 201
    metrics = {a.metric: a.level for a in db.query(RiskAlert).all()}
    assert metrics.get("blood_glucose") == "warning"
    assert metrics.get("weight") == "warning"


def test_staff_queue_is_community_scoped_and_ackable(client, token, db):
    _vital(client, token, type="blood_pressure", systolic=185, diastolic=95, client_request_id="risk-queue-001")
    listing = client.get("/api/community/risk-alerts", headers=token("staff"))
    assert listing.status_code == 200
    assert listing.json()["open_count"] == 1
    alert_id = listing.json()["items"][0]["id"]
    # Another community's staff cannot see or acknowledge it.
    assert client.get("/api/community/risk-alerts", headers=token("staff2")).json()["open_count"] == 0
    assert client.post(f"/api/community/risk-alerts/{alert_id}/ack", headers=token("staff2"),
                       json={"outcome": "called"}).status_code == 404
    assert client.post(f"/api/community/risk-alerts/{alert_id}/ack", headers=token("resident"),
                       json={"outcome": "called"}).status_code == 403
    acked = client.post(f"/api/community/risk-alerts/{alert_id}/ack", headers=token("staff"),
                        json={"outcome": "visited", "outcome_note": "已上门复测"})
    assert acked.status_code == 200 and acked.json()["outcome_label"] == "上门探访"
    # outcome without a valid value is rejected
    assert client.post(f"/api/community/risk-alerts/{alert_id}/ack", headers=token("staff"),
                       json={"outcome": "magic"}).status_code == 422
    assert client.get("/api/community/risk-alerts", headers=token("staff")).json()["open_count"] == 0
    entry = db.query(AuditLog).filter(AuditLog.action == "risk_alert_acknowledged").count() == 1


def test_resident_and_family_alert_surfaces(client, token, db):
    _vital(client, token, type="blood_pressure", systolic=185, diastolic=95, client_request_id="risk-surf-001")
    own = client.get("/api/residents/1/risk-alerts", headers=token("resident"))
    assert own.status_code == 200 and len(own.json()["items"]) == 1
    # Family has no grant yet -> hidden; after a vitals grant -> visible.
    assert client.get("/api/residents/1/risk-alerts", headers=token("family")).status_code == 404
    grant = client.post("/api/residents/1/family-grants", json={"invite_code": "FAMILYCODE",
                        "can_vitals": True}, headers=token("resident"))
    family_user = token  # noqa: F841 - readability
    from app.models import User
    # the seeded family invite code is on the family user; fetch via admin-less path:
    # resident can read it from the grant flow only in real usage, so use db directly.
    fam = db.query(User).filter(User.username == "family").one()
    db.expire_all()
    grant2 = client.post("/api/residents/1/family-grants", json={"invite_code": fam.invite_code,
                         "can_vitals": True}, headers=token("resident"))
    assert grant2.status_code == 201, grant2.text
    seen = client.get("/api/residents/1/risk-alerts", headers=token("family"))
    assert seen.status_code == 200 and len(seen.json()["items"]) == 1


def _book(client, token, **overrides):
    body = {"kind": "doctor", "preferred_date": str(date.today() + timedelta(days=3)),
            "time_slot": "morning", "note": "复诊陪同"}
    body.update(overrides)
    return client.post("/api/residents/1/appointments", headers=token("resident"), json=body)


def test_appointment_flow_resident_to_staff(client, token, db):
    created = _book(client, token)
    assert created.status_code == 201, created.text
    booking_id = created.json()["id"]
    assert created.json()["status"] == "pending" and created.json()["kind_label"] == "医生门诊陪同"
    # Validation: past date rejected, bad kind rejected.
    assert _book(client, token, preferred_date=str(date.today() - timedelta(days=1))).status_code == 422
    assert _book(client, token, kind="taxi").status_code == 422
    # Staff of the same community confirms; other community cannot even see it.
    queue = client.get("/api/community/appointments", headers=token("staff"))
    assert queue.status_code == 200 and queue.json()["total"] == 1
    assert client.get("/api/community/appointments", headers=token("staff2")).json()["total"] == 0
    assert client.post(f"/api/community/appointments/{booking_id}/confirm",
                       headers=token("staff2"), json={}).status_code == 404
    confirmed = client.post(f"/api/community/appointments/{booking_id}/confirm", headers=token("staff"),
                            json={"response_note": "安排李老师陪同，上午9点在门口集合"})
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "confirmed"
    assert "李老师" in confirmed.json()["response_note"]
    # Resident sees the response and cannot complete; staff completes.
    mine = client.get("/api/residents/1/appointments", headers=token("resident")).json()
    assert mine[0]["status"] == "confirmed" and mine[0]["handled_by"]
    assert client.post(f"/api/residents/1/appointments/{booking_id}/cancel",
                       headers=token("resident")).status_code == 200  # confirmed cancellable by owner
    reopened = _book(client, token, preferred_date=str(date.today() + timedelta(days=5)))
    assert reopened.status_code == 201
    second = reopened.json()["id"]
    assert client.post(f"/api/community/appointments/{second}/confirm", headers=token("staff"), json={}).status_code == 200
    done = client.post(f"/api/community/appointments/{second}/complete", headers=token("staff"), json={})
    assert done.status_code == 200 and done.json()["status"] == "completed"
    # Completed cannot be cancelled again.
    assert client.post(f"/api/residents/1/appointments/{second}/cancel",
                       headers=token("resident")).status_code == 409


def test_staff_cannot_book_and_family_cannot_see_appointments(client, token):
    assert client.post("/api/residents/1/appointments", headers=token("staff"),
                       json={"kind": "staff", "preferred_date": str(date.today() + timedelta(days=1)),
                             "time_slot": "morning"}).status_code in {403, 404}
    assert client.get("/api/residents/1/appointments", headers=token("family")).status_code == 404
