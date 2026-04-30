"""
任务沙箱数据模型
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

SandboxStatus = Literal["ready", "running", "stopped", "error"]
SandboxRunStatus = Literal["running", "ok", "error", "stopped"]


class Sandbox(BaseModel):
    id: str
    owner_type: str = "task"
    owner_id: str = ""
    task_id: str
    path: str
    status: SandboxStatus = "ready"
    title: str = ""
    description: str = ""
    runtime_type: str = "local"
    dev_port: Optional[int] = None
    preview_url: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    last_active_at: Optional[datetime] = None


class SandboxRun(BaseModel):
    id: str
    sandbox_id: str
    task_id: str
    command: str
    cwd: str = "."
    status: SandboxRunStatus = "running"
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    stdout_path: Optional[str] = None
    stderr_path: Optional[str] = None
    preview_url: Optional[str] = None
    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None


class SandboxCreateResponse(BaseModel):
    sandbox: Sandbox
    created: bool


class SandboxFile(BaseModel):
    name: str
    path: str
    kind: Literal["file", "directory"]
    size: int = 0
    updated_at: Optional[datetime] = None


class SandboxWriteFileRequest(BaseModel):
    path: str
    content: str


class SandboxRunRequest(BaseModel):
    command: str
    cwd: str = "."
    background: bool = False
    timeout_seconds: int = 120
