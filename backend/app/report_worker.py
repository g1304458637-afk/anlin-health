"""Database-backed bounded OCR queue; an interrupted job requires an explicit retry."""
import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select, update

from .db import SessionLocal
from .deps import audit, check_access
from .models import Resident, User
from .report_models import MedicalReport
from .report_storage import read_blob
from .report_processing import extract_document


def _claim():
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        # Do not silently resend files after a crash. The next attempt is user initiated.
        db.execute(update(MedicalReport).where(MedicalReport.status == "processing",
                   MedicalReport.lease_until < now).values(status="failed", lease_token=None,
                   lease_until=None, error="处理被中断，请核对后点击重试", updated_at=now))
        db.commit()
        report_id = db.scalar(select(MedicalReport.id).where(MedicalReport.status == "queued")
                              .order_by(MedicalReport.id).limit(1))
        if report_id is None:
            return None
        token = uuid.uuid4().hex
        updated = db.execute(update(MedicalReport).where(MedicalReport.id == report_id,
                    MedicalReport.status == "queued").values(status="processing", lease_token=token,
                    lease_until=now + timedelta(minutes=7), attempts=MedicalReport.attempts + 1,
                    error=None, updated_at=now))
        db.commit()
        return (report_id, token) if updated.rowcount == 1 else None


def _authorize(db, report_id, lease):
    report = db.get(MedicalReport, report_id)
    if not report or report.status != "processing" or report.lease_token != lease:
        raise ValueError("任务状态已变化，请刷新报告")
    if not report.lease_until or report.lease_until.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        raise ValueError("识别任务已超时，请核对后重试")
    actor = db.get(User, report.requested_by_id)
    if not actor or not actor.is_active or actor.token_version != report.requested_token_version:
        raise ValueError("申请识别的登录已失效，请重新登录后重试")
    try:
        check_access(db, actor, db.get(Resident, report.resident_id), scope="reports", write=True)
    except HTTPException:
        raise ValueError("报告授权已变化，识别已停止") from None
    return report, actor


async def process_one() -> bool:
    claimed = await asyncio.to_thread(_claim)
    if not claimed:
        return False
    report_id, lease = claimed

    async def authorize():
        with SessionLocal() as db:
            _authorize(db, report_id, lease)

    try:
        with SessionLocal() as db:
            report, _ = _authorize(db, report_id, lease)
            storage_key = report.storage_key
            expected_sha = report.sha256
        data = await asyncio.to_thread(read_blob, storage_key)
        if hashlib.sha256(data).hexdigest() != expected_sha:
            raise ValueError("原件校验失败，请联系管理员")
        async with asyncio.timeout(330):
            candidate = await extract_document(data, authorize)
        with SessionLocal() as db:
            report, actor = _authorize(db, report_id, lease)
            now = datetime.now(timezone.utc)
            changed = db.execute(update(MedicalReport).where(MedicalReport.id == report_id,
                MedicalReport.status == "processing", MedicalReport.lease_token == lease,
                MedicalReport.lease_until > now).values(
                candidate_json=json.dumps(candidate, ensure_ascii=False), status="draft",
                revision=MedicalReport.revision + 1, lease_token=None, lease_until=None,
                updated_at=now).execution_options(synchronize_session=False))
            if changed.rowcount != 1:
                raise ValueError("识别任务状态已变化，请刷新报告")
            audit(db, actor, "extract_report_draft", "medical_report", report.id)
            db.commit()
    except asyncio.CancelledError:
        # Durable lease expires after process restart; leave no false success.
        raise
    except Exception as exc:
        message = str(exc)[:250] if isinstance(exc, ValueError) else "识别暂未完成，请稍后重试（原件已保留）"
        with SessionLocal() as db:
            db.execute(update(MedicalReport).where(MedicalReport.id == report_id,
                       MedicalReport.lease_token == lease, MedicalReport.status == "processing").values(
                       status="failed", error=message, lease_token=None, lease_until=None,
                       updated_at=datetime.now(timezone.utc)))
            db.commit()
    return True


async def worker_loop(stop: asyncio.Event):
    while not stop.is_set():
        try:
            worked = await process_one()
        except asyncio.CancelledError:
            raise
        except Exception:
            worked = False  # A temporary database failure must not kill the worker.
        if not worked:
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
            except TimeoutError:
                pass


if __name__ == "__main__":
    from .config import validate_settings
    validate_settings()
    asyncio.run(worker_loop(asyncio.Event()))
