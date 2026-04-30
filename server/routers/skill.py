"""
公共 Skill 池 API 路由

支持两条新建路径：
  - `POST /skills/import`：把 SkillHub / ClawHub 的 skill URL 拉下来落地到本地
  - `POST /skills/ai-create`：由 LLM (skill_creator) 新生成一个本地 bundle skill

遗留的 `POST /skills/` 只保留给管理员/脚本使用，常规 UI 不走这条。
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, field_validator

from models.skill import (
    BundleSkillConfig,
    Skill,
    SkillAiCreateRequest,
    SkillCreate,
    SkillImportRequest,
    SkillUpdate,
)
from services.skill_import import (
    SkillImportError,
    import_skill_from_url,
    probe_skill_url,
    read_skill_md,
    refresh_bundle_skill,
)
from storage.skill_store import SkillStore
from tools.skill_hub import skill_creator as skill_creator_tool

router = APIRouter()
store = SkillStore()


class SkillProbeRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def _url_http(cls, v: str) -> str:
        v = (v or "").strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("URL 必须以 http:// 或 https:// 开头")
        return v


@router.get("/")
async def list_skills(include_disabled: bool = Query(True)) -> list[Skill]:
    return await store.list_all(include_disabled=include_disabled)


@router.post("/probe")
async def probe_skill(req: SkillProbeRequest) -> dict:
    """贴 URL 后给前端的实时预览：只识别来源并拉一份元数据，不落盘不入库。"""
    try:
        return await probe_skill_url(req.url)
    except SkillImportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"识别失败: {e}") from e


@router.post("/import", status_code=201)
async def import_skill(req: SkillImportRequest) -> Skill:
    """从 SkillHub / ClawHub 的 skill URL 导入并注册为本地 bundle skill。"""
    try:
        return await import_skill_from_url(req)
    except SkillImportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:  # pragma: no cover - 兜底
        raise HTTPException(status_code=500, detail=f"导入失败: {e}") from e


@router.post("/{slug}/refresh")
async def refresh_skill(slug: str) -> Skill:
    """按 bundle skill 的 source 重新拉取一份，覆盖本地文件与 skill_pool 行。"""
    try:
        return await refresh_bundle_skill(slug)
    except SkillImportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"刷新失败: {e}") from e


@router.get("/{slug}/skill-md")
async def get_skill_md(slug: str) -> dict:
    """返回 bundle skill 的 SKILL.md 原文（前端预览用）。"""
    try:
        return await read_skill_md(slug)
    except SkillImportError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"读取失败: {e}") from e


@router.post("/ai-create", status_code=201)
async def ai_create_skill(req: SkillAiCreateRequest) -> dict:
    """调用 skill_creator 工具（和 agent 用的是同一个实现）同步生成一个 skill。"""
    result_text = await skill_creator_tool(
        slug=req.slug,
        name=req.name,
        goal=req.goal,
        category=req.category,
    )
    if result_text.startswith("[错误]"):
        raise HTTPException(status_code=400, detail=result_text)
    skill = await store.get(req.slug)
    return {"skill": skill, "message": result_text}


@router.post("/", status_code=201)
async def create_skill(req: SkillCreate) -> Skill:
    """低层创建接口。常规流程请用 /import 或 /ai-create。
    仅允许创建 kind=bundle 且 config 合法的条目（builtin 由系统 seed）。"""
    if await store.get(req.slug):
        raise HTTPException(status_code=409, detail="Skill 已存在")
    if req.kind != "bundle":
        raise HTTPException(
            status_code=400,
            detail="新建 Skill 必须是 bundle 类型；请改用 /skills/import 或 /skills/ai-create",
        )
    try:
        BundleSkillConfig.model_validate(req.config)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"config 不符合 BundleSkillConfig: {e}") from e
    try:
        return await store.create(req)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.put("/{slug}")
async def update_skill(slug: str, req: SkillUpdate) -> Skill:
    existing = await store.get(slug)
    if not existing:
        raise HTTPException(status_code=404, detail="Skill 不存在")

    # 禁止改动内置 skill 的 kind / config（语义上那是平台实现的一部分）
    if existing.builtin:
        if req.kind is not None and req.kind != existing.kind:
            raise HTTPException(status_code=400, detail="内置 Skill 的 kind 不可修改")
        if req.config is not None and req.config != existing.config:
            raise HTTPException(status_code=400, detail="内置 Skill 的 config 不可修改")

    # 若目标会变成 bundle 且有 config 变更，提前校验
    target_kind = req.kind if req.kind is not None else existing.kind
    if target_kind == "bundle" and req.config is not None:
        try:
            BundleSkillConfig.model_validate(req.config)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"config 不符合 BundleSkillConfig: {e}") from e

    skill = await store.update(slug, req)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    return skill


@router.delete("/{slug}", status_code=204)
async def delete_skill(slug: str):
    existing = await store.get(slug)
    if not existing:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    if existing.builtin:
        raise HTTPException(status_code=400, detail="内置 Skill 不能删除，可以停用")
    if not await store.delete(slug):
        raise HTTPException(status_code=404, detail="Skill 不存在")
