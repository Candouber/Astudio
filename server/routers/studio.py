"""
工作室 API 路由
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from models.studio import Studio, StudioCreate, StudioUpdate
from storage.skill_store import SkillStore
from storage.studio_store import StudioStore

router = APIRouter()
store = StudioStore()
skill_store = SkillStore()


# 内置 skill 兜底：当 Skill 池暂未初始化或查询失败时使用，保证编辑页不至于完全清零。
_BUILTIN_SKILL_SLUGS = {"web_search", "execute_code", "read_file", "write_file", "list_files"}


async def _safe_skills(candidate: Optional[list[str]]) -> Optional[list[str]]:
    """把前端提交的 skill 列表按当前 Skill 池（含内置 + 用户自定义）过滤一遍。
    Skill 池查询异常时退化为内置集合，避免整块保存失败。"""
    if candidate is None:
        return None
    try:
        allowed = await skill_store.list_enabled_slugs()
    except Exception:
        allowed = set(_BUILTIN_SKILL_SLUGS)
    allowed = allowed | _BUILTIN_SKILL_SLUGS
    return [s for s in candidate if s in allowed]


@router.get("/")
async def list_studios() -> list[Studio]:
    """获取所有工作室"""
    return await store.list_all()


@router.get("/{studio_id}")
async def get_studio(studio_id: str) -> Studio:
    """获取工作室详情"""
    studio = await store.get(studio_id)
    if not studio:
        raise HTTPException(status_code=404, detail="工作室不存在")
    return studio


@router.post("/", status_code=201)
async def create_studio(req: StudioCreate) -> Studio:
    """创建工作室"""
    return await store.create(req)


@router.put("/{studio_id}")
async def update_studio(studio_id: str, req: StudioUpdate) -> Studio:
    """更新工作室"""
    studio = await store.update(studio_id, req)
    if not studio:
        raise HTTPException(status_code=404, detail="工作室不存在")
    return studio


@router.delete("/{studio_id}", status_code=204)
async def delete_studio(studio_id: str):
    """删除工作室"""
    success = await store.delete(studio_id)
    if not success:
        raise HTTPException(status_code=404, detail="工作室不存在")


@router.get("/{studio_id}/tasks")
async def list_studio_tasks(studio_id: str):
    """获取工作室的历史任务"""
    from storage.task_store import TaskStore
    task_store = TaskStore()
    return await task_store.list_by_studio(studio_id)


# ── 成员管理 ──────────────────────────────────────────────────────────────


class MemberCreate(BaseModel):
    role: str
    skills: list[str] = Field(default_factory=list)
    agent_md: str = ""


class MemberUpdate(BaseModel):
    role: Optional[str] = None
    skills: Optional[list[str]] = None
    agent_md: Optional[str] = None
    # 员工的经验记忆（soul.md 内容）。历史版本漏掉这个字段，导致编辑页保存 soul 被静默丢弃。
    soul: Optional[str] = None


@router.post("/{studio_id}/members", status_code=201)
async def add_member(studio_id: str, req: MemberCreate):
    """向工作室添加成员"""
    studio = await store.get(studio_id)
    if not studio:
        raise HTTPException(404, "工作室不存在")
    safe_skills = await _safe_skills(req.skills) or []
    member = await store.add_member(studio_id, req.role.strip(), safe_skills, req.agent_md)
    return member


@router.put("/{studio_id}/members/{member_id}")
async def update_member(studio_id: str, member_id: str, req: MemberUpdate):
    """编辑成员信息（role / skills / agent_md / soul，可单独传）"""
    safe_skills = await _safe_skills(req.skills)
    ok = await store.update_member(
        member_id,
        role=req.role,
        skills=safe_skills,
        agent_md=req.agent_md,
        soul=req.soul,
    )
    if not ok:
        raise HTTPException(404, "成员不存在")
    return await store.get(studio_id)


@router.delete("/{studio_id}/members/{member_id}", status_code=204)
async def delete_member(studio_id: str, member_id: str):
    """删除工作室成员"""
    ok = await store.delete_member(member_id)
    if not ok:
        raise HTTPException(404, "成员不存在")
