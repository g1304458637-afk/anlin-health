"""Consent-filtered queries shared by reports and the read-only assistant."""
from sqlalchemy import func, select

from .models import ConsentEvent, Resident


def visible_resident_ids(community_id: int | None):
    latest = (select(ConsentEvent.resident_id, func.max(ConsentEvent.id).label("event_id"))
              .where(ConsentEvent.purpose == "community_care")
              .group_by(ConsentEvent.resident_id).subquery())
    return (select(Resident.id)
            .join(latest, latest.c.resident_id == Resident.id)
            .join(ConsentEvent, ConsentEvent.id == latest.c.event_id)
            .where(Resident.community_id == community_id,
                   ConsentEvent.granted.is_(True), ConsentEvent.review_status == "approved"))
