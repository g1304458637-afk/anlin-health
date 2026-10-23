from datetime import datetime, date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class LoginIn(Model):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class TotpLoginIn(Model):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)
    totp_code: str = Field(min_length=6, max_length=6)


class PasswordChangeIn(Model):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class WxLoginIn(Model):
    code: str = Field(min_length=5, max_length=512)


class RegisterFamilyIn(Model):
    username: str = Field(min_length=4, max_length=40)
    display_name: str = Field(min_length=2, max_length=40)
    password: str = Field(min_length=1, max_length=200)


class ProvisionAccountIn(Model):
    username: str = Field(min_length=4, max_length=40)
    display_name: str = Field(min_length=2, max_length=40)
    initial_password: str = Field(min_length=1, max_length=200)


class UserOut(Model):
    id: int
    username: str
    display_name: str
    role: str
    community_id: int | None
    invite_code: str | None = None


class AdminUserOut(Model):
    id: int
    username: str
    display_name: str
    role: str
    community_id: int | None
    is_active: bool
    mfa_enabled: bool
    last_login_at: datetime | None
    password_updated_at: datetime | None


class ResidentIn(Model):
    name: str = Field(min_length=2, max_length=80)
    birth_year: int = Field(ge=1900, le=2100)
    sex: Literal["male", "female", "undisclosed"] = "undisclosed"
    phone: str | None = Field(default=None, max_length=30)
    community_id: int = Field(ge=1)


class SelfResidentIn(ResidentIn):
    consent_to_community_care: bool


class AssistedResidentIn(ResidentIn):
    consent_confirmed: bool
    evidence_ref: str = Field(min_length=5, max_length=200)


class ResidentPatch(Model):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    birth_year: int | None = Field(default=None, ge=1900, le=2100)
    sex: Literal["male", "female", "undisclosed"] | None = None
    phone: str | None = Field(default=None, max_length=30)


class ResidentOut(Model):
    id: int
    name: str
    birth_year: int
    sex: str
    phone: str | None
    community_id: int
    owner_user_id: int | None
    consent_active: bool | None = None


class ConsentIn(Model):
    granted: bool
    evidence_ref: str | None = Field(default=None, max_length=200)


class ConsentOut(Model):
    id: int
    resident_id: int
    granted: bool
    purpose: str
    method: str
    evidence_ref: str | None
    review_status: str = "approved"
    review_note: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime


class ConsentReviewIn(Model):
    approve: bool
    note: str | None = Field(default=None, max_length=300)


class ClaimRequestIn(Model):
    resident_id: int = Field(ge=1)
    full_name: str = Field(min_length=2, max_length=80)
    phone: str | None = Field(default=None, max_length=30)
    note: str | None = Field(default=None, max_length=300)


class ClaimRequestOut(Model):
    id: int
    resident_id: int
    requester_id: int
    requester_name: str
    status: str
    stated_name: str
    stated_phone: str | None
    name_matched: bool
    phone_matched: bool
    note: str | None
    reviewed_at: datetime | None
    created_at: datetime


class ClaimReviewIn(Model):
    approve: bool
    note: str | None = Field(default=None, max_length=300)


class VitalIn(Model):
    type: Literal["blood_pressure", "blood_glucose", "weight"]
    systolic: int | None = None
    diastolic: int | None = None
    glucose_mmol_l: float | None = None
    weight_kg: float | None = None
    measured_at: datetime | None = None

    @model_validator(mode="after")
    def validate_values(self):
        if self.type == "blood_pressure":
            if self.systolic is None or self.diastolic is None or not 40 <= self.systolic <= 300 or not 20 <= self.diastolic <= 200 or self.systolic <= self.diastolic:
                raise ValueError("血压必须填写合理范围的收缩压与舒张压")
            if self.glucose_mmol_l is not None or self.weight_kg is not None:
                raise ValueError("血压记录不应混入其他指标")
        elif self.type == "blood_glucose":
            if self.glucose_mmol_l is None or not 0.5 <= self.glucose_mmol_l <= 50:
                raise ValueError("血糖值超出录入范围")
            if any(v is not None for v in (self.systolic, self.diastolic, self.weight_kg)):
                raise ValueError("血糖记录不应混入其他指标")
        else:
            if self.weight_kg is None or not 5 <= self.weight_kg <= 400:
                raise ValueError("体重值超出录入范围")
            if any(v is not None for v in (self.systolic, self.diastolic, self.glucose_mmol_l)):
                raise ValueError("体重记录不应混入其他指标")
        return self


class VitalOut(Model):
    id: int
    resident_id: int
    type: str
    systolic: int | None
    diastolic: int | None
    glucose_mmol_l: float | None
    weight_kg: float | None
    measured_at: datetime


class QuestionnaireOut(Model):
    id: int
    slug: str
    title: str
    version: int
    category: str
    questions: list


class AnswersIn(Model):
    questionnaire_id: int
    answers: dict[str, str] = Field(min_length=1, max_length=40)


class ResponseOut(Model):
    id: int
    resident_id: int
    questionnaire_id: int
    questionnaire_version: int
    answers: dict
    created_at: datetime


class ReminderIn(Model):
    type: Literal["measure", "questionnaire", "followup"]
    title: str = Field(min_length=2, max_length=120)
    due_date: date


class ReminderOut(Model):
    id: int
    resident_id: int
    type: str
    title: str
    due_date: date
    completed_at: datetime | None


class FamilyGrantIn(Model):
    invite_code: str = Field(min_length=10, max_length=32)
    can_profile: bool = True
    can_vitals: bool = True
    expires_in_days: int = Field(default=30, ge=1, le=365)

    @model_validator(mode="after")
    def require_scope(self):
        if not self.can_profile and not self.can_vitals:
            raise ValueError("至少选择一项共享范围")
        return self


class FamilyGrantOut(Model):
    id: int
    resident_id: int
    grantee_id: int
    grantee_name: str
    can_profile: bool
    can_vitals: bool
    expires_at: datetime
    revoked_at: datetime | None


class VisitIn(Model):
    resident_id: int
    due_date: date
    note: str | None = Field(default=None, max_length=500)


class VisitFinish(Model):
    note: str | None = Field(default=None, max_length=500)


class VisitOut(Model):
    id: int
    resident_id: int
    staff_id: int
    due_date: date
    status: str
    note: str | None
    finished_at: datetime | None
