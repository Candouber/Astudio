"""
公共 Skill 池存储层
"""
import json
from datetime import datetime
from typing import Any, Optional

from models.skill import Skill, SkillCreate, SkillUpdate
from storage.database import get_db

DEFAULT_SKILLS = [
    {
        "slug": "file_analysis",
        "name": "附件分析",
        "description": "分析用户上传的 Excel/CSV、PDF、图片和文本附件；会自动暴露附件读取、表格读取、PDF 抽文本、图片元数据工具。",
        "category": "文件",
    },
    {
        "slug": "list_attachments",
        "name": "列出附件",
        "description": "列出当前任务上传的附件清单。",
        "category": "文件",
    },
    {
        "slug": "read_uploaded_file",
        "name": "读取上传文件",
        "description": "读取当前任务上传的文本、CSV、JSON、Markdown 文件。",
        "category": "文件",
    },
    {
        "slug": "read_excel_sheet",
        "name": "读取 Excel",
        "description": "读取当前任务上传的 Excel .xlsx 或 CSV 文件，支持按 sheet 读取预览行。",
        "category": "文件",
    },
    {
        "slug": "read_pdf_text",
        "name": "读取 PDF",
        "description": "提取当前任务上传 PDF 的文本内容。",
        "category": "文件",
    },
    {
        "slug": "image_metadata",
        "name": "图片元数据",
        "description": "读取当前任务上传图片的格式、尺寸和大小。",
        "category": "文件",
    },
    {
        "slug": "web_search",
        "name": "网络搜索",
        "description": "网络搜索、信息检索、竞品调研、资料收集。普通搜索失败时会自动尝试浏览器搜索兜底。",
        "category": "检索",
    },
    {
        "slug": "browser_search",
        "name": "浏览器搜索",
        "description": "使用项目内置浏览器打开搜索结果页并提取标题、链接和摘要，作为 web_search 的本地兜底能力。",
        "category": "检索",
    },
    {
        "slug": "execute_code",
        "name": "代码执行",
        "description": "代码编写与执行、数据处理、脚本自动化。会自动附带任务沙箱能力。",
        "category": "工程",
    },
    {
        "slug": "read_file",
        "name": "读取文件",
        "description": "读取本地文件、文档解析。会自动附带任务沙箱读取能力。",
        "category": "文件",
    },
    {
        "slug": "write_file",
        "name": "写入文件",
        "description": "创建或更新文件、生成报告。会自动附带任务沙箱写入能力。",
        "category": "文件",
    },
    {
        "slug": "list_files",
        "name": "浏览文件",
        "description": "浏览目录结构、查找资源。会自动附带任务沙箱文件列表能力。",
        "category": "文件",
    },
    {
        "slug": "ensure_sandbox",
        "name": "准备任务沙箱",
        "description": "为当前任务创建或获取独立沙箱目录与端口，适合需要终端、运行服务或隔离产物的任务。",
        "category": "沙箱",
    },
    {
        "slug": "sandbox_list_files",
        "name": "浏览沙箱文件",
        "description": "列出当前任务沙箱内的目录和文件，适合检查构建产物、脚本和日志。",
        "category": "沙箱",
    },
    {
        "slug": "sandbox_read_file",
        "name": "读取沙箱文件",
        "description": "读取当前任务沙箱内的文件内容，适合查看日志、输出结果和生成后的文件。",
        "category": "沙箱",
    },
    {
        "slug": "sandbox_write_file",
        "name": "写入沙箱文件",
        "description": "向当前任务沙箱写入文件，适合生成脚本、配置、页面和测试数据。",
        "category": "沙箱",
    },
    {
        "slug": "sandbox_run_command",
        "name": "终端命令",
        "description": "像 terminal/bash 一样在当前任务沙箱内运行 shell 命令。适合执行 CLI、构建、测试、浏览器自动化和本地服务启动。",
        "category": "沙箱",
    },
    {
        "slug": "sandbox_start_preview",
        "name": "沙箱预览",
        "description": "为当前任务沙箱里生成的 HTML 页面创建预览链接，便于查看前端产物。",
        "category": "沙箱",
    },
    {
        "slug": "schedule_task",
        "name": "定时任务",
        "description": "创建、列出、删除或立即运行 AStudio 定时任务。只建议给系统管理类角色。",
        "category": "系统",
    },
    # ── Skill 元技能（对接 SkillHub/ClawHub） ─────────────────────────────
    {
        "slug": "use_skill",
        "name": "加载 Skill 包",
        "description": "按 slug 加载本地已安装的 Skill 包 (SKILL.md + 资源)，把指南注入当前上下文，之后你可以用 read_file/execute_code 访问 skill 目录继续执行。",
        "category": "Skill",
    },
    {
        "slug": "find_skill",
        "name": "搜索 Skill",
        "description": "在 SkillHub / ClawHub 社区中按自然语言需求搜索合适的 Skill 包，返回候选列表和安装 URL，供用户决定导入。",
        "category": "Skill",
    },
    {
        "slug": "skill_creator",
        "name": "生成 Skill",
        "description": "按自然语言目标，用模型新生成一个 SKILL.md 并落地到本地 data/workspace/skills/，自动注册为 bundle 类型 skill。",
        "category": "Skill",
    },
]


def _parse_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if value:
        return datetime.fromisoformat(str(value))
    return datetime.now()


def _loads_config(raw: Any) -> dict[str, Any]:
    """安全地把 DB 里的 config 文本反序列化为 dict，容错坏数据。"""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except (TypeError, ValueError):
        return {}


class SkillStore:
    """公共 Skill 池数据存储"""

    async def list_all(self, include_disabled: bool = True) -> list[Skill]:
        db = await get_db()
        try:
            sql = "SELECT * FROM skill_pool"
            if not include_disabled:
                sql += " WHERE enabled = 1"
            sql += " ORDER BY builtin DESC, category ASC, slug ASC"
            cursor = await db.execute(sql)
            rows = await cursor.fetchall()
            return [self._row_to_skill(dict(row)) for row in rows]
        finally:
            await db.close()

    async def list_enabled_slugs(self) -> set[str]:
        skills = await self.list_all(include_disabled=False)
        return {skill.slug for skill in skills}

    async def get(self, slug: str) -> Optional[Skill]:
        db = await get_db()
        try:
            cursor = await db.execute("SELECT * FROM skill_pool WHERE slug = ?", (slug,))
            row = await cursor.fetchone()
            return self._row_to_skill(dict(row)) if row else None
        finally:
            await db.close()

    async def create(self, req: SkillCreate) -> Skill:
        now = datetime.now().isoformat()
        config_json = json.dumps(req.config or {}, ensure_ascii=False)
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO skill_pool
                   (slug, name, description, category, enabled, builtin, kind, config, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)""",
                (
                    req.slug.strip(),
                    req.name.strip() or req.slug.strip(),
                    req.description.strip(),
                    req.category.strip() or "通用",
                    req.enabled,
                    req.kind,
                    config_json,
                    now,
                    now,
                ),
            )
            await db.commit()
        finally:
            await db.close()
        skill = await self.get(req.slug.strip())
        assert skill is not None
        return skill

    async def update(self, slug: str, req: SkillUpdate) -> Optional[Skill]:
        fields = req.model_dump(exclude_unset=True)
        if not fields:
            return await self.get(slug)

        updates: list[str] = []
        params: list = []
        for key in ("name", "description", "category", "enabled", "kind"):
            if key in fields:
                updates.append(f"{key} = ?")
                value = fields[key]
                params.append(value.strip() if isinstance(value, str) else value)
        if "config" in fields:
            updates.append("config = ?")
            params.append(json.dumps(fields["config"] or {}, ensure_ascii=False))

        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(slug)

        db = await get_db()
        try:
            cursor = await db.execute(
                f"UPDATE skill_pool SET {', '.join(updates)} WHERE slug = ?",
                params,
            )
            await db.commit()
            if cursor.rowcount <= 0:
                return None
        finally:
            await db.close()
        return await self.get(slug)

    async def delete(self, slug: str) -> bool:
        db = await get_db()
        try:
            cursor = await db.execute(
                "DELETE FROM skill_pool WHERE slug = ? AND builtin = 0",
                (slug,),
            )
            await db.commit()
            return cursor.rowcount > 0
        finally:
            await db.close()

    @staticmethod
    def _row_to_skill(row: dict) -> Skill:
        kind_raw = row.get("kind") or "builtin"
        # 兼容历史数据：P1 早期曾有 kind=http 的行，现在一律视为 builtin 占位（会在
        # tools.registry._is_slug_runnable 里被过滤掉，不会真的出现在可用工具里）。
        # 合法 kind 只剩 builtin / bundle。
        if kind_raw not in ("builtin", "bundle"):
            kind_raw = "builtin"
        return Skill(
            slug=row["slug"],
            name=row.get("name") or row["slug"],
            description=row.get("description") or "",
            category=row.get("category") or "通用",
            enabled=bool(row.get("enabled", True)),
            builtin=bool(row.get("builtin", False)),
            kind=kind_raw,  # type: ignore[arg-type]
            config=_loads_config(row.get("config")),
            created_at=_parse_dt(row.get("created_at")),
            updated_at=_parse_dt(row.get("updated_at")),
        )
