"""First-run setup against temporary, migrated SQLite only."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Community, Questionnaire, Resident, User
from app.security import verify_password
from scripts.init_instance import initialize, require_current_schema


ADMIN_PASSWORD = "DistinctAdminPass2026!"
STAFF_PASSWORD = "DistinctStaffPass2026!"
ARGS = dict(community_name="临时测试社区", admin_username="first_admin",
            admin_name="首位管理员", staff_username="first_staff", staff_name="首位员工",
            admin_password=ADMIN_PASSWORD, staff_password=STAFF_PASSWORD)


def _migrated_database(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{tmp_path / 'setup.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    command.upgrade(config, "head")
    return create_engine(url)


def test_migration_head_matches_model_columns(tmp_path, monkeypatch):
    engine = _migrated_database(tmp_path, monkeypatch)
    try:
        with engine.connect() as connection:
            require_current_schema(connection)
            inspector = inspect(connection)
            for table in Base.metadata.tables.values():
                actual = {column["name"] for column in inspector.get_columns(table.name)}
                expected = {column.name for column in table.columns}
                assert actual == expected, f"{table.name}: missing={expected - actual}, extra={actual - expected}"
    finally:
        engine.dispose()


def test_initializer_requires_alembic_head(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'unmigrated.db'}")
    try:
        with engine.connect() as connection:
            with pytest.raises(RuntimeError, match="migrated"):
                require_current_schema(connection)
    finally:
        engine.dispose()


def test_initializer_is_atomic_and_exact_rerun_preserves_passwords(tmp_path, monkeypatch):
    engine = _migrated_database(tmp_path, monkeypatch)
    try:
        with engine.begin() as connection:
            require_current_schema(connection)
            with Session(bind=connection) as session:
                assert initialize(session, **ARGS)
                session.flush()

        with engine.begin() as connection:
            with Session(bind=connection) as session:
                first_hashes = {user.username: user.password_hash for user in session.scalars(select(User))}
                assert not initialize(session, **{**ARGS,
                    "admin_password": "AnotherAdminPass2026!",
                    "staff_password": "AnotherStaffPass2026!"})
                assert {user.username: user.password_hash for user in session.scalars(select(User))} == first_hashes

        with Session(engine) as session:
            users = session.scalars(select(User)).all()
            assert {(user.username, user.role) for user in users} == {
                ("first_admin", "admin"), ("first_staff", "staff")}
            assert verify_password(ADMIN_PASSWORD, next(u.password_hash for u in users if u.role == "admin"))
            assert verify_password(STAFF_PASSWORD, next(u.password_hash for u in users if u.role == "staff"))
            assert len(session.scalars(select(Community)).all()) == 1
            assert {q.slug for q in session.scalars(select(Questionnaire))} == {
                "daily-health", "mood-checkin"}
            assert session.scalar(select(Resident.id).limit(1)) is None

        with pytest.raises(RuntimeError, match="not pristine"):
            with engine.begin() as connection:
                with Session(bind=connection) as session:
                    initialize(session, **{**ARGS, "admin_username": "takeover"})
        with Session(engine) as session:
            assert {user.username for user in session.scalars(select(User))} == {
                "first_admin", "first_staff"}
    finally:
        engine.dispose()
