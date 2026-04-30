"""
工作室存储层 — SQLite + 文件混合
"""
import asyncio
import json
import shutil
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from models.studio import Studio, StudioCard, StudioCreate, StudioUpdate, SubAgentConfig
from storage.database import STUDIOS_DIR, get_db

# 按 studio_id 锁 read-modify-write 型更新，避免并发覆盖
_studio_card_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


class StudioStore:
    """工作室数据存储"""

    def _ensure_studio_dir(self, studio_id: str) -> Path:
        """确保工作室文件目录存在"""
        studio_dir = STUDIOS_DIR / studio_id
        studio_dir.mkdir(parents=True, exist_ok=True)
        (studio_dir / "sub_agents").mkdir(exist_ok=True)
        return studio_dir

    async def list_all(self) -> list[Studio]:
        """获取所有工作室"""
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT * FROM studios ORDER BY last_active DESC NULLS LAST, created_at DESC"
            )
            rows = await cursor.fetchall()
            studios = []
            for row in rows:
                studios.append(await self._row_to_studio(dict(row)))
            return studios
        finally:
            await db.close()

    async def get(self, studio_id: str) -> Optional[Studio]:
        """获取工作室详情"""
        db = await get_db()
        try:
            cursor = await db.execute("SELECT * FROM studios WHERE id = ?", (studio_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            return await self._row_to_studio(dict(row))
        finally:
            await db.close()

    async def create(self, req: StudioCreate) -> Studio:
        """创建工作室"""
        studio_id = str(uuid.uuid4())[:8]
        now = datetime.now()

        # 创建文件目录
        studio_dir = self._ensure_studio_dir(studio_id)

        # 写入 agent.md
        agent_md_content = f"# {req.scenario}\n\n{req.description}\n"
        (studio_dir / "agent.md").write_text(agent_md_content, encoding="utf-8")

        # 写入空 soul
        (studio_dir / "soul.md").write_text("# 工作室记忆\n\n暂无记忆。\n", encoding="utf-8")

        # 写入数据库
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO studios (id, scenario, description, core_capabilities, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (studio_id, req.scenario, req.description, json.dumps([]), now.isoformat(), now.isoformat())
            )

            # 写入 sub-agent 配置
            for sa in req.sub_agents:
                sa_id = getattr(sa, "id", None) or str(uuid.uuid4())[:8]
                sa_dir = studio_dir / "sub_agents" / sa_id
                sa_dir.mkdir(parents=True, exist_ok=True)

                # 写入 sub-agent 文件
                (sa_dir / "agent.md").write_text(sa.agent_md or f"# {sa.role}\n", encoding="utf-8")
                (sa_dir / "soul.md").write_text("# 记忆\n\n暂无。\n", encoding="utf-8")

                await db.execute(
                    """INSERT INTO sub_agent_configs (id, studio_id, role, agent_md_path, soul_path)
                       VALUES (?, ?, ?, ?, ?)""",
                    (sa_id, studio_id, sa.role,
                     str(sa_dir / "agent.md"), str(sa_dir / "soul.md"))
                )

            await db.commit()
        finally:
            await db.close()

        return await self.get(studio_id)

    async def update(self, studio_id: str, req: StudioUpdate) -> Optional[Studio]:
        """更新工作室"""
        existing = await self.get(studio_id)
        if not existing:
            return None

        db = await get_db()
        try:
            updates = []
            params = []
            if req.scenario is not None:
                updates.append("scenario = ?")
                params.append(req.scenario)
            if req.description is not None:
                updates.append("description = ?")
                params.append(req.description)

            if updates:
                updates.append("updated_at = ?")
                params.append(datetime.now().isoformat())
                params.append(studio_id)
                await db.execute(
                    f"UPDATE studios SET {', '.join(updates)} WHERE id = ?",
                    params
                )
                await db.commit()
        finally:
            await db.close()

        return await self.get(studio_id)

    async def delete(self, studio_id: str) -> bool:
        """删除工作室"""
        db = await get_db()
        try:
            cursor = await db.execute("DELETE FROM studios WHERE id = ?", (studio_id,))
            await db.commit()

            # 删除文件目录
            import shutil
            studio_dir = STUDIOS_DIR / studio_id
            if studio_dir.exists():
                shutil.rmtree(studio_dir)

            return cursor.rowcount > 0
        finally:
            await db.close()

    async def get_all_cards(self) -> list[dict]:
        """获取所有工作室名片（供 0号 Agent 路由用）"""
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT id, scenario, description, core_capabilities, recent_topics, user_facts, task_count, last_active FROM studios"
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            await db.close()

    async def _row_to_studio(self, row: dict) -> Studio:
        """将数据库行转换为 Studio 对象"""
        studio_id = row["id"]

        # 读取 sub-agent 配置
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT * FROM sub_agent_configs WHERE studio_id = ?", (studio_id,)
            )
            sa_rows = await cursor.fetchall()
        finally:
            await db.close()

        sub_agents = []
        for sa_row in sa_rows:
            sa = dict(sa_row)
            agent_md = ""
            soul = ""
            if sa.get("agent_md_path") and Path(sa["agent_md_path"]).exists():
                agent_md = Path(sa["agent_md_path"]).read_text(encoding="utf-8")
            if sa.get("soul_path") and Path(sa["soul_path"]).exists():
                soul = Path(sa["soul_path"]).read_text(encoding="utf-8")
            raw_skills = sa.get("skills", "[]")
            try:
                skills = json.loads(raw_skills) if raw_skills else []
            except Exception:
                skills = []
            sub_agents.append(SubAgentConfig(
                id=sa["id"], role=sa["role"], agent_md=agent_md, soul=soul,
                skills=skills, is_working=bool(sa.get("is_working", False)),
                total_tokens=sa.get("total_tokens", 0) or 0,
            ))

        def _safe_json_list(raw: str | None) -> list:
            if not raw:
                return []
            try:
                val = json.loads(raw)
                return val if isinstance(val, list) else []
            except Exception:
                return []

        return Studio(
            id=studio_id,
            scenario=row["scenario"],
            total_tokens=row.get("total_tokens", 0) or 0,
            sub_agents=sub_agents,
            card=StudioCard(
                description=row.get("description", "") or "",
                core_capabilities=_safe_json_list(row.get("core_capabilities")),
                recent_topics=_safe_json_list(row.get("recent_topics")),
                user_facts=_safe_json_list(row.get("user_facts")),
                task_count=row.get("task_count", 0) or 0,
                last_active=row.get("last_active"),
            ),
            created_at=row.get("created_at", datetime.now()),
            updated_at=row.get("updated_at", datetime.now()),
        )

    async def add_member(self, studio_id: str, role: str, skills: list[str], agent_md: str = "") -> Optional[SubAgentConfig]:
        """向工作室添加一名新员工"""
        studio_dir = self._ensure_studio_dir(studio_id)
        sa_id = str(uuid.uuid4())[:8]
        sa_dir = studio_dir / "sub_agents" / sa_id
        sa_dir.mkdir(parents=True, exist_ok=True)
        (sa_dir / "agent.md").write_text(agent_md or f"# {role}\n", encoding="utf-8")
        (sa_dir / "soul.md").write_text("# 记忆\n\n暂无。\n", encoding="utf-8")

        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO sub_agent_configs (id, studio_id, role, agent_md_path, soul_path, skills) VALUES (?, ?, ?, ?, ?, ?)",
                (sa_id, studio_id, role, str(sa_dir / "agent.md"), str(sa_dir / "soul.md"),
                 json.dumps(skills, ensure_ascii=False))
            )
            await db.commit()
        finally:
            await db.close()

        return SubAgentConfig(id=sa_id, role=role, agent_md=agent_md or f"# {role}\n",
                              soul="# 记忆\n\n暂无。\n", skills=skills)

    async def update_member(
        self,
        sa_id: str,
        role: Optional[str] = None,
        skills: Optional[list[str]] = None,
        agent_md: Optional[str] = None,
        soul: Optional[str] = None,
    ) -> bool:
        """更新成员信息（role / skills / agent_md / soul，均可选）"""
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT agent_md_path, soul_path FROM sub_agent_configs WHERE id = ?", (sa_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return False

            updates, params = [], []
            if role is not None:
                updates.append("role = ?")
                params.append(role)
            if skills is not None:
                updates.append("skills = ?")
                params.append(json.dumps(skills, ensure_ascii=False))

            if updates:
                params.append(sa_id)
                await db.execute(
                    f"UPDATE sub_agent_configs SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
                await db.commit()

            # 同步更新 agent.md 文件
            if agent_md is not None and row["agent_md_path"]:
                Path(row["agent_md_path"]).write_text(agent_md, encoding="utf-8")

            # 同步更新 soul.md 文件（员工经验记忆）
            if soul is not None and row["soul_path"]:
                Path(row["soul_path"]).write_text(soul, encoding="utf-8")

            return True
        finally:
            await db.close()

    async def delete_member(self, sa_id: str) -> bool:
        """删除工作室成员（含文件清理）"""
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT agent_md_path FROM sub_agent_configs WHERE id = ?", (sa_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return False

            await db.execute("DELETE FROM sub_agent_configs WHERE id = ?", (sa_id,))
            await db.commit()

            # 清理对应文件目录
            if row["agent_md_path"]:
                sa_dir = Path(row["agent_md_path"]).parent
                if sa_dir.exists():
                    shutil.rmtree(sa_dir, ignore_errors=True)
            return True
        finally:
            await db.close()

    async def add_tokens(self, sa_id: str, studio_id: str, tokens: int) -> None:
        """给指定成员和所在工作室各自累加 token 消耗量"""
        if tokens <= 0:
            return
        db = await get_db()
        try:
            await db.execute(
                "UPDATE sub_agent_configs SET total_tokens = total_tokens + ? WHERE id = ?",
                (tokens, sa_id)
            )
            await db.execute(
                "UPDATE studios SET total_tokens = total_tokens + ? WHERE id = ?",
                (tokens, studio_id)
            )
            await db.commit()
        finally:
            await db.close()

    async def update_agent_soul(self, sa_id: str, new_soul_content: str) -> bool:
        """将新的 soul 内容写回员工的 soul.md 文件"""
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT soul_path FROM sub_agent_configs WHERE id = ?", (sa_id,)
            )
            row = await cursor.fetchone()
            if not row or not row["soul_path"]:
                return False
            soul_path = Path(row["soul_path"])
            soul_path.parent.mkdir(parents=True, exist_ok=True)
            soul_path.write_text(new_soul_content, encoding="utf-8")
            return True
        finally:
            await db.close()

    async def update_studio_card(
        self,
        studio_id: str,
        new_topics: list[str],
        new_capabilities: list[str],
        new_facts: list[str] | None = None,
    ) -> None:
        """更新工作室名片：recent_topics、core_capabilities、user_facts、task_count、last_active。
        使用 per-studio asyncio 锁避免并发任务写回时相互覆盖。"""
        async with _studio_card_locks[studio_id]:
            studio = await self.get(studio_id)
            if not studio:
                return

            existing_topics = studio.card.recent_topics or []
            merged_topics = list(dict.fromkeys(new_topics + existing_topics))[:10]

            existing_caps = studio.card.core_capabilities or []
            merged_caps = list(dict.fromkeys(new_capabilities + existing_caps))[:20]

            existing_facts = studio.card.user_facts or []
            if new_facts:
                merged_facts = list(dict.fromkeys(new_facts + existing_facts))[:30]
            else:
                merged_facts = existing_facts

            now = datetime.now()
            db = await get_db()
            try:
                await db.execute(
                    """UPDATE studios
                       SET recent_topics = ?, core_capabilities = ?, user_facts = ?,
                           task_count = task_count + 1,
                           last_active = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        json.dumps(merged_topics, ensure_ascii=False),
                        json.dumps(merged_caps, ensure_ascii=False),
                        json.dumps(merged_facts, ensure_ascii=False),
                        now.isoformat(), now.isoformat(),
                        studio_id,
                    )
                )
                await db.commit()
            finally:
                await db.close()
