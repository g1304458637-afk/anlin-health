from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=100)
    type: Literal["measure", "questionnaire", "followup", "general"] = "measure"
    frequency: Literal["daily", "weekly"]
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    local_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    start_date: date
    end_date: date | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("请填写计划名称")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("请选择有效的 IANA 时区，例如 Asia/Shanghai")
        return value

    @model_validator(mode="after")
    def valid_schedule(self):
        if any(day < 0 or day > 6 for day in self.weekdays) or len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError("星期必须是不重复的 0 至 6，0 代表星期一")
        if self.frequency == "weekly" and not self.weekdays:
            raise ValueError("每周计划至少选择一天")
        if self.frequency == "daily" and self.weekdays:
            raise ValueError("每天计划无需选择星期")
        self.weekdays.sort()
        today = datetime.now(ZoneInfo(self.timezone)).date()
        if not today - timedelta(days=366) <= self.start_date <= today + timedelta(days=366):
            raise ValueError("开始日期需在今天前后一年内")
        if self.end_date and not self.start_date <= self.end_date <= self.start_date + timedelta(days=1826):
            raise ValueError("结束日期应不早于开始日期，且计划跨度不能超过五年")
        return self


class SnoozeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    due_at: datetime

    @field_validator("due_at")
    @classmethod
    def valid_due_at(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("延后时间必须包含时区")
        now = datetime.now(timezone.utc)
        if not now < value <= now + timedelta(days=30):
            raise ValueError("延后时间应在未来三十天内")
        return value.astimezone(timezone.utc)
