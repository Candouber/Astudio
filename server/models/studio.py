"""
工作室数据模型
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class StudioCard(BaseModel):
    """工作室名片 — 供 0号 Agent 快速路由"""
    description: str = ""
    core_capabilities: list[str] = Field(default_factory=list)
    recent_topics: list[str] = Field(default_factory=list)
    user_facts: list[str] = Field(default_factory=list)
    task_count: int = 0
    last_active: Optional[datetime] = None


class SubAgentConfig(BaseModel):
    """Sub-agent 配置"""
    id: str
    role: str
    agent_md: str = ""   # agent.md 文件内容
    soul: str = ""       # soul 文件内容
    skills: list[str] = Field(default_factory=list) # 员工绑定的工具/能力
    is_working: bool = False
    total_tokens: int = 0


class Studio(BaseModel):
    """工作室"""
    id: str
    scenario: str
    is_working: bool = False
    total_tokens: int = 0
    sub_agents: list[SubAgentConfig] = Field(default_factory=list)
    card: StudioCard = Field(default_factory=StudioCard)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)



class SubAgentConfigCreate(BaseModel):
    """创建 Sub-agent 时的输入（无需 id，由后端生成）"""
    role: str
    agent_md: str = ""
    skills: list[str] = Field(default_factory=list)


class StudioCreate(BaseModel):
    """创建工作室请求"""
    scenario: str
    description: str = ""
    sub_agents: list[SubAgentConfigCreate] = Field(default_factory=list)


class StudioUpdate(BaseModel):
    """更新工作室请求"""
    scenario: Optional[str] = None
    description: Optional[str] = None
