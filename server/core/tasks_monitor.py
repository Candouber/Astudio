"""
异步分层任务监控系统与守护 watchdog。
"""
import asyncio
import os
from datetime import datetime

from loguru import logger

from agents.agent_zero import agent_zero
from agents.context import ContextBuilder
from agents.studio_leader_executor import studio_leader
from i18n.status_message_codec import encode_task_status_msg as enc
from services.llm_service import llm_service
from storage.sandbox_store import SandboxStore
from storage.studio_store import StudioStore
from storage.task_store import TaskStore
from utils.language import is_chinese

WATCHDOG_INTERVAL_SECONDS = 15
PLANNING_STALE_SECONDS = 180
EXECUTING_STALE_SECONDS = 240
SOUL_COMPRESS_THRESHOLD = 2000  # soul 文件超过此字符数时触发压缩
SOUL_MAX_CHARS = 1500           # 压缩后目标字符数
SUMMARY_MIN_CHARS = 120
SUMMARY_STRONG_MIN_CHARS = 260
SUMMARY_VALID_ENDINGS = set("。.!！?？)）]】」”’`")
SUMMARY_BAD_ENDINGS = set("，,、；;：:-—")


class TaskMonitor:
    def __init__(self):
        self.task_store = TaskStore()
        self.studio_store = StudioStore()
        self.sandbox_store = SandboxStore()
        # 保证 finalize / consolidate 对同一个 task 只跑一次
        self._finalizing: set = set()
        self._consolidated: set = set()
        self._finalize_lock = asyncio.Lock()
        self._watchdog_task: asyncio.Task | None = None

    @staticmethod
    def _parse_dt(raw):
        if isinstance(raw, datetime):
            return raw
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw)
            except Exception:
                return None
        return None

    async def recover_running_tasks(self):
        """
        在应用启动时拉起常驻 watchdog，并立即扫描一次活跃任务。
        """
        self.start_watchdog()
        await self.scan_active_tasks_once()

    def start_watchdog(self) -> None:
        if self._watchdog_task and not self._watchdog_task.done():
            return
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        logger.info("Task watchdog started")

    async def _watchdog_loop(self):
        while True:
            try:
                await self.scan_active_tasks_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(f"Task watchdog scan failed: {e}")
            await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)

    async def scan_active_tasks_once(self):
        """
        巡检所有 planning / executing 任务：
          1. 若所有子任务都已终态但未 finalize，自动补 finalize
          2. 若长时间无活动，自动落到用户可感知、可恢复的状态
        """
        from storage.database import get_db
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT id, status, created_at, updated_at, last_activity_at FROM tasks WHERE status IN ('planning', 'executing')"
            )
            rows = await cursor.fetchall()
            for row in rows:
                task_id = row["id"]
                task = await self.task_store.get(task_id)
                if not task:
                    continue

                current_sub_tasks = [
                    st for st in task.sub_tasks
                    if not task.current_iteration_id or st.iteration_id == task.current_iteration_id
                ]
                if current_sub_tasks:
                    done_statuses = {"accepted", "blocked"}
                    all_done = all(st.status in done_statuses for st in current_sub_tasks)
                    has_blockers = any(st.status == "blocked" for st in current_sub_tasks)
                    if all_done:
                        logger.info(f"Watchdog: finalize dangling task {task_id}")
                        await self._finalize_task(task_id, task.question, has_blockers)
                        continue

                last_activity = (
                    self._parse_dt(row["last_activity_at"])
                    or self._parse_dt(row["updated_at"])
                    or self._parse_dt(row["created_at"])
                    or datetime.now()
                )
                inactive_seconds = (datetime.now() - last_activity).total_seconds()
                stale_limit = (
                    EXECUTING_STALE_SECONDS if task.status == "executing"
                    else PLANNING_STALE_SECONDS
                )
                if inactive_seconds >= stale_limit:
                    await self._mark_task_stale(task, int(inactive_seconds))
        finally:
            await db.close()

    async def _mark_task_stale(self, task, inactive_seconds: int):
        chinese = is_chinese(getattr(task, "question", "") or "")
        reason = (
            f"任务已连续 {inactive_seconds}s 无新进展，疑似卡死。"
            if chinese
            else f"The task has had no progress for {inactive_seconds}s and appears stalled."
        )
        logger.warning(f"Watchdog: task {task.id} stale, status={task.status}, inactive={inactive_seconds}s")

        for node in task.nodes:
            if node.status == "running":
                try:
                    await self.task_store.update_node_status(
                        node.id, "error", f"[STALE] {reason}"
                    )
                except Exception as e:
                    logger.warning(f"Watchdog: mark node stale failed for {node.id}: {e}")

        if task.status == "executing" and task.plan_steps:
            final_reason = (
                reason + " 已自动终止，原方案已保留，可沿用原方案重新执行。"
                if chinese
                else reason + " It was automatically terminated. The original plan was preserved and can be rerun."
            )
            await self.task_store.set_task_failure(task.id, "terminated", final_reason)
        else:
            final_reason = (
                reason + " 已自动标记失败，请重新发起或重新规划。"
                if chinese
                else reason + " It was automatically marked as failed. Please start again or replan."
            )
            await self.task_store.set_task_failure(task.id, "failed", final_reason)

    async def _finalize_task(self, task_id: str, question: str, has_blockers: bool):
        """
        所有子任务结束后，由 Leader 汇总 findings，再由 CEO 综合输出最终答案，
        并将答案写入根节点 output，更新任务终态。

        幂等：同一 task 可能被后台编排流 & monitor_task 同时判定为"完成"，
        我们通过内存锁 + 状态校验来去重。
        """
        async with self._finalize_lock:
            if task_id in self._finalizing:
                logger.debug(f"_finalize_task[{task_id}] 已在进行中，跳过重复调用")
                return
            # 读一次状态避免重复 finalize 已完成的任务
            task_pre = await self.task_store.get(task_id)
            if task_pre and task_pre.status in (
                "completed", "completed_with_blockers", "timeout_killed", "terminated",
            ):
                logger.debug(
                    f"_finalize_task[{task_id}] 任务已是终态 {task_pre.status}，跳过"
                )
                return
            self._finalizing.add(task_id)

        try:
            findings_str = await studio_leader.compile_findings(task_id)

            if has_blockers:
                blocker_prefix = "Note: some sub-tasks were blocked. The following summary covers the completed parts.\n\n"
                findings_str = blocker_prefix + findings_str

            synthesis_result = await agent_zero.synthesize_results(question, findings_str)
            if self._looks_incomplete_summary(synthesis_result):
                logger.warning(
                    f"Task {task_id} synthesis looks incomplete, retrying once "
                    f"(len={len((synthesis_result or '').strip())})"
                )
                retry_result = await agent_zero.synthesize_results(
                    question,
                    findings_str,
                    extra_instruction=(
                        "The previous final conclusion appears incomplete. Regenerate a complete but concise final summary. "
                        "It must cover final deliverables, main conclusions, and concrete next actions for the user. "
                        "If artifacts were written to sandbox files, explicitly mention their file paths. "
                        "Strictly follow the Response Language Policy."
                    ),
                )
                if not self._looks_incomplete_summary(retry_result):
                    synthesis_result = retry_result
                else:
                    logger.warning(
                        f"Task {task_id} synthesis retry still incomplete, using deterministic fallback "
                        f"(len={len((retry_result or '').strip())})"
                    )
                    task_for_fallback = await self.task_store.get(task_id)
                    synthesis_result = await self._build_fallback_summary(task_for_fallback, has_blockers)

            # 将最终答案写入当前 iteration 的根节点（CEO 节点）的 output
            task = await self.task_store.get(task_id)
            if task and task.nodes:
                root_node_id = await self.task_store.get_current_iteration_root_node_id(task_id)
                if not root_node_id:
                    root_node_id = task.nodes[0].id
                await self.task_store.update_node_status(
                    root_node_id, "completed", synthesis_result
                )

            final_status = "completed_with_blockers" if has_blockers else "completed"
            await self.task_store.update_task_status(task_id, final_status)
            await self.task_store.set_status_message(
                task_id,
                enc("backendTaskStatus.task_summary_done")
                if not has_blockers
                else enc("backendTaskStatus.task_summary_done_with_blockers"),
            )

            logger.info(f"Task {task_id} finalized with status={final_status}")

            execution_mode = os.environ.get("ASTUDIO_EXECUTION_MODE") or os.environ.get("ANTIT_EXECUTION_MODE")
            if execution_mode == "worker":
                await self.consolidate_memory(task_id)
            else:
                asyncio.create_task(self.consolidate_memory(task_id))
        finally:
            self._finalizing.discard(task_id)

    @staticmethod
    def _looks_incomplete_summary(text: str | None) -> bool:
        value = (text or "").strip()
        if len(value) < SUMMARY_MIN_CHARS:
            return True
        if value.count("```") % 2 != 0:
            return True
        last = value[-1]
        if last in SUMMARY_BAD_ENDINGS:
            return True
        if len(value) < SUMMARY_STRONG_MIN_CHARS and last not in SUMMARY_VALID_ENDINGS:
            return True
        return False

    async def _build_fallback_summary(self, task, has_blockers: bool) -> str:
        if not task:
            return "The task has ended, but final synthesis failed. Check the step directory and output directory for deliverables."

        chinese = is_chinese(getattr(task, "question", "") or "")

        current_iteration_id = getattr(task, "current_iteration_id", None)
        sub_tasks = [
            st for st in getattr(task, "sub_tasks", [])
            if not current_iteration_id or getattr(st, "iteration_id", None) == current_iteration_id
        ]
        accepted = [st for st in sub_tasks if st.status == "accepted"]
        blocked = [st for st in sub_tasks if st.status == "blocked"]
        final_step = next(
            (
                st for st in reversed(accepted)
                if any(
                    token in (st.step_label or "").lower()
                    for token in ("final", "deliver", "synthesis", "integrat", "最终", "交付", "汇总", "整合")
                )
            ),
            accepted[-1] if accepted else None,
        )
        final_text = (getattr(final_step, "deliverable", "") or "").strip() if final_step else ""
        if len(final_text) > 1200:
            final_text = final_text[:1200].rstrip() + "…"

        output_files = await self._list_output_files(task.id)
        if chinese:
            lines = [
                "# 任务已完成",
                "",
                "最终汇总触发了完整性保护，系统已根据可验证的任务产物生成这份结果笔记。",
                "",
                "## 交付状态",
                f"- 状态：{'部分完成，存在阻塞' if has_blockers else '已完成'}",
                f"- 已验收步骤：{len(accepted)}",
            ]
        else:
            lines = [
                "# Task Completed",
                "",
                "Completeness protection was triggered during final synthesis, so the system generated this result note from verifiable task artifacts.",
                "",
                "## Delivery Status",
                f"- Status: {'partially completed with blockers' if has_blockers else 'completed'}",
                f"- Accepted steps: {len(accepted)}",
            ]
        if blocked:
            lines.append(f"- {'阻塞步骤' if chinese else 'Blocked steps'}: {len(blocked)}")
        if output_files:
            lines.extend(["", "## 输出文件" if chinese else "## Output Files"])
            lines.extend(f"- `{path}`" for path in output_files)
        if final_step:
            lines.extend([
                "",
                "## 最终交付摘要" if chinese else "## Final Deliverable Summary",
                f"- {'来源步骤' if chinese else 'Source step'}: {final_step.step_label} / {final_step.assign_to_role}",
                "",
                final_text or (
                    "该步骤已完成，但没有写入文本摘要。请查看输出目录中的交付文件。"
                    if chinese
                    else "This step completed but did not write a text summary. Check deliverable files in the output directory."
                ),
            ])
        else:
            lines.extend([
                "",
                "## 后续查看" if chinese else "## Further Reading",
                "请查看步骤目录和输出目录中的交付内容。" if chinese else "Check deliverables in the step directory and output directory.",
            ])
        return "\n".join(lines).strip()

    async def _list_output_files(self, task_id: str) -> list[str]:
        try:
            sandbox = await self.sandbox_store.get_by_task(task_id)
            if not sandbox:
                return []
            files = self.sandbox_store.list_files(sandbox, "output")
            return [
                file.path for file in files
                if getattr(file, "kind", "") == "file"
            ][:12]
        except Exception:
            return []

    async def consolidate_memory(self, task_id: str):
        """
        任务结束后，将经验写回两层记忆：
          1. 每个参与员工的 soul.md（个人经验）
          2. 工作室名片（CEO 路由用的能力标签 + 话题记录）
        幂等保护：同一 task 仅执行一次（避免 finalize/monitor 重复触发）。
        """
        if task_id in self._consolidated:
            logger.debug(f"consolidate_memory[{task_id}] 已执行过，跳过")
            return
        self._consolidated.add(task_id)
        logger.info(f"Memory consolidation start for task {task_id}...")
        try:
            task = await self.task_store.get(task_id)
            if not task:
                return

            studio_id = task.studio_id
            if not studio_id:
                return

            studio = await self.studio_store.get(studio_id)
            if not studio:
                return

            # ── 层一：员工 soul 写回 ──────────────────────────────────
            # 以 assign_to_role 为 key 聚合本次任务中每个员工的子任务摘要
            role_to_summaries: dict[str, list[str]] = {}
            step_labels: list[str] = []

            for st in task.sub_tasks:
                if st.status != "accepted" or not st.deliverable:
                    continue
                role = st.assign_to_role
                # 截断过长的 deliverable，避免 soul_update prompt 超长
                summary_text = st.deliverable[:800] if len(st.deliverable) > 800 else st.deliverable
                role_to_summaries.setdefault(role, []).append(
                    f"[{st.step_label}] {summary_text}"
                )
                step_labels.append(st.step_label)

            # 澄清上下文（若有）统一附加到每位员工的经验摘要末尾
            clarification_answers = task.clarification_answers or {}
            clarification_ctx = ""
            if clarification_answers:
                qa_lines = "\n".join(
                    f"  Q: {q}\n  A: {a}" for q, a in clarification_answers.items()
                )
                clarification_ctx = f"\n\n[Key supplemental information from the user for this task]\n{qa_lines}"

            for sa in studio.sub_agents:
                summaries = role_to_summaries.get(sa.role)
                if not summaries:
                    continue

                combined_summary = "\n".join(summaries) + clarification_ctx
                step_label_for_prompt = " / ".join(
                    [st.step_label for st in task.sub_tasks if st.assign_to_role == sa.role][:3]
                )

                # 用 LLM 生成一条经验记录
                try:
                    system_prompt = ContextBuilder.build_soul_update(
                        agent_role=sa.role,
                        step_label=step_label_for_prompt or "General Task",
                        distilled_summary=combined_summary,
                    )
                    new_experience = await llm_service.chat(
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": "Extract an experience note."},
                        ],
                        role="distillation",
                        stream=False,
                        temperature=0.1,
                    )
                    new_experience = str(new_experience).strip()
                except Exception as e:
                    logger.warning(f"Soul update LLM call failed for {sa.role}: {e}")
                    new_experience = f"[{step_label_for_prompt}] {combined_summary[:200]}"

                # 读当前 soul
                current_soul = sa.soul or ""

                if len(current_soul) + len(new_experience) > SOUL_COMPRESS_THRESHOLD:
                    # 触发压缩合并
                    try:
                        compress_prompt = ContextBuilder.build_soul_compress(
                            agent_role=sa.role,
                            existing_soul=current_soul,
                            new_experience=new_experience,
                            max_chars=SOUL_MAX_CHARS,
                        )
                        compressed = await llm_service.chat(
                            messages=[
                                {"role": "system", "content": compress_prompt},
                                {"role": "user", "content": "Compress and consolidate the memory."},
                            ],
                            role="distillation",
                            stream=False,
                            temperature=0.1,
                        )
                        new_soul_content = str(compressed).strip()
                    except Exception as e:
                        logger.warning(f"Soul compress failed for {sa.role}: {e}")
                        # 压缩失败时简单追加
                        new_soul_content = current_soul + f"\n\n{new_experience}"
                else:
                    # 直接追加
                    new_soul_content = (current_soul.rstrip() + f"\n\n{new_experience}").strip()

                await self.studio_store.update_agent_soul(sa.id, new_soul_content)
                logger.info(f"Soul updated for agent {sa.role} ({sa.id})")

            # ── 层二：Studio card 写回 ────────────────────────────────
            # recent_topics：优先使用澄清后的完整意图，否则退回原始问题
            clarification_answers = task.clarification_answers or {}
            if clarification_answers:
                qa_text = " / ".join(
                    f"{q}: {a}" for q, a in list(clarification_answers.items())[:3]
                )
                topic = f"{task.question[:40]} [{qa_text}]"[:80].strip()
            else:
                topic = task.question[:60].strip()
            new_topics = [topic] if topic else []

            new_caps = list(dict.fromkeys(step_labels))[:10]

            # ── 层三：从澄清问答中提取可复用的用户关键事实 ────────────
            new_facts: list[str] = []
            if clarification_answers:
                try:
                    qa_lines = "\n".join(
                        f"Q: {q}\nA: {a}" for q, a in clarification_answers.items()
                    )
                    fact_prompt = ContextBuilder.build_fact_extract(
                        task_question=task.question,
                        clarification_qa=qa_lines,
                    )
                    fact_result = await llm_service.chat(
                        messages=[
                            {"role": "system", "content": fact_prompt},
                            {"role": "user", "content": "Extract key facts."},
                        ],
                        role="distillation",
                        stream=False,
                        temperature=0.1,
                    )
                    fact_text = str(fact_result).strip()
                    if fact_text and fact_text not in {"无", "None"}:
                        new_facts = [
                            line.strip() for line in fact_text.splitlines()
                            if line.strip() and line.strip() not in {"无", "None"}
                        ]
                    logger.info(f"Extracted {len(new_facts)} user facts from clarification")
                except Exception as e:
                    logger.warning(f"Fact extraction failed: {e}")

            await self.studio_store.update_studio_card(studio_id, new_topics, new_caps, new_facts or None)
            logger.info(f"Studio card updated for {studio_id}")

        except Exception as e:
            logger.error(f"consolidate_memory failed for task {task_id}: {e}")

        logger.info(f"Memory consolidation completed for task {task_id}.")


task_monitor = TaskMonitor()
