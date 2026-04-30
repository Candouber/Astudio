"""
任务存储层
"""
import json
import uuid
from datetime import datetime
from typing import Optional

from models.canvas import DeepDive, PathEdge, PathNode
from models.task import SubTask, Task, TaskIteration
from storage.database import get_db


class TaskStore:
    """任务数据存储"""

    async def get(self, task_id: str) -> Optional[Task]:
        """获取任务详情（含画布数据）"""
        db = await get_db()
        try:
            # 获取任务
            cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            task_data = dict(row)
            current_iteration_id = task_data.get("current_iteration_id")

            # 获取迭代
            cursor = await db.execute(
                "SELECT * FROM task_iterations WHERE task_id = ? ORDER BY created_at",
                (task_id,),
            )
            iteration_rows = await cursor.fetchall()
            iterations = [self._row_to_iteration(dict(r)) for r in iteration_rows]

            # 获取节点
            cursor = await db.execute(
                "SELECT * FROM path_nodes WHERE task_id = ? ORDER BY created_at",
                (task_id,)
            )
            node_rows = await cursor.fetchall()
            node_ids = [dict(r)["id"] for r in node_rows]

            # 一次性批量拉取该任务下所有 deep_dives（避免 N+1）
            dd_by_node: dict[str, list[DeepDive]] = {}
            if node_ids:
                placeholders = ",".join("?" for _ in node_ids)
                dd_cursor = await db.execute(
                    f"SELECT * FROM deep_dives WHERE node_id IN ({placeholders}) ORDER BY created_at",
                    tuple(node_ids),
                )
                dd_rows = await dd_cursor.fetchall()
                for rr in dd_rows:
                    dd = dict(rr)
                    dd_by_node.setdefault(dd["node_id"], []).append(
                        DeepDive(
                            id=dd["id"],
                            question=dd["question"],
                            answer=dd.get("answer", ""),
                            created_at=dd.get("created_at", datetime.now()),
                        )
                    )

            nodes = []
            for nr in node_rows:
                nd = dict(nr)
                deep_dives = dd_by_node.get(nd["id"], [])
                nodes.append(PathNode(
                    id=nd["id"],
                    iteration_id=nd.get("iteration_id"),
                    type=nd["type"],
                    agent_role=nd.get("agent_role", ""),
                    step_label=nd.get("step_label", ""),
                    input=nd.get("input", ""),
                    output=nd.get("output", ""),
                    status=nd.get("status", "pending"),
                    deep_dives=deep_dives,
                    distilled_summary=nd.get("distilled_summary", ""),
                    parent_id=nd.get("parent_id"),
                    position={"x": nd.get("position_x", 0), "y": nd.get("position_y", 0)},
                ))

            # 获取路径边
            cursor = await db.execute("SELECT * FROM path_edges WHERE task_id = ?", (task_id,))
            edge_rows = await cursor.fetchall()
            edges = [
                PathEdge(
                    id=er["id"],
                    iteration_id=er.get("iteration_id"),
                    source=er["source_id"],
                    target=er["target_id"],
                    type=er.get("type", "main")
                )
                for er in [dict(r) for r in edge_rows]
            ]

            # 获取子任务工单
            cursor = await db.execute("SELECT * FROM sub_tasks WHERE task_id = ? ORDER BY created_at", (task_id,))
            st_rows = await cursor.fetchall()
            sub_tasks = []
            for r in st_rows:
                sr = dict(r)
                raw_depends = sr.get("depends_on", "[]")
                try:
                    depends_on = json.loads(raw_depends) if raw_depends else []
                except Exception:
                    depends_on = []
                sub_tasks.append(SubTask(
                    id=sr["id"],
                    task_id=sr["task_id"],
                    iteration_id=sr.get("iteration_id"),
                    studio_id=sr.get("studio_id"),
                    group_id=sr.get("group_id"),
                    step_id=sr.get("step_id", ""),
                    depends_on=depends_on,
                    step_label=sr["step_label"],
                    assign_to_role=sr["assign_to_role"],
                    input_context=sr["input_context"],
                    status=sr.get("status", "pending"),
                    deliverable=sr.get("deliverable"),
                    blocker_reason=sr.get("blocker_reason"),
                    review_feedback=sr.get("review_feedback"),
                    attempt_index=sr.get("attempt_index", 1),
                    retry_count=sr.get("retry_count", 0),
                    created_at=sr.get("created_at", datetime.now()),
                    updated_at=sr.get("updated_at", datetime.now()),
                    distilled_summary=sr.get("distilled_summary"),
                    tokens=int(sr.get("tokens") or 0),
                    duration_ms=int(sr.get("duration_ms") or 0),
                    cost_usd=float(sr.get("cost_usd") or 0.0),
                    started_at=sr.get("started_at"),
                    finished_at=sr.get("finished_at"),
                    model_name=sr.get("model_name"),
                    edited_by_user=bool(sr.get("edited_by_user") or 0),
                    edited_at=sr.get("edited_at"),
                ))

            try:
                plan_steps = json.loads(task_data.get("plan_steps") or "[]")
            except Exception:
                plan_steps = []

            try:
                clarification_questions = json.loads(task_data.get("clarification_questions") or "[]")
            except Exception:
                clarification_questions = []

            try:
                clarification_answers = json.loads(task_data.get("clarification_answers") or "{}")
            except Exception:
                clarification_answers = {}

            return Task(
                id=task_data["id"],
                current_iteration_id=current_iteration_id,
                sandbox_owner_type=task_data.get("sandbox_owner_type") or "task",
                sandbox_owner_id=task_data.get("sandbox_owner_id") or task_data["id"],
                studio_id=task_data.get("studio_id"),
                question=task_data["question"],
                status=self._coerce_status(task_data.get("status")),
                nodes=nodes,
                edges=edges,
                sub_tasks=sub_tasks,
                iterations=iterations,
                plan_steps=plan_steps,
                plan_studio_id=task_data.get("plan_studio_id"),
                clarification_questions=clarification_questions,
                clarification_answers=clarification_answers,
                created_at=task_data.get("created_at", datetime.now()),
                updated_at=task_data.get("updated_at", datetime.now()),
                started_at=task_data.get("started_at"),
                last_activity_at=task_data.get("last_activity_at"),
                completed_at=task_data.get("completed_at"),
                failure_reason=(task_data.get("failure_reason") or "") or "",
                status_message=(task_data.get("status_message") or "") or "",
            )
        finally:
            await db.close()

    async def save_plan_steps(self, task_id: str, studio_id: str, steps: list) -> None:
        """持久化 Leader 规划方案步骤，供用户刷新后恢复审批状态"""
        db = await get_db()
        try:
            now = datetime.now().isoformat()
            await db.execute(
                "UPDATE tasks SET plan_steps=?, plan_studio_id=?, studio_id=?, updated_at=? WHERE id=?",
                (json.dumps(steps, ensure_ascii=False), studio_id, studio_id, now, task_id)
            )
            await db.execute(
                """UPDATE task_iterations
                   SET plan_steps=?, plan_studio_id=?, updated_at=?
                   WHERE id=(SELECT current_iteration_id FROM tasks WHERE id=?)""",
                (json.dumps(steps, ensure_ascii=False), studio_id, now, task_id)
            )
            await db.commit()
        finally:
            await db.close()

    async def save_iteration_plan_steps(self, iteration_id: str, studio_id: str, steps: list) -> None:
        db = await get_db()
        try:
            await db.execute(
                "UPDATE task_iterations SET plan_steps=?, plan_studio_id=?, updated_at=? WHERE id=?",
                (json.dumps(steps, ensure_ascii=False), studio_id, datetime.now().isoformat(), iteration_id)
            )
            await db.commit()
        finally:
            await db.close()

    async def begin_iteration(
        self,
        task_id: str,
        instruction: str,
        title: str = "继续迭代",
        source_node_id: Optional[str] = None,
        parent_iteration_id: Optional[str] = None,
    ) -> TaskIteration:
        """创建并切换到一轮新的任务迭代。"""
        db = await get_db()
        try:
            if parent_iteration_id is None:
                parent_iteration_id = await self._get_current_iteration_id_with_db(db, task_id)
            iteration_id = f"it_{uuid.uuid4().hex[:10]}"
            now = datetime.now().isoformat()
            await db.execute(
                """INSERT INTO task_iterations
                   (id, task_id, parent_iteration_id, source_node_id, title, instruction,
                    status, created_at, updated_at, started_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'planning', ?, ?, ?)""",
                (
                    iteration_id,
                    task_id,
                    parent_iteration_id,
                    source_node_id,
                    title,
                    instruction[:4000],
                    now,
                    now,
                    now,
                ),
            )
            await db.execute(
                """UPDATE tasks
                   SET current_iteration_id=?, plan_steps='[]', plan_studio_id=NULL,
                       clarification_questions='[]', clarification_answers='{}',
                       updated_at=?, last_activity_at=?
                   WHERE id=?""",
                (iteration_id, now, now, task_id),
            )
            await db.commit()
        finally:
            await db.close()
        return TaskIteration(
            id=iteration_id,
            task_id=task_id,
            parent_iteration_id=parent_iteration_id,
            source_node_id=source_node_id,
            title=title,
            instruction=instruction[:4000],
            status="planning",
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
            started_at=datetime.fromisoformat(now),
        )

    async def get_current_iteration_id(self, task_id: str) -> Optional[str]:
        db = await get_db()
        try:
            return await self._get_current_iteration_id_with_db(db, task_id)
        finally:
            await db.close()

    async def get_current_iteration_root_node_id(self, task_id: str) -> Optional[str]:
        db = await get_db()
        try:
            iteration_id = await self._get_current_iteration_id_with_db(db, task_id)
            if not iteration_id:
                return None
            cursor = await db.execute(
                """SELECT id FROM path_nodes
                   WHERE task_id=? AND iteration_id=? AND type='agent_zero'
                   ORDER BY created_at DESC LIMIT 1""",
                (task_id, iteration_id),
            )
            row = await cursor.fetchone()
            return row["id"] if row else None
        finally:
            await db.close()

    async def get_current_iteration(self, task_id: str) -> Optional[TaskIteration]:
        db = await get_db()
        try:
            cursor = await db.execute(
                """SELECT * FROM task_iterations
                   WHERE id=(SELECT current_iteration_id FROM tasks WHERE id=?)""",
                (task_id,),
            )
            row = await cursor.fetchone()
            return self._row_to_iteration(dict(row)) if row else None
        finally:
            await db.close()

    async def link_current_iteration_root(self, task_id: str, root_node_id: str) -> None:
        """若当前 iteration 有来源节点，则在画布上连一条迭代分支边。"""
        db = await get_db()
        try:
            cursor = await db.execute(
                """SELECT id, source_node_id FROM task_iterations
                   WHERE id=(SELECT current_iteration_id FROM tasks WHERE id=?)""",
                (task_id,),
            )
            row = await cursor.fetchone()
            if not row or not row["source_node_id"] or row["source_node_id"] == root_node_id:
                return
            edge_id = f"iter-{row['source_node_id']}-{root_node_id}"
            await db.execute(
                """INSERT OR IGNORE INTO path_edges
                   (id, task_id, iteration_id, source_id, target_id, type)
                   VALUES (?, ?, ?, ?, ?, 'diverge')""",
                (edge_id, task_id, row["id"], row["source_node_id"], root_node_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def save_clarification(self, task_id: str, studio_id: str, questions: list) -> None:
        """持久化 Leader 的需求确认问题"""
        db = await get_db()
        try:
            await db.execute(
                "UPDATE tasks SET clarification_questions=?, studio_id=? WHERE id=?",
                (json.dumps(questions, ensure_ascii=False), studio_id, task_id)
            )
            await db.commit()
        finally:
            await db.close()

    async def save_clarification_answers(self, task_id: str, answers: dict) -> None:
        """持久化用户的确认回答"""
        db = await get_db()
        try:
            await db.execute(
                "UPDATE tasks SET clarification_answers=? WHERE id=?",
                (json.dumps(answers, ensure_ascii=False), task_id)
            )
            await db.commit()
        finally:
            await db.close()

    async def delete(self, task_id: str) -> bool:
        """删除任务及其关联的节点、边、子任务（通过 ON DELETE CASCADE）"""
        db = await get_db()
        try:
            cursor = await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            await db.commit()
            return cursor.rowcount > 0
        finally:
            await db.close()

    async def create(
        self,
        question: str,
        studio_id: Optional[str] = None,
        sandbox_owner_type: str = "task",
        sandbox_owner_id: Optional[str] = None,
    ) -> Task:
        """创建新任务"""
        task_id = str(uuid.uuid4())[:8]
        now = datetime.now()
        owner_type = sandbox_owner_type or "task"
        owner_id = sandbox_owner_id or task_id

        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO tasks
                   (id, current_iteration_id, sandbox_owner_type, sandbox_owner_id, studio_id,
                    question, status, created_at, updated_at, last_activity_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id, f"it_{task_id}_0", owner_type, owner_id, studio_id,
                    question, "planning",
                    now.isoformat(), now.isoformat(), now.isoformat(),
                )
            )
            await db.execute(
                """INSERT INTO task_iterations
                   (id, task_id, title, instruction, status, created_at, updated_at, started_at)
                   VALUES (?, ?, ?, ?, 'planning', ?, ?, ?)""",
                (
                    f"it_{task_id}_0", task_id, "初始执行", question[:1000],
                    now.isoformat(), now.isoformat(), now.isoformat(),
                ),
            )
            await db.commit()
        finally:
            await db.close()

        return Task(
            id=task_id,
            current_iteration_id=f"it_{task_id}_0",
            sandbox_owner_type=owner_type,
            sandbox_owner_id=owner_id,
            studio_id=studio_id,
            question=question,
            status="planning",
            created_at=now,
            updated_at=now,
            last_activity_at=now,
        )

    async def add_node(self, task_id: str, node: PathNode) -> PathNode:
        """添加路径节点"""
        db = await get_db()
        try:
            now = datetime.now().isoformat()
            iteration_id = node.iteration_id or await self._get_current_iteration_id_with_db(db, task_id)
            node.iteration_id = iteration_id
            await db.execute(
                """INSERT INTO path_nodes
                   (id, task_id, iteration_id, type, agent_role, step_label, input, output, status,
                   distilled_summary, parent_id, position_x, position_y)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (node.id, task_id, iteration_id, node.type, node.agent_role, node.step_label,
                 node.input, node.output, node.status, node.distilled_summary,
                 node.parent_id, node.position.get("x", 0), node.position.get("y", 0))
            )
            await db.execute(
                "UPDATE tasks SET updated_at = ?, last_activity_at = ? WHERE id = ?",
                (now, now, task_id),
            )
            await db.commit()
        finally:
            await db.close()
        return node

    async def update_node_status(self, node_id: str, status: str, output: str = None):
        """更新节点状态"""
        db = await get_db()
        try:
            now = datetime.now().isoformat()
            if output is not None:
                await db.execute(
                    "UPDATE path_nodes SET status = ?, output = ? WHERE id = ?",
                    (status, output, node_id)
                )
            else:
                await db.execute(
                    "UPDATE path_nodes SET status = ? WHERE id = ?",
                    (status, node_id)
                )
            await db.execute(
                """UPDATE tasks
                   SET updated_at = ?, last_activity_at = ?
                   WHERE id = (SELECT task_id FROM path_nodes WHERE id = ?)""",
                (now, now, node_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def update_node_distilled_summary(self, node_id: str, summary: str):
        """更新节点的蒸馏摘要"""
        db = await get_db()
        try:
            await db.execute(
                "UPDATE path_nodes SET distilled_summary = ? WHERE id = ?",
                (summary, node_id)
            )
            await db.commit()
        finally:
            await db.close()

    async def add_edge(self, task_id: str, edge: PathEdge):
        """添加路径边"""
        db = await get_db()
        try:
            iteration_id = edge.iteration_id or await self._get_current_iteration_id_with_db(db, task_id)
            edge.iteration_id = iteration_id
            await db.execute(
                "INSERT INTO path_edges (id, task_id, iteration_id, source_id, target_id, type) VALUES (?, ?, ?, ?, ?, ?)",
                (edge.id, task_id, iteration_id, edge.source, edge.target, edge.type)
            )
            await db.commit()
        finally:
            await db.close()

    async def update_task_status(self, task_id: str, status: str):
        """更新任务状态"""
        db = await get_db()
        try:
            now = datetime.now().isoformat()
            terminal_statuses = {
                "completed", "completed_with_blockers", "timeout_killed", "terminated", "failed",
            }
            active_statuses = {"planning", "executing"}

            updates = ["status = ?", "updated_at = ?"]
            params: list = [status, now]

            if status in active_statuses:
                updates.append("started_at = COALESCE(started_at, ?)")
                params.append(now)
                updates.append("last_activity_at = ?")
                params.append(now)
            if status in terminal_statuses:
                updates.append("completed_at = ?")
                params.append(now)
            else:
                updates.append("completed_at = NULL")
            if status not in {"failed", "timeout_killed", "terminated"}:
                updates.append("failure_reason = ''")

            params.append(task_id)
            await db.execute(
                f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            iter_updates = ["status = ?", "updated_at = ?"]
            iter_params: list = [status, now]
            if status in active_statuses:
                iter_updates.append("started_at = COALESCE(started_at, ?)")
                iter_params.append(now)
            if status in terminal_statuses:
                iter_updates.append("completed_at = ?")
                iter_params.append(now)
            elif status in active_statuses or status in {"need_clarification", "await_leader_plan_approval"}:
                iter_updates.append("completed_at = NULL")
            iter_params.append(task_id)
            await db.execute(
                f"""UPDATE task_iterations
                    SET {', '.join(iter_updates)}
                    WHERE id=(SELECT current_iteration_id FROM tasks WHERE id=?)""",
                iter_params,
            )
            await db.commit()
        finally:
            await db.close()

    async def set_task_failure(self, task_id: str, status: str, reason: str) -> None:
        """记录失败/终止类状态及其原因。"""
        db = await get_db()
        try:
            now = datetime.now().isoformat()
            await db.execute(
                """UPDATE tasks
                   SET status = ?, failure_reason = ?, status_message = ?,
                       updated_at = ?, last_activity_at = ?, completed_at = ?
                   WHERE id = ?""",
                (status, reason, reason, now, now, now, task_id),
            )
            await db.execute(
                """UPDATE task_iterations
                   SET status=?, summary=?, updated_at=?, completed_at=?
                   WHERE id=(SELECT current_iteration_id FROM tasks WHERE id=?)""",
                (status, reason, now, now, task_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def set_status_message(self, task_id: str, message: str) -> None:
        """更新人可读进展（与 status 正交），并刷新任务活动时间。"""
        db = await get_db()
        try:
            now = datetime.now().isoformat()
            await db.execute(
                "UPDATE tasks SET status_message = ?, updated_at = ?, last_activity_at = ? WHERE id = ?",
                (message, now, now, task_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def touch_task_activity(self, task_id: str, status_message: Optional[str] = None) -> None:
        """仅刷新任务活跃时间；可选顺带更新进展文案。"""
        db = await get_db()
        try:
            now = datetime.now().isoformat()
            if status_message is None:
                await db.execute(
                    "UPDATE tasks SET updated_at = ?, last_activity_at = ? WHERE id = ?",
                    (now, now, task_id),
                )
            else:
                await db.execute(
                    """UPDATE tasks
                       SET status_message = ?, updated_at = ?, last_activity_at = ?
                       WHERE id = ?""",
                    (status_message, now, now, task_id),
                )
            await db.commit()
        finally:
            await db.close()

    async def list_all(self) -> list[Task]:
        """获取全部任务（不含画布详情），按创建时间倒序"""
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()
            return [self._row_to_task_summary(dict(r)) for r in rows]
        finally:
            await db.close()

    async def list_by_studio(self, studio_id: str) -> list[Task]:
        """获取工作室的所有任务（不含画布详情）"""
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT * FROM tasks WHERE studio_id = ? ORDER BY created_at DESC",
                (studio_id,)
            )
            rows = await cursor.fetchall()
            return [self._row_to_task_summary(dict(r)) for r in rows]
        finally:
            await db.close()

    # Task.status 合法枚举集合（与 models/task.py 保持同步）
    _ALLOWED_STATUSES = {
        "planning", "need_clarification", "await_leader_plan_approval",
        "executing", "terminated", "completed", "completed_with_blockers",
        "timeout_killed", "failed",
    }

    @classmethod
    def _coerce_status(cls, raw: str | None) -> str:
        """防御性兜底：DB 中若出现未知状态字符串（例如旧版本遗留），降级为 planning，
        避免 pydantic Literal 校验直接让整个 list 接口 500。"""
        if raw in cls._ALLOWED_STATUSES:
            return raw
        return "planning"

    @classmethod
    def _row_to_task_summary(cls, row: dict) -> Task:
        return Task(
            id=row["id"],
            current_iteration_id=row.get("current_iteration_id"),
            sandbox_owner_type=row.get("sandbox_owner_type") or "task",
            sandbox_owner_id=row.get("sandbox_owner_id") or row["id"],
            studio_id=row.get("studio_id"),
            question=row["question"],
            status=cls._coerce_status(row.get("status")),
            created_at=row.get("created_at", datetime.now()),
            updated_at=row.get("updated_at", datetime.now()),
            started_at=row.get("started_at"),
            last_activity_at=row.get("last_activity_at"),
            completed_at=row.get("completed_at"),
            failure_reason=(row.get("failure_reason") or "") or "",
            status_message=(row.get("status_message") or "") or "",
        )

    @classmethod
    def _row_to_iteration(cls, row: dict) -> TaskIteration:
        try:
            plan_steps = json.loads(row.get("plan_steps") or "[]")
        except Exception:
            plan_steps = []
        return TaskIteration(
            id=row["id"],
            task_id=row["task_id"],
            parent_iteration_id=row.get("parent_iteration_id"),
            source_node_id=row.get("source_node_id"),
            title=row.get("title") or "",
            instruction=row.get("instruction") or "",
            status=cls._coerce_status(row.get("status")),
            plan_steps=plan_steps,
            plan_studio_id=row.get("plan_studio_id"),
            created_at=row.get("created_at", datetime.now()),
            updated_at=row.get("updated_at", datetime.now()),
            started_at=row.get("started_at"),
            completed_at=row.get("completed_at"),
            summary=row.get("summary") or "",
        )

    async def _get_current_iteration_id_with_db(self, db, task_id: str) -> Optional[str]:
        cursor = await db.execute("SELECT current_iteration_id FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        return row["current_iteration_id"] if row and row["current_iteration_id"] else None

    # --- SubTask 扩展 ---

    async def add_sub_task(self, sub_task: SubTask) -> SubTask:
        """添加子任务(细化工单)"""
        db = await get_db()
        try:
            iteration_id = sub_task.iteration_id or await self._get_current_iteration_id_with_db(db, sub_task.task_id)
            sub_task.iteration_id = iteration_id
            await db.execute(
                """INSERT INTO sub_tasks
                   (id, task_id, iteration_id, studio_id, group_id, step_id, depends_on,
                    step_label, assign_to_role, input_context, status,
                    deliverable, blocker_reason, review_feedback,
                    attempt_index, retry_count, created_at, updated_at, distilled_summary)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sub_task.id, sub_task.task_id, iteration_id, sub_task.studio_id, sub_task.group_id,
                    sub_task.step_id, json.dumps(sub_task.depends_on, ensure_ascii=False),
                    sub_task.step_label, sub_task.assign_to_role, sub_task.input_context, sub_task.status,
                    sub_task.deliverable, sub_task.blocker_reason, sub_task.review_feedback,
                    sub_task.attempt_index, sub_task.retry_count,
                    sub_task.created_at.isoformat(), sub_task.updated_at.isoformat(),
                    sub_task.distilled_summary,
                )
            )
            await db.commit()
        finally:
            await db.close()
        return sub_task

    async def update_sub_task_review(
        self, sub_task_id: str, status: str, review_feedback: Optional[str] = None
    ):
        """更新质检状态与反馈（pending_review / revision_requested / accepted）"""
        db = await get_db()
        try:
            updates = ["status = ?", "updated_at = ?"]
            params: list = [status, datetime.now().isoformat()]
            if review_feedback is not None:
                updates.append("review_feedback = ?")
                params.append(review_feedback)
            params.append(sub_task_id)
            await db.execute(
                f"UPDATE sub_tasks SET {', '.join(updates)} WHERE id = ?", params
            )
            await db.commit()
        finally:
            await db.close()

    async def update_sub_task_status(
        self, sub_task_id: str, status: str, deliverable: Optional[str] = None, blocker_reason: Optional[str] = None
    ):
        """更新子任务(细化工单)状态及产出"""
        db = await get_db()
        try:
            updates = ["status = ?", "updated_at = ?"]
            params = [status, datetime.now().isoformat()]
            if deliverable is not None:
                updates.append("deliverable = ?")
                params.append(deliverable)
            if blocker_reason is not None:
                updates.append("blocker_reason = ?")
                params.append(blocker_reason)

            params.append(sub_task_id)

            await db.execute(
                f"UPDATE sub_tasks SET {', '.join(updates)} WHERE id = ?",
                params
            )
            await db.commit()
        finally:
            await db.close()

    async def increment_sub_task_retry(self, sub_task_id: str) -> int:
        """增加子任务的重试次数"""
        db = await get_db()
        try:
            cursor = await db.execute(
                "UPDATE sub_tasks SET retry_count = retry_count + 1, updated_at = ? WHERE id = ? RETURNING retry_count",
                (datetime.now().isoformat(), sub_task_id)
            )
            row = await cursor.fetchone()
            await db.commit()
            return row["retry_count"] if row else 0
        finally:
            await db.close()

    async def mark_sub_task_started(self, sub_task_id: str) -> None:
        """记录子任务开始执行的时间戳。幂等：若已有 started_at 则不覆盖。"""
        db = await get_db()
        try:
            await db.execute(
                "UPDATE sub_tasks SET started_at = COALESCE(started_at, ?), updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(), datetime.now().isoformat(), sub_task_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def record_sub_task_metrics(
        self,
        sub_task_id: str,
        tokens: int,
        duration_ms: int,
        cost_usd: float,
        model_name: Optional[str] = None,
    ) -> None:
        """累加记录子任务消耗的 tokens / duration / cost（可被多次调用，会累加）。
        finished_at 会被更新为当前时间。
        """
        db = await get_db()
        try:
            await db.execute(
                """UPDATE sub_tasks
                   SET tokens = COALESCE(tokens, 0) + ?,
                       duration_ms = COALESCE(duration_ms, 0) + ?,
                       cost_usd = COALESCE(cost_usd, 0) + ?,
                       model_name = COALESCE(?, model_name),
                       finished_at = ?,
                       updated_at = ?
                   WHERE id = ?""",
                (
                    int(tokens or 0),
                    int(duration_ms or 0),
                    float(cost_usd or 0.0),
                    model_name,
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                    sub_task_id,
                ),
            )
            await db.commit()
        finally:
            await db.close()

    async def manual_edit_sub_task(
        self, sub_task_id: str, new_deliverable: str
    ) -> None:
        """用户手动编辑 deliverable：覆盖产出、打上 edited_by_user 标记。"""
        db = await get_db()
        try:
            now = datetime.now().isoformat()
            await db.execute(
                """UPDATE sub_tasks
                   SET deliverable = ?,
                       status = 'accepted',
                       edited_by_user = 1,
                       edited_at = ?,
                       updated_at = ?
                   WHERE id = ?""",
                (new_deliverable, now, now, sub_task_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def update_sub_task_summary(self, sub_task_id: str, summary: str):
        """更新子任务蒸馏结果摘要"""
        db = await get_db()
        try:
            await db.execute(
                "UPDATE sub_tasks SET distilled_summary = ?, updated_at = ? WHERE id = ?",
                (summary, datetime.now().isoformat(), sub_task_id)
            )
            await db.commit()
        finally:
            await db.close()

    async def clear_execution_state(self, task_id: str):
        """
        清理当前 iteration 的执行状态（用于"沿用原方案重新执行"）。
        历史 iteration 的节点/边/子任务保留在二维画布中，不再整任务清空。
        """
        db = await get_db()
        try:
            now = datetime.now().isoformat()
            iteration_id = await self._get_current_iteration_id_with_db(db, task_id)
            if not iteration_id:
                return None

            # 删除当前 iteration 的子任务
            await db.execute(
                "DELETE FROM sub_tasks WHERE task_id = ? AND iteration_id = ?",
                (task_id, iteration_id),
            )

            # 删除当前 iteration 的非根节点
            cursor = await db.execute(
                """SELECT id FROM path_nodes
                   WHERE task_id = ? AND iteration_id = ? AND type = 'agent_zero'
                   ORDER BY rowid ASC LIMIT 1""",
                (task_id, iteration_id),
            )
            root_row = await cursor.fetchone()
            root_node_id = root_row["id"] if root_row else None

            if root_node_id:
                await db.execute(
                    "DELETE FROM path_nodes WHERE task_id = ? AND iteration_id = ? AND id != ?",
                    (task_id, iteration_id, root_node_id)
                )
                # 重置根节点状态
                await db.execute(
                    "UPDATE path_nodes SET status = 'pending', output = '' WHERE id = ?",
                    (root_node_id,)
                )
            else:
                await db.execute(
                    "DELETE FROM path_nodes WHERE task_id = ? AND iteration_id = ?",
                    (task_id, iteration_id),
                )

            # 删除当前 iteration 的边（重新执行时会重新生成）
            await db.execute(
                "DELETE FROM path_edges WHERE task_id = ? AND iteration_id = ?",
                (task_id, iteration_id),
            )
            await db.execute(
                "UPDATE tasks SET updated_at = ?, last_activity_at = ?, failure_reason = '' WHERE id = ?",
                (now, now, task_id),
            )

            await db.commit()
            return root_node_id
        finally:
            await db.close()

    # ── 批注 CRUD ──────────────────────────────────────────────────────

    async def create_annotation(
        self, ann_id: str, task_id: str, node_id: str,
        selected_text: str, question: str,
    ) -> None:
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO annotations (id, task_id, node_id, selected_text, question, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ann_id, task_id, node_id, selected_text, question, datetime.now().isoformat()),
            )
            await db.commit()
        finally:
            await db.close()

    async def update_annotation_answer(self, ann_id: str, answer: str) -> None:
        db = await get_db()
        try:
            await db.execute("UPDATE annotations SET answer = ? WHERE id = ?", (answer, ann_id))
            await db.commit()
        finally:
            await db.close()

    async def list_annotations(self, task_id: str) -> list[dict]:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT * FROM annotations WHERE task_id = ? ORDER BY created_at DESC",
                (task_id,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        finally:
            await db.close()

    async def delete_annotation(self, ann_id: str) -> None:
        db = await get_db()
        try:
            await db.execute("DELETE FROM annotations WHERE id = ?", (ann_id,))
            await db.commit()
        finally:
            await db.close()
