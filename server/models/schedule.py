"""
定时任务数据模型
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

ScheduleKind = Literal["at", "every", "cron"]
ApprovalPolicy = Literal["auto_execute", "require_plan_review"]
OverlapPolicy = Literal["skip", "queue"]
RunStatus = Literal["running", "ok", "error", "skipped"]


class ScheduledJobBase(BaseModel):
    name: str = ""
    message: str
    enabled: bool = True
    schedule_kind: ScheduleKind
    at_time: Optional[datetime] = None
    every_seconds: Optional[int] = None
    cron_expr: Optional[str] = None
    timezone: Optional[str] = None
    target_studio_id: Optional[str] = None
    approval_policy: ApprovalPolicy = "auto_execute"
    overlap_policy: OverlapPolicy = "skip"
    delete_after_run: bool = False
    created_by: str = "agent"

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.schedule_kind == "at" and not self.at_time:
            raise ValueError("at_time is required when schedule_kind is 'at'")
        if self.schedule_kind == "every" and (not self.every_seconds or self.every_seconds <= 0):
            raise ValueError("every_seconds must be positive when schedule_kind is 'every'")
        if self.schedule_kind == "cron" and not self.cron_expr:
            raise ValueError("cron_expr is required when schedule_kind is 'cron'")
        if self.timezone and self.schedule_kind != "cron":
            raise ValueError("timezone is only supported with cron schedules")
        return self


class ScheduledJobCreate(ScheduledJobBase):
    pass


class ScheduledJobUpdate(BaseModel):
    name: Optional[str] = None
    message: Optional[str] = None
    enabled: Optional[bool] = None
    schedule_kind: Optional[ScheduleKind] = None
    at_time: Optional[datetime] = None
    every_seconds: Optional[int] = None
    cron_expr: Optional[str] = None
    timezone: Optional[str] = None
    target_studio_id: Optional[str] = None
    approval_policy: Optional[ApprovalPolicy] = None
    overlap_policy: Optional[OverlapPolicy] = None
    delete_after_run: Optional[bool] = None


class ScheduledJob(ScheduledJobBase):
    id: str
    next_run_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ScheduledJobRun(BaseModel):
    id: str
    job_id: str
    task_id: Optional[str] = None
    status: RunStatus = "running"
    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
