from datetime import date, timedelta, datetime, timezone
from sqlalchemy import select
from app.models import FamilyGrant, Vital, AuditLog, ConsentEvent, User


def test_health_and_docs(client):
    assert client.get('/healthz').json()['status']=='ok'
    assert client.get('/openapi.json').status_code==200
    assert client.get('/console').status_code==200


def test_authentication(client, token):
    assert client.get('/api/me').status_code==401
    assert client.get('/api/me',headers={'Authorization':'Bearer bad-token'}).status_code==401
    res=client.post('/api/auth/dev-login',json={'username':'staff','password':'WRONG'})
    assert res.status_code==401
    assert client.get('/api/me',headers=token('staff')).json()['role']=='staff'


def test_password_never_in_user_json(client,token):
    d=client.get('/api/me',headers=token('resident')).json()
    assert 'password_hash' not in d and 'wechat_openid' not in d


def test_owner_sees_self(client,token):
    assert client.get('/api/residents/me',headers=token('resident')).json()['name']=='虚构王一'


def test_unlinked_user_can_create_once(client,token):
    h=token('unlinked')
    data={'name':'虚构李三','birth_year':1958,'sex':'female','community_id':1,'consent_to_community_care':True}
    assert client.post('/api/residents/me',json=data,headers=h).status_code==201
    assert client.post('/api/residents/me',json=data,headers=h).status_code==409


def test_consent_required_for_self_onboarding(client,token):
    r=client.post('/api/residents/me',json={'name':'虚构李三','birth_year':1961,'community_id':1,'consent_to_community_care':False},headers=token('unlinked'))
    assert r.status_code==422


def test_assisted_creation_needs_documented_consent(client,token):
    h=token('staff')
    payload={'name':'虚构李三','birth_year':1958,'community_id':1,'consent_confirmed':True,'evidence_ref':'PAPER-00001'}
    assert client.post('/api/community/residents',json={**payload,'consent_confirmed':False},headers=h).status_code==422
    assert client.post('/api/community/residents',json={**payload,'community_id':2},headers=h).status_code==403
    good=client.post('/api/community/residents',json=payload,headers=h)
    assert good.status_code==201
    assert good.json()['consent_active'] is True


def test_cross_community_denial(client,token):
    h=token('staff')
    assert client.get('/api/residents/2',headers=h).status_code==404
    assert client.post('/api/residents/2/vitals',json={'type':'weight','weight_kg':58},headers=h).status_code==404
    assert client.get('/api/community/residents',headers=h).json()[0]['id']==1


def test_admin_has_no_personal_read_access(client,token):
    h=token('admin')
    assert client.get('/api/residents/1',headers=h).status_code==404
    assert client.get('/api/residents/1/vitals',headers=h).status_code==404
    assert client.get('/api/admin/summary',headers=h).json()['community_count']==2


def test_family_no_implicit_access(client,token):
    h=token('family')
    assert client.get('/api/residents/1',headers=h).status_code==404
    assert client.get('/api/residents/1/vitals',headers=h).status_code==404
    assert client.get('/api/family/shared-residents',headers=h).json()==[]


def test_family_grants_vitals_only(client,token,db):
    fam=db.scalar(select(User).where(User.username=='family'))
    r=client.post('/api/residents/1/family-grants',headers=token('resident'),json={'invite_code':fam.invite_code,'can_profile':False,'can_vitals':True,'expires_in_days':7})
    assert r.status_code==201
    assert client.get('/api/residents/1',headers=token('family')).status_code==404
    assert client.get('/api/residents/1/vitals',headers=token('family')).status_code==200
    shared=client.get('/api/family/shared-residents',headers=token('family')).json()
    assert shared[0]['name']=='授权居民' and shared[0]['can_profile'] is False


def test_family_cannot_write(client,token,db):
    fam=db.scalar(select(User).where(User.username=='family'))
    assert client.post('/api/residents/1/family-grants',headers=token('resident'),json={'invite_code':fam.invite_code,'can_profile':True,'can_vitals':True}).status_code==201
    assert client.post('/api/residents/1/vitals',json={'type':'weight','weight_kg':68},headers=token('family')).status_code==404
    assert client.get('/api/residents/1/responses',headers=token('family')).status_code==404


def test_revoke_family_grant(client,token,db):
    fam=db.scalar(select(User).where(User.username=='family'))
    r=client.post('/api/residents/1/family-grants',headers=token('resident'),json={'invite_code':fam.invite_code,'can_vitals':True})
    gid=r.json()['id']
    assert client.delete(f'/api/residents/1/family-grants/{gid}',headers=token('resident')).status_code==204
    assert client.get('/api/residents/1/vitals',headers=token('family')).status_code==404


def test_family_expired_grant_not_valid(client,token,db):
    f=db.scalar(select(User).where(User.username=='family'))
    g=FamilyGrant(resident_id=1,grantee_id=f.id,can_profile=True,can_vitals=True,expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))
    db.add(g);db.commit()
    assert client.get('/api/residents/1',headers=token('family')).status_code==404


def test_vitals_valid_record_and_read(client,token):
    h=token('resident')
    payload={'type':'blood_pressure','systolic':124,'diastolic':81}
    r=client.post('/api/residents/1/vitals',headers=h,json=payload)
    assert r.status_code==201, r.text
    assert client.get('/api/residents/1/vitals',headers=h).json()[0]['systolic']==124


def test_vitals_invalid_or_mixed_values(client,token):
    h=token('resident')
    for payload in [{'type':'blood_pressure','systolic':45,'diastolic':100},
                    {'type':'weight','weight_kg':-10},
                    {'type':'blood_glucose','glucose_mmol_l':5,'weight_kg':70}]:
        assert client.post('/api/residents/1/vitals',headers=h,json=payload).status_code==422


def test_vitals_future_timestamp_denied(client,token):
    when=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    r=client.post('/api/residents/1/vitals',headers=token('resident'),json={'type':'weight','weight_kg':60,'measured_at':when})
    assert r.status_code==422


def test_owner_delete_vitals(client,token):
    rec=client.post('/api/residents/1/vitals',headers=token('resident'),json={'type':'weight','weight_kg':60}).json()
    assert client.delete(f'/api/residents/1/vitals/{rec["id"]}',headers=token('staff')).status_code==403
    assert client.delete(f'/api/residents/1/vitals/{rec["id"]}',headers=token('resident')).status_code==204
    assert client.get('/api/residents/1/vitals',headers=token('resident')).json()==[]


def test_questionnaire_no_diagnosis_endpoint(client,token):
    qs=client.get('/api/questionnaires',headers=token('resident')).json()
    assert len(qs)==2 and any(x['category']=='wellbeing' for x in qs)
    assert all('risk_score' not in q for q in qs)


def test_submit_questionnaire_and_private_results(client,token):
    payload={'questionnaire_id':2,'answers':{'mood':'mixed','support':'yes'}}
    r=client.post('/api/residents/1/responses',json=payload,headers=token('resident'))
    assert r.status_code==201, r.text
    assert len(client.get('/api/residents/1/responses',headers=token('resident')).json())==1
    assert client.get('/api/residents/1/responses',headers=token('staff')).status_code==404


def test_assisted_psych_questionnaire_forbidden(client,token):
    payload={'questionnaire_id':2,'answers':{'mood':'mixed','support':'yes'}}
    assert client.post('/api/residents/1/responses',json=payload,headers=token('staff')).status_code==403


def test_assisted_general_questionnaire_allowed(client,token):
    payload={'questionnaire_id':1,'answers':{'activity':'10to30','sleep':'average','help':'yes'}}
    assert client.post('/api/residents/1/responses',json=payload,headers=token('staff')).status_code==201


def test_unexpected_questionnaire_option_rejected(client,token):
    p={'questionnaire_id':2,'answers':{'mood':'malicious','support':'no'}}
    assert client.post('/api/residents/1/responses',json=p,headers=token('resident')).status_code==422


def test_consent_revocation_blocks_staff_not_owner(client,token):
    h=token('resident')
    out=client.post('/api/residents/1/consents',json={'granted':False},headers=h)
    assert out.status_code==201
    assert client.get('/api/residents/1',headers=token('staff')).status_code==404
    assert client.get('/api/residents/1',headers=h).status_code==200
    assert client.get('/api/community/residents',headers=token('staff')).json()==[]


def test_staff_cannot_change_consent_without_evidence(client,token):
    assert client.post('/api/residents/1/consents',json={'granted':False},headers=token('staff')).status_code==422
    assert client.post('/api/residents/1/consents',json={'granted':False,'evidence_ref':'SIGNED-PAPER-001'},headers=token('staff')).status_code==201


def test_reminders_create_complete(client,token):
    h=token('resident')
    r=client.post('/api/residents/1/reminders',headers=h,json={'type':'measure','title':'量血压','due_date':date.today().isoformat()})
    assert r.status_code==201, r.text
    rid=r.json()['id']
    done=client.patch(f'/api/residents/1/reminders/{rid}/complete',headers=h)
    assert done.status_code==200 and done.json()['completed_at'] is not None
    assert client.get('/api/residents/1/reminders',headers=token('family')).status_code==404


def test_visits_staff_scope(client,token):
    payload={'resident_id':1,'due_date':date.today().isoformat(),'note':'普通工作安排'}
    r=client.post('/api/community/visits',headers=token('staff'),json=payload)
    assert r.status_code==201, r.text
    vid=r.json()['id']
    assert client.patch(f'/api/community/visits/{vid}/finish',headers=token('staff2'),json={}).status_code==404
    assert client.patch(f'/api/community/visits/{vid}/finish',headers=token('staff'),json={'note':'走访完成'}).json()['status']=='done'
    # Resident interface excludes private staff notes.
    assert 'note' not in client.get('/api/residents/1/visits',headers=token('resident')).json()[0]


def test_role_escalation_restricted(client,token):
    assert client.post('/api/me/family-mode',headers=token('resident'),json={}).status_code==409
    assert client.post('/api/me/family-mode',headers=token('staff'),json={}).status_code==403
    assert client.post('/api/me/family-mode',headers=token('unlinked'),json={}).json()['role']=='family'


def test_audit_log_no_raw_health_payload(client,token,db):
    client.post('/api/residents/1/vitals',headers=token('resident'),json={'type':'weight','weight_kg':70})
    rows=db.scalars(select(AuditLog)).all()
    assert any(x.action=='create_vital' for x in rows)
    assert all(not hasattr(x,'answers') and not hasattr(x,'weight_kg') for x in rows)


def test_wechat_requires_configured_appid(client):
    r=client.post('/api/auth/wechat',json={'code':'example-wechat-code-should-fail'})
    assert r.status_code==503


def test_wechat_login_creates_only_low_privilege_user(client,monkeypatch):
    from app.routes import auth
    async def fake_exchange(_code):
        return 'FAKE-OPENID-FOR-UNIT-TEST-ONLY'
    monkeypatch.setattr(auth,'exchange_wechat_code',fake_exchange)
    body={'code':'FAKE-WECHAT-LOGIN-CODE'}
    response=client.post('/api/auth/wechat',json=body)
    assert response.status_code==200,response.text
    data=response.json()
    assert data['user']['role']=='resident'
    assert 'wechat_openid' not in data['user']
    again=client.post('/api/auth/wechat',json=body)
    assert again.json()['user']['id']==data['user']['id']
    assert client.get('/api/admin/summary',headers={'Authorization':'Bearer '+data['access_token']}).status_code==403


def test_family_invalid_invite_not_accepted(client,token):
    r=client.post('/api/residents/1/family-grants',json={'invite_code':'totallywrongcode'},headers=token('resident'))
    assert r.status_code==422


def test_staff_created_unclaimed_record_cannot_be_granted(client,token,db):
    fam=db.scalar(select(User).where(User.username=='family'))
    r=client.post('/api/residents/2/family-grants',json={'invite_code':fam.invite_code},headers=token('staff2'))
    assert r.status_code==404


def test_staff_cannot_see_revoked_resident_vitals(client,token):
    r=client.post('/api/residents/1/vitals',json={'type':'weight','weight_kg':65},headers=token('resident'))
    assert r.status_code==201
    assert client.post('/api/residents/1/consents',json={'granted':False},headers=token('resident')).status_code==201
    assert client.get('/api/residents/1/vitals',headers=token('staff')).status_code==404
    assert client.post('/api/residents/1/vitals',json={'type':'weight','weight_kg':65},headers=token('staff')).status_code==404


def test_staff_search_safe_escape(client,token):
    assert client.get('/api/community/residents?q=%25',headers=token('staff')).status_code==200
    assert client.get('/api/community/residents?q=%5F',headers=token('staff')).json()==[]
