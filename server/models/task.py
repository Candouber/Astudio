"""
任务数据模型
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .canvas import PathEdge, PathNode


class SubTask(BaseModel):
    """子任务/流水线细化工单"""
    id: str
    task_id: str
    iteration_id: Optional[str] = None
    studio_id: Optional[str] = None
    group_id: Optional[str] = None
    step_id: str = ""                   # Leader 分配的逻辑步骤 ID（用于 DAG 依赖引用）
    depends_on: list[str] = Field(default_factory=list)  # 依赖的 step_id 列表
    step_label: str
    assign_to_role: str
    input_context: str
    status: Literal[
        "pending",
        "running",
        "pending_review",       # 员工已提交，等待 Leader 质检
        "revision_requested",   # Leader 打回，需要重做
        "accepted",             # Leader 质检通过
        "blocked",              # 不可恢复的阻塞
    ] = "pending"
    deliverable: Optional[str] = None
    blocker_reason: Optional[str] = None
    review_feedback: Optional[str] = None  # Leader 质检反馈
    attempt_index: int = 1
    retry_count: int = 0
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    distilled_summary: Optional[str] = None
    # 成本 / 耗时观测
    tokens: int = 0
    duration_ms: int = 0
    cost_usd: float = 0.0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    model_name: Optional[str] = None
    # 人类干预痕迹
    edited_by_user: bool = False
    edited_at: Optional[datetime] = None


class Task(BaseModel):
    """任务"""
    id: str
    current_iteration_id: Optional[str] = None
    sandbox_owner_type: str = "task"
    sandbox_owner_id: Optional[str] = None
    studio_id: Optional[str] = None
    question: str
    nodes: list[PathNode] = Field(default_factory=list)
    edges: list[PathEdge] = Field(default_factory=list)
    sub_tasks: list[SubTask] = Field(default_factory=list)
    iterations: list["TaskIteration"] = Field(default_factory=list)
    plan_steps: list[dict] = Field(default_factory=list)           # Leader 规划步骤，等待审批时持久化
    plan_studio_id: Optional[str] = None                           # 规划关联的工作室 ID
    clarification_questions: list[dict] = Field(default_factory=list)  # Leader 待用户确认的问题列表
    clarification_answers: dict = Field(default_factory=dict)          # 用户的回答 {question_id: answer}
    status: Literal[
        "planning",
        "need_clarification",
        "await_leader_plan_approval",
        "executing",
        "terminated",
        "completed",
        "completed_with_blockers",
        "timeout_killed",
        "failed",
    ] = "planning"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    failure_reason: str = ""
    # 人可读进展文案（如「0 号正在评估…」）；与 status 正交，供轮询 /stream 与列表展示
    status_message: str = ""


class TaskIteration(BaseModel):
    """任务的一轮执行/迭代。Task 是工作空间，Iteration 是一次运行。"""
    id: str
    task_id: str
    parent_iteration_id: Optional[str] = None
    source_node_id: Optional[str] = None
    title: str = ""
    instruction: str = ""
    status: Literal[
        "planning",
        "need_clarification",
        "await_leader_plan_approval",
        "executing",
        "terminated",
        "completed",
        "completed_with_blockers",
        "timeout_killed",
        "failed",
    ] = "planning"
    plan_steps: list[dict] = Field(default_factory=list)
    plan_studio_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    summary: str = ""


class AskRequest(BaseModel):
    """向 0号 Agent 提问"""
    question: str


class CorrectRequest(BaseModel):
    """纠错请求"""
    node_id: str
    correction_type: Literal["input", "direction", "conclusion"]
    new_content: str


class DeepDiveRequest(BaseModel):
    """追问请求"""
    node_id: str
    question: str


class DivergeRequest(BaseModel):
    """发散请求"""
    node_id: str
    direction: str  # 发散方向描述
