"""Isolated SQLite test database; no real names or health data."""
import os
import tempfile
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

os.environ["APP_ENV"] = "test"
os.environ["ENABLE_DEMO_AUTH"] = "true"
os.environ["SECRET_KEY"] = "unit_test_only_key_12345678901234567890123456789012345"
os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(suffix=".db")

from app.main import app
from app.db import Base, engine, SessionLocal
from app.models import User, Resident, Community, ConsentEvent, Vital, Questionnaire
from app.seed import SAMPLE_QS
from app.security import hash_password, new_invite_code

PASSWORD = "DemoPass2026!"
HASH = hash_password(PASSWORD)

@pytest.fixture
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        a = Community(name="演示社区一")
        b = Community(name="演示社区二")
        session.add_all([a,b]); session.flush()
        users = []
        for username,role,c in [('resident','resident',None),('family','family',None),('staff','staff',a.id),('staff2','staff',b.id),('admin','admin',None),('unlinked','resident',None)]:
            users.append(User(username=username, display_name=username, role=role, community_id=c,
                invite_code=new_invite_code(), password_hash=HASH))
        session.add_all(users);session.flush()
        r1=Resident(name="虚构王一",birth_year=1952,sex="female",community_id=a.id,owner_user_id=users[0].id,created_by_id=users[0].id)
        r2=Resident(name="虚构赵二",birth_year=1949,sex="male",community_id=b.id,created_by_id=users[3].id)
        session.add_all([r1,r2]);session.flush()
        session.add_all([
            ConsentEvent(resident_id=r1.id,actor_id=users[0].id,granted=True,method="resident_self",purpose="community_care"),
            ConsentEvent(resident_id=r2.id,actor_id=users[3].id,granted=True,method="paper_assisted",evidence_ref="DEMO-001",purpose="community_care")
        ])
        for t in SAMPLE_QS: session.add(Questionnaire(version=1, published=True, **t))
        session.commit()
        yield session

@pytest.fixture
def client(db):
    with TestClient(app) as testclient:
        yield testclient

@pytest.fixture
def token(client):
    def login(username):
        r=client.post('/api/auth/dev-login',json={'username':username,'password':PASSWORD})
        assert r.status_code==200, r.text
        return {'Authorization': 'Bearer '+r.json()['access_token']}
    return login
