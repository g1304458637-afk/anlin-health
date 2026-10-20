"""Production configuration guards and database migration round-trip."""
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config

from app.config import validate_settings


def _prod_settings(**overrides):
    base = dict(app_env="production", secret_key="x" * 48, demo_auth=False,
                database_url="postgresql+psycopg://u:p@localhost:5432/ch",
                allowed_hosts=("health.example.org",), field_encryption_key="A" * 43)
    base.update(overrides)
    return SimpleNamespace(**base)


def test_production_settings_reject_insecure_config(monkeypatch):
    from app import config as config_mod
    # A valid production configuration passes.
    monkeypatch.setattr(config_mod, "settings", _prod_settings())
    validate_settings()
    for bad, override in [
        ("short secret", {"secret_key": "x" * 10}),
        ("demo auth in production", {"demo_auth": True}),
        ("sqlite in production", {"database_url": "sqlite:///./dev.db"}),
        ("missing allowed hosts", {"allowed_hosts": ()}),
        ("missing encryption key", {"field_encryption_key": ""}),
    ]:
        monkeypatch.setattr(config_mod, "settings", _prod_settings(**override))
        try:
            validate_settings()
        except RuntimeError:
            continue
        raise AssertionError(f"production settings should reject: {bad}")


def test_encryption_key_decode_validation(monkeypatch):
    from app import crypto
    import base64, os
    bad = SimpleNamespace(field_encryption_key="not-base64!!!")
    monkeypatch.setattr(crypto, "settings", bad)
    try:
        crypto._load_key()
    except RuntimeError:
        pass
    else:
        raise AssertionError("invalid key material must be rejected")
    short = SimpleNamespace(field_encryption_key=base64.b64encode(os.urandom(8)).decode())
    monkeypatch.setattr(crypto, "settings", short)
    try:
        crypto._load_key()
    except RuntimeError:
        pass
    else:
        raise AssertionError("short keys must be rejected")


def test_alembic_upgrade_and_downgrade(tmp_path, monkeypatch):
    db_path = tmp_path / "migration_check.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(cfg, "head")
    conn = sqlite3.connect(db_path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {"users", "residents", "claim_requests", "consent_events", "family_grants",
                "vitals", "questionnaires", "questionnaire_responses", "reminders", "visits", "audit_logs"}
    missing = expected - tables
    assert not missing, f"migration is missing tables: {missing}"
    conn.close()
    command.downgrade(cfg, "base")
    conn = sqlite3.connect(db_path)
    after = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert not (expected & after), "downgrade must drop application tables"
