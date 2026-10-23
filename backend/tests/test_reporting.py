"""Totals and access control remain correct beyond the old 100-row cutoff."""
from datetime import datetime, timezone

from sqlalchemy import select

from app.models import ConsentEvent, Resident, User, Vital


def test_totals_latest_and_pagination_cover_all_authorized_data(client, token, db):
    staff = db.scalar(select(User).where(User.username == "staff"))
    for i in range(120):
        r = Resident(name=f"本社区虚构居民{i:03d}", birth_year=1950, community_id=1, created_by_id=staff.id)
        db.add(r); db.flush()
        db.add(ConsentEvent(resident_id=r.id, actor_id=staff.id, granted=True, purpose="community_care", method="paper_assisted", review_status="approved"))
    for i in range(5):
        r = Resident(name=f"不得显示的待复核居民{i}", birth_year=1950, community_id=1, created_by_id=staff.id)
        db.add(r); db.flush()
        db.add(ConsentEvent(resident_id=r.id, actor_id=staff.id, granted=True, purpose="community_care", method="paper_assisted", review_status="pending"))
    now = datetime.now(timezone.utc)
    for i in range(125):
        db.add(Vital(resident_id=1, actor_id=staff.id, type="blood_pressure", systolic=120 + i % 10, diastolic=80, measured_at=now))
    db.add(Vital(resident_id=2, actor_id=staff.id, type="weight", weight_kg=75, measured_at=now))
    db.commit()
    h = token("staff")
    data = client.get("/api/community/health-stats?limit=25", headers=h).json()
    assert data["resident_count"] == 121 and data["total_residents"] == 121
    assert data["totals"] == {"blood_pressure": 125, "blood_glucose": 0, "weight": 0}
    assert sum(x["count"] for x in data["daily_counts"]) == 125
    assert len(data["residents"]) == 25
    match = client.get("/api/community/health-stats?q=虚构王一", headers=h).json()
    assert match["total_residents"] == 1 and match["totals"]["blood_pressure"] == 125
    assert match["residents"][0]["record_count"] == 125
    assert match["residents"][0]["blood_pressure"]["systolic"] == 124
    first = client.get("/api/community/residents?limit=100", headers=h).json()
    next_page = client.get("/api/community/residents?limit=100&offset=100", headers=h).json()
    assert len(first) == 100 and len(next_page) == 21
    assert not ({r["id"] for r in first} & {r["id"] for r in next_page})
    assert all("不得显示" not in r["name"] for r in first + next_page)
    assert client.get("/api/community/health-stats", headers=token("family")).status_code == 403
    assert client.get("/api/community/health-stats", headers=token("admin")).status_code == 403
    other = client.get("/api/community/health-stats", headers=token("staff2")).json()
    assert other["resident_count"] == 1 and other["totals"]["weight"] == 1
    client.post("/api/residents/1/consents", headers=token("resident"), json={"granted": False})
    after = client.get("/api/community/health-stats", headers=h).json()
    assert after["totals"]["blood_pressure"] == 0


def test_vitals_history_supports_offset_and_still_checks_scope(client, token, db):
    owner = db.scalar(select(User).where(User.username == "resident"))
    for i in range(25):
        db.add(Vital(resident_id=1, actor_id=owner.id, type="weight", weight_kg=50 + i))
    db.commit()
    data = client.get("/api/residents/1/vitals?metric=weight&limit=5&offset=20", headers=token("resident")).json()
    assert len(data) == 5 and [v["weight_kg"] for v in data] == [54, 53, 52, 51, 50]
    assert client.get("/api/residents/1/vitals?offset=20", headers=token("staff2")).status_code == 404
