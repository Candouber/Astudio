"""
公共 Skill 池数据模型

一条 Skill 代表一个可被 agent 使用的能力。支持两种 `kind`：

- `builtin` — 对应 `server/tools/*.py` 里的 Python 实现（web_search / use_skill / ...）
- `bundle`  — 从 SkillHub / ClawHub 等社区导入，或由 `skill_creator` 生成的
              "Claude Skill 包"（一个带 `SKILL.md` 的文件夹）。agent 不直接 function-call
              bundle，而是统一通过 builtin 工具 `use_skill(slug=...)` 加载说明后执行。

注意：早期 P1 阶段曾支持 `kind=http` 的自定义 HTTP 接口，后被"URL 导入 + 元技能"
方案替换（参见 `tools.skill_hub`）。遗留的 http 行会被 `tools.registry` 视为不可执行。
"""
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

SkillKind = Literal["builtin", "bundle"]

SkillProvider = Literal["clawhub", "skillhub_cn", "local", "github"]


class BundleSkillSource(BaseModel):
    """bundle skill 的来源信息，用来追溯与更新。"""

    provider: SkillProvider = "local"
    # 原始 URL（贴进来的）；`local` 来源为 None
    url: Optional[str] = None
    # 平台侧的 owner / slug / version（主要给 clawhub 用）
    username: Optional[str] = None
    slug: Optional[str] = None
    version: Optional[str] = None
    # GitHub 源码坐标（clawhub 的 skill 真正文件在 GitHub 上）
    source_type: Optional[str] = None  # "github" | None
    source_identifier: Optional[str] = None  # e.g. "acme/skills"
    default_branch: Optional[str] = None  # e.g. "main"
    skill_path: Optional[str] = None  # skill 在 repo 里的相对路径


class BundleSkillConfig(BaseModel):
    """kind=bundle 时 `Skill.config` 反序列化出来的结构。"""

    source: BundleSkillSource = Field(default_factory=BundleSkillSource)
    # 本地文件夹相对路径（相对项目根），例如 `data/workspace/skills/acme__data-analysis`
    local_dir: str
    # SKILL.md 里抽出的简短说明，LLM prompt 里展示用，避免每次都塞整个 md
    summary: str = ""
    # 文件清单，仅用于 UI 显示；真正加载文件时由 use_skill 动态读盘
    files: list[str] = Field(default_factory=list)

    @field_validator("local_dir")
    @classmethod
    def _local_dir_non_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("local_dir 不能为空")
        return v


class SkillBase(BaseModel):
    slug: str = Field(pattern=r"^[a-zA-Z0-9_\-]+$")
    name: str
    description: str = ""
    category: str = "通用"
    enabled: bool = True
    kind: SkillKind = "builtin"
    config: dict[str, Any] = Field(default_factory=dict)


class SkillCreate(SkillBase):
    """直接创建一条空 skill；常规路径应走 `SkillImportRequest` 或 `SkillAiCreateRequest`。
    这里仍保留为 admin/内部使用。"""

    @model_validator(mode="after")
    def _bundle_requires_config(self) -> "SkillCreate":
        if self.kind == "bundle":
            BundleSkillConfig.model_validate(self.config)
        return self


class SkillUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    enabled: Optional[bool] = None
    # 注意：kind / config 一般不由用户在 UI 直接改（bundle 由导入/生成来源决定）
    kind: Optional[SkillKind] = None
    config: Optional[dict[str, Any]] = None


class Skill(SkillBase):
    builtin: bool = False
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class SkillImportRequest(BaseModel):
    """从 SkillHub / ClawHub URL 导入 bundle skill。"""

    url: str
    # 允许用户自定义一个 slug；None 则用 provider 返回的 slug
    override_slug: Optional[str] = Field(default=None, pattern=r"^[a-zA-Z0-9_\-]+$")
    category: str = "导入"

    @field_validator("url")
    @classmethod
    def _url_http(cls, v: str) -> str:
        v = (v or "").strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("URL 必须以 http:// 或 https:// 开头")
        return v


class SkillAiCreateRequest(BaseModel):
    """让 skill_creator 用模型生成一个全新的 bundle skill。"""

    slug: str = Field(pattern=r"^[a-zA-Z0-9_\-]+$")
    name: str
    goal: str  # 想要这个 skill 做什么，一段自然语言
    category: str = "自定义"
