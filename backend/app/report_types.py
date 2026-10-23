"""Untrusted extraction candidates and explicit human review inputs."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReportItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=100)
    value: str = Field(max_length=100)
    unit: str = Field(default="", max_length=50)
    reference_range: str = Field(default="", max_length=150)
    flag: str = Field(default="", max_length=30)
    page: int = Field(ge=1, le=5)
    quote: str = Field(min_length=1, max_length=600)


class ReportImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(min_length=1, max_length=40)
    type: Literal["blood_pressure", "blood_glucose", "weight"]
    measured_at: datetime
    systolic: int | None = None
    diastolic: int | None = None
    glucose_mmol_l: float | None = None
    weight_kg: float | None = None


class ReportReview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    revision: int = Field(ge=1)
    identity_confirmed: bool
    title: str = Field(min_length=1, max_length=150)
    patient_name: str | None = Field(default=None, max_length=80)
    specimen_collected_at: str | None = Field(default=None, max_length=80)
    reported_at: str | None = Field(default=None, max_length=80)
    items: list[ReportItem] = Field(max_length=100)
    imports: list[ReportImport] = Field(default_factory=list, max_length=100)
