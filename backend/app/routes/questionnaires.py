from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from ..db import get_db
from ..models import User, Resident, Questionnaire, QuestionnaireResponse
from ..deps import auth_user, check_access, is_owner, is_staff_for, audit
from ..schemas import QuestionnaireOut, AnswersIn, ResponseOut

router = APIRouter(prefix="/api", tags=["questionnaires"])


@router.get("/questionnaires", response_model=list[QuestionnaireOut])
def list_questionnaires(user: User = Depends(auth_user), db: Session = Depends(get_db)):
    return db.scalars(select(Questionnaire).where(Questionnaire.published.is_(True)).order_by(Questionnaire.id)).all()


@router.get("/questionnaires/{questionnaire_id}", response_model=QuestionnaireOut)
def get_questionnaire(questionnaire_id: int, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    q = db.get(Questionnaire, questionnaire_id)
    if not q or not q.published:
        raise HTTPException(404, "问卷不存在")
    return q


@router.post("/residents/{resident_id}/responses", response_model=ResponseOut, status_code=201)
def submit_responses(resident_id: int, body: AnswersIn, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    resident = check_access(db, user, db.get(Resident, resident_id), write=True)
    questionnaire = db.get(Questionnaire, body.questionnaire_id)
    if not questionnaire or not questionnaire.published:
        raise HTTPException(404, "问卷不存在")
    # Psychological and wellbeing answers are never entered by staff, and never exposed to staff/family.
    if questionnaire.category == "wellbeing" and not is_owner(user, resident):
        raise HTTPException(403, "此问卷仅供本人填写")
    expected = {item["id"]: item for item in questionnaire.questions}
    if set(body.answers) != set(expected):
        raise HTTPException(422, "问卷题目不完整或包含无效题目")
    for key, val in body.answers.items():
        options = expected[key].get("options", [])
        if not isinstance(val, str) or val not in {x["value"] for x in options}:
            raise HTTPException(422, f"题目 {key} 的选项无效")
    response = QuestionnaireResponse(resident_id=resident_id, questionnaire_id=questionnaire.id,
        questionnaire_version=questionnaire.version, answers=dict(body.answers), completed_by_id=user.id)
    db.add(response)
    db.flush()
    audit(db, user, "submit_questionnaire", "questionnaire_response", response.id)
    db.commit()
    return response


@router.get("/residents/{resident_id}/responses", response_model=list[ResponseOut])
def my_responses(resident_id: int, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    resident = db.get(Resident, resident_id)
    if not resident or not is_owner(user, resident):
        raise HTTPException(404, "记录不存在或无权限")
    return db.scalars(select(QuestionnaireResponse).where(QuestionnaireResponse.resident_id == resident_id).order_by(QuestionnaireResponse.id.desc()).limit(100)).all()
