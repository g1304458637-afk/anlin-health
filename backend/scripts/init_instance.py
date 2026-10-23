"""One-time, non-demo bootstrap after Alembic has migrated an empty database."""

import argparse
import getpass
import os
import sys
from pathlib import Path

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Community, Questionnaire, User  # noqa: E402
from app.seed import SAMPLE_QS  # noqa: E402
from app.security import hash_password, new_invite_code  # noqa: E402
from app.config import validate_settings  # noqa: E402


def initialize(session: Session, *, community_name: str, admin_username: str,
               admin_name: str, staff_username: str, staff_name: str,
               admin_password: str, staff_password: str) -> bool:
    """Insert the first two accounts and templates atomically; return False on exact rerun.

    A rerun never changes an existing password, role, community or questionnaire.
    Any partial or unrelated state is rejected for manual review.
    """
    names = [community_name, admin_username, admin_name, staff_username, staff_name]
    if any(not value.strip() for value in names):
        raise ValueError("Community, usernames and display names must be nonempty")
    if admin_username == staff_username:
        raise ValueError("Admin and staff usernames must differ")

    communities = session.scalars(select(Community)).all()
    users = session.scalars(select(User)).all()
    templates = session.scalars(select(Questionnaire)).all()
    if communities or users or templates:
        by_username = {user.username: user for user in users}
        by_slug = {template.slug: template for template in templates}
        if (len(communities) == 1 and communities[0].name == community_name
                and len(users) == 2 and set(by_username) == {admin_username, staff_username}
                and len(templates) == len(SAMPLE_QS)
                and set(by_slug) == {item["slug"] for item in SAMPLE_QS}):
            admin, staff = by_username[admin_username], by_username[staff_username]
            if (admin.role == "admin" and admin.display_name == admin_name
                    and admin.community_id is None and admin.is_active and admin.password_hash
                    and staff.role == "staff" and staff.display_name == staff_name
                    and staff.community_id == communities[0].id and staff.is_active
                    and staff.password_hash
                    and all(by_slug[item["slug"]].title == item["title"]
                            and by_slug[item["slug"]].category == item["category"]
                            and by_slug[item["slug"]].questions == item["questions"]
                            and by_slug[item["slug"]].version == 1
                            and by_slug[item["slug"]].published for item in SAMPLE_QS)):
                return False
        raise RuntimeError("Database is not pristine or does not match this bootstrap; no accounts changed")

    # Hash before adding rows so password validation fails without any writes.
    admin_hash = hash_password(admin_password)
    staff_hash = hash_password(staff_password)
    community = Community(name=community_name)
    session.add(community)
    session.flush()
    session.add_all([
        User(username=admin_username, display_name=admin_name, role="admin",
             password_hash=admin_hash, invite_code=new_invite_code()),
        User(username=staff_username, display_name=staff_name, role="staff",
             community_id=community.id, password_hash=staff_hash, invite_code=new_invite_code()),
    ])
    for item in SAMPLE_QS:
        session.add(Questionnaire(version=1, published=True, **item))
    session.flush()
    return True


def require_current_schema(connection) -> None:
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    heads = scripts.get_heads()
    current = MigrationContext.configure(connection).get_current_heads()
    if len(heads) != 1 or tuple(current) != tuple(heads):
        raise RuntimeError(f"Database must be migrated to the single Alembic head first (current={current}, heads={heads})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--community", required=True)
    parser.add_argument("--admin-username", required=True)
    parser.add_argument("--admin-name", required=True)
    parser.add_argument("--staff-username", required=True)
    parser.add_argument("--staff-name", required=True)
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("Set DATABASE_URL explicitly to the already migrated database")

    def secret(label: str, env_name: str) -> str:
        value = os.environ.get(env_name)
        if value is not None:
            return value
        value = getpass.getpass(f"Initial {label} password: ")
        if value != getpass.getpass(f"Repeat {label} password: "):
            parser.error(f"{label} passwords did not match")
        return value

    admin_password = secret("admin", "ADMIN_BOOTSTRAP_PASSWORD")
    staff_password = secret("staff", "STAFF_BOOTSTRAP_PASSWORD")
    validate_settings()
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            require_current_schema(connection)
            with Session(bind=connection) as session:
                created = initialize(session, community_name=args.community,
                                     admin_username=args.admin_username, admin_name=args.admin_name,
                                     staff_username=args.staff_username, staff_name=args.staff_name,
                                     admin_password=admin_password, staff_password=staff_password)
                session.flush()
        print("Initial accounts and questionnaires created" if created else "Bootstrap already present; no changes made")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
