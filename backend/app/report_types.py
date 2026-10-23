"""Untrusted extraction candidates and explicit human review inputs."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class ReviewedReportItem(ReportItem):
    """Manual additions never impersonate the extractor's immutable quotes."""
    source_kind: Literal["extracted", "manual"] = "extracted"
    page: int | None = Field(default=None, ge=1, le=5)
    quote: str = Field(default="", max_length=600)

    @model_validator(mode="after")
    def validate_source(self):
        if self.source_kind == "extracted" and (self.page is None or not self.quote):
            raise ValueError("提取项目必须保留原始页码和原文")
        if self.source_kind == "manual" and self.quote:
            raise ValueError("人工补录项目不能声明模型原文引用")
        return self


class ReportReview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    revision: int = Field(ge=1)
    identity_confirmed: bool
    title: str = Field(min_length=1, max_length=150)
    patient_name: str | None = Field(default=None, max_length=80)
    specimen_collected_at: str | None = Field(default=None, max_length=80)
    reported_at: str | None = Field(default=None, max_length=80)
    items: list[ReviewedReportItem] = Field(max_length=200)
    excluded_item_ids: list[str] = Field(default_factory=list, max_length=200)
    imports: list[ReportImport] = Field(default_factory=list, max_length=100)


class ReportRevisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=2, max_length=500)
    review: ReportReview
