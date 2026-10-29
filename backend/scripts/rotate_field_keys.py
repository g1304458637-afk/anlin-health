"""Re-encrypt sensitive columns to the current FIELD_ENCRYPTION_KEY version.

Rotation procedure:
1. Provision the NEW key as FIELD_ENCRYPTION_KEY and move the outgoing key into
   FIELD_ENCRYPTION_PREVIOUS_KEYS (comma-separated, oldest first). The app stays
   readable throughout: legacy v1 rows fall back through the previous keys and
   new writes already use the current version.
2. Run `python scripts/rotate_field_keys.py --check` for a dry run, then without
   --check to re-encrypt every stored value to the current version.
3. After the script reports 0 stale values, remove the old key from the
   environment (and from any secret manager copies) at the next restart.

Every step is safe to re-run; rows already on the current version are skipped.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, String, text, type_coerce  # noqa: E402

from app.crypto import decrypt_value, encrypt_value, is_current_format  # noqa: E402
from app.db import engine  # noqa: E402
from app import models, report_models  # noqa: E402

# (model, column name) pairs for every EncryptedString/EncryptedText field.
TARGETS = [
    (models.Resident, "phone"),
    (models.User, "totp_secret"),
    (models.User, "totp_pending_secret"),
    (models.ClaimRequest, "stated_phone"),
    (report_models.MedicalReport, "candidate_json"),
    (report_models.MedicalReport, "reviewed_json"),
    (report_models.ReportVitalLink, "imported_snapshot_json"),
    (report_models.ReportRevision, "actor_name"),
    (report_models.ReportRevision, "reason"),
    (report_models.ReportRevision, "review_json"),
]


def rotate_all(check_only: bool = False) -> dict:
    import time
    started = time.time()
    report: dict[str, int] = {}
    with engine.begin() as conn:
        for model, column in TARGETS:
            column_attr = getattr(model, column)
            # type_coerce strips the encrypted TypeDecorator on both sides: the
            # LIKE matches raw text and the selected values stay sealed, so the
            # script (not the ORM) controls decryption.
            rows = conn.execute(select(model.id, type_coerce(column_attr, String))
                                .where(type_coerce(column_attr, String).like("enc:%"))).all()
            stale_rows: list[tuple[int, str]] = []
            for row_id, value in rows:
                if value is None or is_current_format(value):
                    continue
                stale_rows.append((row_id, encrypt_value(decrypt_value(value))))
            if not check_only:
                for row_id, resealed in stale_rows:
                    # Raw SQL: an ORM update would run the sealed value back
                    # through the TypeDecorator and encrypt it a second time.
                    conn.execute(text(f"UPDATE {model.__tablename__} SET {column} = :value WHERE id = :row_id"),
                                 {"value": resealed, "row_id": row_id})
            report[f"{model.__tablename__}.{column}"] = len(stale_rows)
    report["_elapsed_seconds"] = round(time.time() - started, 2)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="count stale values without writing")
    args = parser.parse_args()
    try:
        report = rotate_all(check_only=args.check)
    except RuntimeError as exc:
        print(f"ABORT: {exc}")
        return 1
    mode = "stale values" if args.check else "re-encrypted values"
    for key, count in report.items():
        print(f"{key}: {count} {mode}" if not key.startswith("_") else f"{key[1:]}: {count}")
    total = sum(v for k, v in report.items() if not k.startswith("_"))
    print(f"TOTAL: {total} ({'dry run' if args.check else 'written'}); "
          f"re-run with --check until TOTAL is 0, then retire the previous key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
