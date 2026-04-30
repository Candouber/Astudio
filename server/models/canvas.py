"""
画布数据模型
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class DeepDive(BaseModel):
    """追问记录"""
    id: str
    question: str
    answer: str = ""
    created_at: datetime = Field(default_factory=datetime.now)


class PathNode(BaseModel):
    """路径节点"""
    id: str
    iteration_id: Optional[str] = None
    type: Literal["agent_zero", "sub_agent", "user_intervention", "diverge"]
    agent_role: str = ""
    step_label: str = ""
    input: str = ""
    output: str = ""
    status: Literal["pending", "running", "completed", "error", "corrected", "deprecated"] = "pending"
    deep_dives: list[DeepDive] = Field(default_factory=list)
    distilled_summary: str = ""
    parent_id: Optional[str] = None
    position: dict = Field(default_factory=lambda: {"x": 0, "y": 0})


class PathEdge(BaseModel):
    """路径边"""
    id: str
    iteration_id: Optional[str] = None
    source: str
    target: str
    type: Literal["main", "correction", "diverge"] = "main"
