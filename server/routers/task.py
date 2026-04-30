import asyncio
import json
import uuid
from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from loguru import logger
from sse_starlette.sse import EventSourceResponse

from agents.agent_zero import agent_zero
from agents.studio_leader_executor import studio_leader
from agents.sub_agent import sub_agent_executor
from core.task_process_runner import should_run_inline, start_task_worker, terminate_task_worker
from core.tasks_monitor import task_monitor
from i18n.status_message_codec import encode_task_status_msg as enc
from models.canvas import PathEdge, PathNode
from models.studio import StudioCreate, SubAgentConfigCreate
from models.task import AskRequest, SubTask, Task
from services.attachments import AttachmentError, build_attachment_prompt, save_task_attachments
from services.llm_service import llm_service
from services.pricing import estimate_cost_usd
from storage.database import get_db
from storage.sandbox_store import SandboxStore
from storage.studio_store import StudioStore
from storage.task_store import TaskStore

MAX_RECRUIT_RETRIES = 3
MAX_REVIEW_RETRIES = 2
OUTPUT_NODE_PREFIX = "__output__:"

_cancel_registry: dict[str, asyncio.Event] = {}
_running_tasks: dict[str, set[asyncio.Task]] = defaultdict(set)


def _register_running(task_id: str, task: asyncio.Task) -> None:
    _running_tasks[task_id].add(task)
    task.add_done_callback(lambda t: _running_tasks.get(task_id, set()).discard(t))


def _cancel_all_running(task_id: str) -> int:
    tasks = list(_running_tasks.get(task_id, set()))
    for t in tasks:
        if not t.done():
            t.cancel()
    return len(tasks)

_task_exec_locks: dict[str, asyncio.Lock] = {}
_task_locks_guard = asyncio.Lock()

async def _get_task_lock(task_id: str) -> asyncio.Lock:
    async with _task_locks_guard:
        lock = _task_exec_locks.get(task_id)
        if lock is None:
            lock = asyncio.Lock()
            _task_exec_locks[task_id] = lock
        return lock


_agent_working_count: dict[str, int] = defaultdict(int)
_agent_working_lock = asyncio.Lock()

async def _mark_agent_working(agent_id: str) -> None:
    async with _agent_working_lock:
        prev = _agent_working_count[agent_id]
        _agent_working_count[agent_id] = prev + 1
        if prev != 0:
            return
    db = await get_db()
    try:
        await db.execute(
            "UPDATE sub_agent_configs SET is_working=1 WHERE id=?", (agent_id,)
        )
        await db.commit()
    finally:
        await db.close()

async def _mark_agent_idle(agent_id: str) -> None:
    async with _agent_working_lock:
        cur = _agent_working_count[agent_id] - 1
        if cur < 0:
            cur = 0
        _agent_working_count[agent_id] = cur
        should_write = cur == 0
        if cur == 0:
            _agent_working_count.pop(agent_id, None)
    if not should_write:
        return
    db = await get_db()
    try:
        await db.execute(
            "UPDATE sub_agent_configs SET is_working=0 WHERE id=?", (agent_id,)
        )
        await db.commit()
    finally:
        await db.close()


def _json_serial(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def _iteration_x_offset(task: Task | None) -> int:
    if not task or not task.current_iteration_id:
        return 0
    idx = next(
        (i for i, iteration in enumerate(task.iterations) if iteration.id == task.current_iteration_id),
        0,
    )
    return idx * 560


router = APIRouter()
task_store = TaskStore()
studio_store = StudioStore()
sandbox_store = SandboxStore()


@router.get("/")
async def list_tasks():
    return await task_store.list_all()


@router.get("/{task_id}")
async def get_task(task_id: str) -> Task:
    task = await task_store.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.delete("/{task_id}", status_code=204)
async def delete_task(task_id: str):
    ok = await task_store.delete(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="任务不存在")


@router.post("/ask")
async def ask_question(req: AskRequest):
    task = await task_store.create(question=req.question)
    await _schedule_ask_pipeline(task.id, req.question)
    return {"task_id": task.id, "status": "planning"}


@router.post("/ask-with-attachments")
async def ask_question_with_attachments(
    question: str = Form(""),
    files: list[UploadFile] | None = File(default=None),
):
    task_question = question.strip() or "Analyze the attachments I uploaded, then provide conclusions and recommendations."
    task = await task_store.create(question=task_question)
    try:
        attachments = await save_task_attachments(task.id, files or [])
    except AttachmentError as e:
        await task_store.delete(task.id)
        raise HTTPException(status_code=400, detail=str(e)) from e

    pipeline_question = task_question + build_attachment_prompt(task.id)
    await _schedule_ask_pipeline(task.id, pipeline_question)
    return {"task_id": task.id, "status": "planning", "attachments": attachments}


async def _schedule_ask_pipeline(task_id: str, question: str, preferred_studio_id: str | None = None) -> None:
    if not should_run_inline():
        try:
            await start_task_worker(
                "ask",
                task_id,
                {
                    "task_id": task_id,
                    "question": question,
                    "preferred_studio_id": preferred_studio_id,
                },
            )
            return
        except Exception as e:
            logger.exception(f"[/ask] task={task_id} 启动隔离执行进程失败: {e}")
            await task_store.set_task_failure(task_id, "failed", f"启动隔离执行进程失败：{e}")
            raise HTTPException(status_code=500, detail="启动隔离执行进程失败") from e

    async def runner():
        try:
            await _run_ask_pipeline(task_id, question, preferred_studio_id=preferred_studio_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"[/ask] task={task_id} 后台编排失败: {e}")
            try:
                cur = await task_store.get(task_id)
                if cur and cur.status not in (
                    "completed", "failed", "terminated", "timeout_killed",
                    "need_clarification", "await_leader_plan_approval",
                ):
                    await task_store.update_task_status(task_id, "failed")
                    await task_store.set_status_message(task_id, enc("backendTaskStatus.task_failed_detail", detail=str(e)[:480]))
            except Exception as inner:
                logger.warning(f"[/ask] 无法标记失败 {task_id}: {inner}")

    asyncio.create_task(runner())


async def _run_ask_pipeline(task_id: str, question: str, preferred_studio_id: str | None = None) -> None:
    """原 /ask SSE 中的逻辑；进度写入 status / status_message，由 GET /tasks/{id}/stream 轮询推送。"""
    studio_info: dict = {"id": "", "scenario": ""}

    route_res: dict | None = None
    action = ""

    if preferred_studio_id and agent_zero._is_high_confidence_system_management_task(question):
        logger.info(
            f"[/ask] task={task_id} 检测到系统/定时任务意图，忽略 preferred_studio={preferred_studio_id}"
        )
        preferred_studio_id = None

    if preferred_studio_id and preferred_studio_id != "studio_0":
        preferred_studio = await studio_store.get(preferred_studio_id)
        if preferred_studio:
            studio_info["id"] = preferred_studio_id
            studio_info["scenario"] = preferred_studio.scenario or ""
            route_res = {
                "action": "route",
                "studio_id": preferred_studio_id,
                "studio_scenario": studio_info["scenario"],
            }
            action = "route"
            await task_store.set_status_message(
                task_id,
                enc(
                    "backendTaskStatus.reuse_studio_leader_plan",
                    name=studio_info["scenario"] or preferred_studio_id,
                ),
            )
            logger.info(f"[/ask] task={task_id} 沿用工作室 studio={preferred_studio_id}")
        else:
            logger.warning(f"[/ask] task={task_id} 指定工作室不存在，回退到常规路由: {preferred_studio_id}")

    if route_res is None:
        await task_store.set_status_message(task_id, enc("backendTaskStatus.agent_zero_evaluating"))

        logger.info(f"[/ask] task={task_id} 开始路由：{question[:60]}")
        try:
            route_res = await agent_zero.route_task(question)
        except Exception as route_err:
            logger.exception(f"[/ask] task={task_id} 路由失败: {route_err}")
            await task_store.set_status_message(task_id, enc("backendTaskStatus.route_failed_detail", detail=str(route_err)[:400]))
            await task_store.update_task_status(task_id, "failed")
            return

        action = route_res.get("action")
        logger.info(f"[/ask] task={task_id} 路由结果 action={action}")

    if action == "solve":
        task_snap = await task_store.get(task_id)
        x_offset = _iteration_x_offset(task_snap)
        studio_info["id"] = "studio_0"
        studio_info["scenario"] = route_res.get("studio_scenario") or "Studio 0"
        await task_store.set_status_message(task_id, enc("backendTaskStatus.agent_zero_direct"))
        answer = route_res.get("answer", "Direct answer completed.")
        node_id = str(uuid.uuid4())[:8]
        root_node = PathNode(
            id=node_id, type="agent_zero", agent_role="Agent Zero",
            step_label="Direct Answer", input=question, output=answer,
            status="completed", position={"x": 400 + x_offset, "y": 50}
        )
        await task_store.add_node(task_id, root_node)
        await task_store.link_current_iteration_root(task_id, root_node.id)
        db = await get_db()
        try:
            await db.execute(
                "UPDATE tasks SET studio_id='studio_0' WHERE id=?", (task_id,)
            )
            await db.commit()
        finally:
            await db.close()
        await task_store.update_task_status(task_id, "completed")
        await task_store.set_status_message(task_id, enc("backendTaskStatus.answer_done"))
        return

    if action == "create_studio":
        await task_store.set_status_message(task_id, enc("backendTaskStatus.creating_new_studio"))
        new_scenario = route_res.get("studio_name", "Dynamic Incubation Studio")
        leader_role = route_res.get("leader_role", "Lead")
        new_studio = await studio_store.create(StudioCreate(
            scenario=new_scenario,
            description=f"Category: {route_res.get('category', 'General')}",
            sub_agents=[SubAgentConfigCreate(role=leader_role, agent_md=f"# {leader_role}")]
        ))
        studio_id = new_studio.id
        studio_info["scenario"] = new_studio.scenario or new_scenario
    else:
        studio_id = route_res.get("studio_id", "studio_0")
        studio_info["scenario"] = route_res.get("studio_scenario") or ""

    studio_info["id"] = studio_id
    await task_store.set_status_message(
        task_id,
        enc("backendTaskStatus.handed_to_studio_leader", name=studio_info["scenario"] or studio_id),
    )

    logger.info(f"[/ask] task={task_id} 开始 Leader 规划，studio={studio_id}")
    plan_data = await _run_leader_planning(task_id, studio_id, question)

    if plan_data.get("action") == "need_clarification":
        questions = plan_data.get("questions", [])
        await task_store.save_clarification(task_id, studio_id, questions)
        await task_store.set_status_message(task_id, enc("backendTaskStatus.leader_need_clarification"))
        await task_store.update_task_status(task_id, "need_clarification")
        return

    steps = plan_data.get("steps", [])
    _validate_dag(steps)

    plan_details = "\n".join([
        f"- [{s.get('id')}] [{s.get('assign_to_role')}] {s.get('step_label')}"
        + (f" (depends_on: {s.get('depends_on')})" if s.get("depends_on") else "")
        for s in steps
    ])
    plan_str = f"Leader execution plan:\n{plan_details}\n\nReview and approve to start, or add revision feedback."

    task_snap = await task_store.get(task_id)
    x_offset = _iteration_x_offset(task_snap)
    node_id = str(uuid.uuid4())[:8]
    root_node = PathNode(
        id=node_id, type="agent_zero", agent_role="CEO",
        step_label="Plan Review" if not task_snap or len(task_snap.iterations) <= 1 else f"{task_snap.iterations[-1].title or 'Continue Iteration'}: Plan Approval",
        input=question, output=plan_str,
        status="pending", position={"x": 400 + x_offset, "y": 50}
    )
    await task_store.add_node(task_id, root_node)
    await task_store.link_current_iteration_root(task_id, root_node.id)
    await task_store.save_plan_steps(task_id, studio_id, steps)
    await task_store.set_status_message(task_id, enc("backendTaskStatus.await_plan_review"))
    await task_store.update_task_status(task_id, "await_leader_plan_approval")


# ──────────────────────────────────────────────
# 内部：Leader 规划（含招聘循环上限）
# ──────────────────────────────────────────────
async def _run_leader_planning(task_id: str, studio_id: str, goal: str) -> dict:
    from storage.database import get_db
    recruit_count = 0
    while True:
        plan_res = await studio_leader.plan_sub_tasks(task_id, studio_id, goal)
        if plan_res.get("action") == "recruit_employee" and recruit_count < MAX_RECRUIT_RETRIES:
            role_needed = plan_res.get("employee_role", "Unnamed Specialist")
            # HR 专员决定该角色所需技能
            skills_needed = await _hr_decide_skills(role_needed)
            emp_id = str(uuid.uuid4())[:8]
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO sub_agent_configs (id, studio_id, role, skills) VALUES (?, ?, ?, ?)",
                    (emp_id, studio_id, role_needed, json.dumps(skills_needed, ensure_ascii=False))
                )
                await db.commit()
            finally:
                await db.close()
            logger.info(f"HR 为 [{role_needed}] 分配技能: {skills_needed}")
            recruit_count += 1
        else:
            return plan_res


async def _hr_decide_skills(role: str) -> list[str]:
    """调用 HR 专员的 LLM，基于当前 Skill 池为指定角色分配技能。

    白名单与提示词都来自 `tools.registry`，跟着 Skill 池动态走 —— 用户在 UI 上
    启用 / 停用 / 新增的 slug 都会被感知，不再硬编码。
    """
    from agents.context import ContextBuilder
    from services.llm_service import llm_service
    from tools.registry import describe_available_skills, list_available_slugs

    available = await describe_available_skills()
    if not available:
        logger.warning("Skill 池当前为空，HR 招聘退化为无 skill 员工")
        return []

    # 动态渲染 Markdown 表（| slug | name | description |）注入到 HR 提示里
    table_lines = ["| slug | Name | Description |", "|---|---|---|"]
    for item in available:
        desc = (item.get("description") or "").replace("|", "/").replace("\n", " ")
        table_lines.append(f"| `{item['slug']}` | {item.get('name') or item['slug']} | {desc} |")
    hr_prompt = ContextBuilder.build_hr_agent(available_skills="\n".join(table_lines))

    valid_slugs = set(await list_available_slugs())
    fallback = [
        slug for slug in ("web_search", "browser_search")
        if slug in valid_slugs
    ] or list(valid_slugs)[:1]

    try:
        response = await llm_service.chat(
            messages=[
                {"role": "system", "content": hr_prompt},
                {"role": "user", "content": f"Assign skills for the following role: {role}"},
            ],
            role="agent_zero",   # 使用 agent_zero 级别的 LLM
            stream=False,
            temperature=0.0,
        )
        text = str(response).strip()
        if text.startswith("```json"):
            text = text[7:-3].strip()
        elif text.startswith("```"):
            text = text[3:-3].strip()
        result = json.loads(text)
        raw_skills = result.get("skills", []) or []
        skills = [s for s in raw_skills if s in valid_slugs]
        if "web_search" in skills and "browser_search" in valid_slugs and "browser_search" not in skills:
            skills.append("browser_search")
        if not skills:
            logger.warning(
                f"HR 返回的 skill 全部落空 (raw={raw_skills})，退化为默认: {fallback}"
            )
            return fallback
        return skills
    except Exception as e:
        logger.warning(f"HR 技能判断失败: {e}，使用默认技能 {fallback}")
        return fallback


# ──────────────────────────────────────────────
# 内部：DAG 校验（修正无效引用并使用 Kahn 算法打破环）
# ──────────────────────────────────────────────
def _validate_dag(steps: list) -> None:
    """就地修正 steps：补缺省 id/depends_on；去除对不存在步骤或自身的依赖；
    若存在环，基于 Kahn 算法找出环中节点并丢弃导致环的反向边。"""
    if not steps:
        return

    # 1) 补缺省字段 & 去重 depends_on
    for step in steps:
        step.setdefault("id", str(uuid.uuid4())[:6])
        step.setdefault("depends_on", [])
        if not isinstance(step["depends_on"], list):
            step["depends_on"] = []

    valid_ids = {s["id"] for s in steps}
    for step in steps:
        step["depends_on"] = list(dict.fromkeys(
            d for d in step["depends_on"]
            if isinstance(d, str) and d in valid_ids and d != step["id"]
        ))

    # 2) Kahn：反复扫描，每轮把入度为 0 的节点移除；扫完后仍有节点未移除即存在环
    remaining = {s["id"]: set(s["depends_on"]) for s in steps}
    order: list[str] = []
    while True:
        ready = [sid for sid, deps in remaining.items() if not deps]
        if not ready:
            break
        for sid in ready:
            order.append(sid)
            remaining.pop(sid, None)
        for deps in remaining.values():
            deps.difference_update(ready)

    if remaining:
        # 环：把这些节点的依赖裁成"只保留已入拓扑序的前驱"
        in_topo = set(order)
        for sid, step in ((s["id"], s) for s in steps):
            if sid in remaining:
                dropped = [d for d in step["depends_on"] if d not in in_topo]
                if dropped:
                    logger.warning(
                        f"[_validate_dag] 检测到环，裁剪节点 {sid} 的反向依赖: {dropped}"
                    )
                step["depends_on"] = [d for d in step["depends_on"] if d in in_topo]


# ──────────────────────────────────────────────
# 内部：计算 DAG 拓扑层级（用于画布布局）
# ──────────────────────────────────────────────
def _compute_dag_levels(steps: list) -> dict[str, int]:
    """BFS 方式计算层级；假定 _validate_dag 已运行，无环。"""
    id_to_step = {s["id"]: s for s in steps}
    levels: dict[str, int] = {}
    MAX_DEPTH = 64  # 防御性兜底，防止意外的环导致递归爆栈

    def get_level(step_id: str, stack: frozenset[str] = frozenset()) -> int:
        if step_id in levels:
            return levels[step_id]
        if step_id in stack or len(stack) > MAX_DEPTH:
            levels[step_id] = 0
            return 0
        deps = id_to_step.get(step_id, {}).get("depends_on", []) or []
        new_stack = stack | {step_id}
        level = (max((get_level(d, new_stack) for d in deps), default=-1) + 1) if deps else 0
        levels[step_id] = level
        return level

    for s in steps:
        get_level(s["id"])
    return levels


def _compute_node_positions(steps: list, levels: dict[str, int]) -> dict[str, dict]:
    level_to_steps: dict[int, list] = defaultdict(list)
    for s in steps:
        level_to_steps[levels.get(s["id"], 0)].append(s["id"])

    positions: dict[str, dict] = {}
    for level, step_ids in level_to_steps.items():
        y = 200 + level * 220
        n = len(step_ids)
        for i, step_id in enumerate(step_ids):
            x = 400 + (i - (n - 1) / 2) * 320
            positions[step_id] = {"x": x, "y": y}
    return positions


# ──────────────────────────────────────────────
# POST /tasks/{task_id}/clarify — 用户提交澄清答案后继续规划（SSE）
# ──────────────────────────────────────────────
@router.post("/{task_id}/clarify")
async def clarify_task(task_id: str, request: Request, payload: dict):
    task = await task_store.get(task_id)
    if not task:
        raise HTTPException(404)
    answers: dict = payload.get("answers", {})  # {question_id: answer_text}
    studio_id = task.plan_studio_id or task.studio_id or "studio_0"

    await task_store.save_clarification_answers(task_id, answers)
    await task_store.update_task_status(task_id, "planning")

    async def event_generator():
        # 尝试查一次工作室名；失败不影响主流程（避免 DB 池紧张时卡死）
        studio_scenario = ""
        try:
            _s = await asyncio.wait_for(studio_store.get(studio_id), timeout=3.0)
            studio_scenario = (_s.scenario if _s else "")
        except Exception as e:
            logger.warning(f"[/clarify] task={task_id} 查询工作室名失败（忽略）: {e}")

        def _status(status_str, message):
            payload = {
                "status": status_str,
                "message": message,
                "task_id": task_id,
                "studio_id": studio_id,
            }
            if studio_scenario:
                payload["studio_scenario"] = studio_scenario
            return {"event": "status", "data": json.dumps(payload)}

        # 将原始问题 + 用户的澄清答案合并交给 Leader 重新规划
        clarification_context = "\n".join([
            f"Q: {q.get('question', '')}\nA: {answers.get(q.get('id', ''), '(Not answered)')}"
            for q in task.clarification_questions
        ])
        goal_with_answers = (
            f"{task.question}\n\n"
            "---\n"
            "## User Clarifications (re-plan based on these answers)\n\n"
            f"{clarification_context}"
        )

        yield _status("planning", enc("backendTaskStatus.clarify_received_replanning"))
        plan_data = await _run_leader_planning(task_id, studio_id, goal_with_answers)
        steps = plan_data.get("steps", [])
        _validate_dag(steps)

        plan_details = "\n".join([
            f"- [{s.get('id')}] [{s.get('assign_to_role')}] {s.get('step_label')}"
            + (f" (depends_on: {s.get('depends_on')})" if s.get('depends_on') else "")
            for s in steps
        ])
        plan_str = (
            f"Leader execution plan (including user clarifications):\n"
            f"{plan_details}\n\nReview and approve to start, or add revision feedback."
        )
        x_offset = _iteration_x_offset(await task_store.get(task_id))
        node_id = str(uuid.uuid4())[:8]
        root_node = PathNode(
            id=node_id, type="agent_zero", agent_role="CEO",
            step_label="Plan Review (Clarifications Included)", input=task.question, output=plan_str,
            status="pending", position={"x": 400 + x_offset, "y": 50}
        )
        await task_store.add_node(task_id, root_node)
        await task_store.link_current_iteration_root(task_id, root_node.id)
        await task_store.save_plan_steps(task_id, studio_id, steps)
        await task_store.set_status_message(task_id, enc("backendTaskStatus.await_plan_review"))
        await task_store.update_task_status(task_id, "await_leader_plan_approval")
        yield {"event": "node_added", "data": json.dumps(root_node.model_dump(), default=_json_serial)}
        yield _status("await_leader_plan_approval", enc("backendTaskStatus.await_plan_review"))
        yield {"event": "done_pause", "data": json.dumps({
            "action": "review_plan",
            "studio_id": studio_id,
            "studio_scenario": studio_scenario,
            "steps": steps,
        })}

    return EventSourceResponse(event_generator())


# ──────────────────────────────────────────────
# POST /tasks/{task_id}/rerun-original — 沿用已保存的原方案重新执行（终止后）
# ──────────────────────────────────────────────
@router.post("/{task_id}/rerun-original")
async def rerun_original(task_id: str):
    """
    仅用于 terminated 任务：清除上次执行残留，用 plan_steps 直接重新执行，
    无需重新规划或审批。
    """
    task = await task_store.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task.status != "terminated":
        raise HTTPException(400, f"只有已终止的任务才能沿用原方案重新执行（当前状态：{task.status}）")
    if not task.plan_steps:
        raise HTTPException(400, "任务没有保存的方案步骤，请重新规划")

    studio_id = task.plan_studio_id or task.studio_id or "studio_0"
    steps = task.plan_steps

    await task_store.begin_iteration(
        task_id,
        instruction="Rerun using the previously saved plan",
        title="Rerun Original Plan",
        source_node_id=task.nodes[-1].id if task.nodes else None,
    )
    await task_store.save_plan_steps(task_id, studio_id, steps)
    root_node = PathNode(
        id=str(uuid.uuid4())[:8],
        type="agent_zero",
        agent_role="CEO",
        step_label="Rerun Original Plan",
        input=task.question,
        output="Rerun using the previously saved plan.",
        status="pending",
        position={"x": 400 + _iteration_x_offset(await task_store.get(task_id)), "y": 50},
    )
    await task_store.add_node(task_id, root_node)
    await task_store.link_current_iteration_root(task_id, root_node.id)

    # 清理上次执行残留（子任务、非根节点、边）
    await task_store.clear_execution_state(task_id)
    await task_store.update_task_status(task_id, "executing")

    if should_run_inline():
        asyncio.create_task(_rerun_with_steps(task_id, studio_id, steps))
    else:
        await start_task_worker(
            "rerun",
            task_id,
            {"task_id": task_id, "studio_id": studio_id, "steps": steps},
        )
    return {"status": "ok", "message": "已开始沿用原方案重新执行，请连接 /stream 监听进度。"}


async def _rerun_with_steps(task_id: str, studio_id: str, steps: list):
    """直接用 steps 执行 DAG，不经过规划阶段。"""
    cancel_event = asyncio.Event()
    _cancel_registry[task_id] = cancel_event
    try:
        _validate_dag(steps)
        await task_store.set_status_message(task_id, enc("backendTaskStatus.rerun_saved_plan"))
        task_snap = await task_store.get(task_id)
        root_node_id = task_snap.nodes[0].id if task_snap and task_snap.nodes else None

        levels = _compute_dag_levels(steps)
        positions = _compute_node_positions(steps, levels)

        await _execute_dag(task_id, studio_id, steps, root_node_id, positions, cancel_event)

        if cancel_event.is_set():
            return

        task_snap = await task_store.get(task_id)
        if task_snap:
            current_sub_tasks = [
                st for st in task_snap.sub_tasks
                if not task_snap.current_iteration_id or st.iteration_id == task_snap.current_iteration_id
            ]
            has_blockers = any(st.status == "blocked" for st in current_sub_tasks)
            await task_monitor._finalize_task(task_id, task_snap.question, has_blockers)
    except Exception as exc:
        logger.exception(
            f"[Task {task_id}] 沿用原方案重跑异常，置 failed: {type(exc).__name__}: {exc}"
        )
        try:
            await task_store.update_task_status(task_id, "failed")
        except Exception as inner:
            logger.error(f"[Task {task_id}] 记录 failed 状态失败: {inner}")
    finally:
        _cancel_registry.pop(task_id, None)


@router.post("/{task_id}/proceed")
async def proceed_task(task_id: str, payload: dict):
    task = await task_store.get(task_id)
    if not task:
        raise HTTPException(404)

    allowed_statuses = {
        "await_leader_plan_approval",
        "need_clarification",
        "terminated",
        "completed_with_blockers",
    }
    if task.status not in allowed_statuses:
        raise HTTPException(
            status_code=409,
            detail=f"任务当前状态 '{task.status}' 不允许触发 proceed（需先终止或完成方案审查）",
        )
    if task_id in _cancel_registry:
        raise HTTPException(status_code=409, detail="任务已有执行流水线在运行，请先终止后再试")

    feedback = payload.get("feedback", "")
    route_cmd = payload.get("route_cmd", {})
    studio_id = route_cmd.get("studio_id") or task.plan_studio_id or task.studio_id or "studio_0"
    if not feedback and not route_cmd.get("steps") and task.plan_steps:
        route_cmd = {**route_cmd, "studio_id": studio_id, "steps": task.plan_steps}
    if not feedback and not route_cmd.get("steps"):
        raise HTTPException(
            status_code=409,
            detail="方案步骤为空，无法开始执行；请先重新生成方案。",
        )

    # Persist the next status before returning so the first SSE snapshot cannot
    # overwrite the UI with the previous approval state.
    new_status = "planning" if feedback else "executing"
    try:
        await task_store.update_task_status(task.id, new_status)
    except Exception as e:
        logger.exception(f"[/proceed] 无法预置状态为 {new_status}: {e}")

    if should_run_inline():
        asyncio.create_task(_execute_background_orchestration(task.id, studio_id, route_cmd, feedback))
    else:
        try:
            await start_task_worker(
                "orchestrate",
                task.id,
                {
                    "task_id": task.id,
                    "studio_id": studio_id,
                    "route_cmd": route_cmd,
                    "feedback": feedback,
                },
            )
        except Exception as e:
            logger.exception(f"[/proceed] task={task.id} 启动隔离执行进程失败: {e}")
            await task_store.set_task_failure(task.id, "failed", f"启动隔离执行进程失败：{e}")
            raise HTTPException(status_code=500, detail="启动隔离执行进程失败") from e
    return {"status": "ok", "message": "流水线已挂载后台启动。请连接 /stream 监听。"}


async def _execute_background_orchestration(task_id: str, studio_id: str, route_cmd: dict, feedback: str):
    task_lock = await _get_task_lock(task_id)
    if task_lock.locked():
        logger.warning(f"[Task {task_id}] 已有编排流水线在跑，忽略本次重复触发")
        return
    async with task_lock:
        cancel_event = asyncio.Event()
        _cancel_registry[task_id] = cancel_event

        try:
            task = await task_store.get(task_id)
            if not task:
                logger.warning(f"[Task {task_id}] 编排阶段找不到任务，提前退出")
                return
            steps = route_cmd.get("steps", [])

            if feedback:
                question_with_feedback = task.question + f"\n[User requested plan revision: {feedback}]"
                await task_store.update_task_status(task_id, "planning")
                plan_data = await _run_leader_planning(task_id, studio_id, question_with_feedback)
                steps = plan_data.get("steps", [])
                _validate_dag(steps)
                await task_store.save_plan_steps(task_id, studio_id, steps)
            elif not steps and task.plan_steps:
                steps = task.plan_steps

            _validate_dag(steps)
            await task_store.update_task_status(task_id, "executing")
            await task_store.set_status_message(task_id, enc("backendTaskStatus.leader_dispatching"))

            root_node_id = await task_store.get_current_iteration_root_node_id(task_id)
            if not root_node_id:
                nodes_rows = await task_store.get(task_id)
                root_node_id = nodes_rows.nodes[0].id if nodes_rows and nodes_rows.nodes else None

            levels = _compute_dag_levels(steps)
            positions = _compute_node_positions(steps, levels)
            x_offset = _iteration_x_offset(await task_store.get(task_id))
            if x_offset:
                positions = {
                    sid: {**pos, "x": pos.get("x", 0) + x_offset}
                    for sid, pos in positions.items()
                }

            await _execute_dag(task_id, studio_id, steps, root_node_id, positions, cancel_event)

            if cancel_event.is_set():
                logger.info(f"[Task {task_id}] 任务被用户终止")
                return

            task_snap = await task_store.get(task_id)
            if task_snap:
                current_iteration_id = task_snap.current_iteration_id
                current_sub_tasks = [
                    st for st in task_snap.sub_tasks
                    if not current_iteration_id or st.iteration_id == current_iteration_id
                ]
                has_blockers = any(st.status == "blocked" for st in current_sub_tasks)
                await task_monitor._finalize_task(task_id, task_snap.question, has_blockers)
        except Exception as exc:
            logger.exception(
                f"[Task {task_id}] 后台编排异常，置 failed: {type(exc).__name__}: {exc}"
            )
            try:
                await task_store.update_task_status(task_id, "failed")
            except Exception as inner:
                logger.error(f"[Task {task_id}] 记录 failed 状态失败: {inner}")
        finally:
            _cancel_registry.pop(task_id, None)


async def _execute_dag(
    task_id: str,
    studio_id: str,
    steps: list,
    root_node_id: str | None,
    positions: dict[str, dict],
    cancel_event: asyncio.Event | None = None,
):
    """
    每个 step 作为独立 coroutine 并发启动。
    通过 asyncio.Event 实现依赖等待：有依赖的步骤等待前置步骤的 Event 被 set 后才开始。
    cancel_event 被设置时，所有步骤将在下一个检查点停止。
    """
    step_events: dict[str, asyncio.Event] = {s["id"]: asyncio.Event() for s in steps}
    # 已完成步骤的产出（step_id → {role, step, deliverable}），带锁保护
    completed_results: dict[str, dict] = {}
    results_lock = asyncio.Lock()
    # 已阻塞的步骤 ID 集合，用于级联跳过下游步骤
    blocked_step_ids: set[str] = set()
    blocked_lock = asyncio.Lock()

    # 预先分配所有 sub_task_id 和 ui_node_id，方便提前画边
    step_ids_map: dict[str, dict] = {
        s["id"]: {
            "sub_task_id": str(uuid.uuid4())[:8],
            "ui_node_id": str(uuid.uuid4())[:8],
        }
        for s in steps
    }

    # 预先创建所有 UI 节点和边（状态为 pending，位置已按 DAG 布局）
    for step in steps:
        sid = step["id"]
        ids = step_ids_map[sid]
        pos = positions.get(sid, {"x": 400, "y": 200})
        emp_role = step.get("assign_to_role", "Expert")
        step_lab = step.get("step_label", sid)
        depends_on = step.get("depends_on", [])

        ui_node = PathNode(
            id=ids["ui_node_id"], type="sub_agent", agent_role=emp_role,
            step_label=step_lab, input=step.get("input_context", ""),
            status="pending", position=pos,
        )
        await task_store.add_node(task_id, ui_node)

        # 边：从每个依赖步骤的节点连到本节点；若无依赖则从根节点连
        sources = [step_ids_map[d]["ui_node_id"] for d in depends_on if d in step_ids_map]
        if not sources and root_node_id:
            sources = [root_node_id]
        for src in sources:
            edge_id = f"e-{src}-{ids['ui_node_id']}"
            await task_store.add_edge(task_id, PathEdge(id=edge_id, source=src, target=ids["ui_node_id"], type="main"))

    async def run_step(step: dict):
        sid = step["id"]
        ids = step_ids_map[sid]
        depends_on = step.get("depends_on", [])
        emp_role = step.get("assign_to_role", "Expert")
        original_spec = step.get("input_context", "")
        step_lab = step.get("step_label", sid)

        try:
            # 等待所有依赖步骤完成（含被阻塞的步骤，阻塞也会 set event，让后续尽快感知）
            for dep_id in depends_on:
                if dep_id in step_events:
                    await step_events[dep_id].wait()
                # 取消检查：终止信号到达，立即退出
                if cancel_event and cancel_event.is_set():
                    await task_store.update_node_status(ids["ui_node_id"], "error", "[TERMINATED] 任务已被用户终止")
                    return

            # 取消检查：依赖等待完毕后，执行前再检查一次
            if cancel_event and cancel_event.is_set():
                await task_store.update_node_status(ids["ui_node_id"], "error", "[TERMINATED] 任务已被用户终止")
                return

            # ── 级联阻塞检查：上游有任何阻塞则直接跳过本步骤 ──────────────────
            async with blocked_lock:
                upstream_blocked = [d for d in depends_on if d in blocked_step_ids]
            if upstream_blocked:
                reason = f"上游步骤「{', '.join(upstream_blocked)}」执行受阻，本步骤已跳过。"
                async with blocked_lock:
                    blocked_step_ids.add(sid)
                # 仍需创建 sub_task 记录，保持 DB 完整性
                skip_task = SubTask(
                    id=ids["sub_task_id"], task_id=task_id, studio_id=studio_id,
                    group_id=ids["ui_node_id"], step_id=sid, depends_on=depends_on,
                    step_label=step_lab, assign_to_role=emp_role, input_context=original_spec,
                    status="blocked",
                )
                await task_store.add_sub_task(skip_task)
                await task_store.update_sub_task_status(ids["sub_task_id"], "blocked", blocker_reason=reason)
                await task_store.update_node_status(ids["ui_node_id"], "error", f"[SKIPPED] {reason}")
                return  # finally 块会自动 set event

            # 按依赖关系精确注入上下文
            async with results_lock:
                dep_results = {k: v for k, v in completed_results.items() if k in depends_on}
            input_ctx = _inject_context_by_deps(original_spec, depends_on, dep_results)

            # 创建 sub_task 记录（DB 只存 original_spec，full context 仅供执行使用）
            # group_id 存储对应的 ui_node_id，供重试端点查找
            await task_store.touch_task_activity(task_id, enc("backendTaskStatus.executing_step", label=step_lab))
            sub_task = SubTask(
                id=ids["sub_task_id"], task_id=task_id, studio_id=studio_id,
                group_id=ids["ui_node_id"], step_id=sid, depends_on=depends_on,
                step_label=step_lab, assign_to_role=emp_role, input_context=original_spec,
                status="pending",
            )
            await task_store.add_sub_task(sub_task)

            # 执行（含质检循环）——包装成独立任务并注册，便于 terminate 时立即中断
            exec_coro = _run_employee_with_review(
                task_id=task_id,
                sub_task_id=ids["sub_task_id"],
                ui_node_id=ids["ui_node_id"],
                emp_role=emp_role,
                input_ctx=input_ctx,
                studio_id=studio_id,
                step_lab=step_lab,
                original_spec=original_spec,
            )
            exec_task = asyncio.create_task(exec_coro)
            _register_running(task_id, exec_task)
            try:
                result = await exec_task
            except asyncio.CancelledError:
                logger.info(f"[Task {task_id}] 子步骤 {sid} 被用户中断")
                await task_store.update_node_status(
                    ids["ui_node_id"], "error", "[TERMINATED] 子步骤已被用户中断"
                )
                return

            if result.get("status") == "accepted":
                async with results_lock:
                    completed_results[sid] = {
                        "role": emp_role,
                        "step": step_lab,
                        "deliverable": result.get("deliverable", ""),
                    }
            else:
                # 该步骤阻塞，记录到集合供下游感知
                async with blocked_lock:
                    blocked_step_ids.add(sid)
        except Exception as exc:
            logger.error(f"run_step [{sid}] crashed: {exc}", exc_info=True)
            async with blocked_lock:
                blocked_step_ids.add(sid)
            await task_store.update_node_status(ids["ui_node_id"], "error", f"[CRASH]: {exc}")
        finally:
            # 无论成功、阻塞还是异常，都必须 set event 防止下游死锁
            step_events[sid].set()

    await asyncio.gather(*(run_step(s) for s in steps), return_exceptions=True)


def _inject_context_by_deps(base_input: str, depends_on: list[str], dep_results: dict[str, dict]) -> str:
    """仅注入声明了依赖的前置步骤产出，而不是所有产出"""
    if not dep_results:
        return base_input
    sections = [
        f"### [{v['role']}] {v['step']}\n{v['deliverable']}"
        for k, v in dep_results.items() if k in depends_on
    ]
    return (
        f"{base_input}\n\n"
        "---\n"
        "## Outputs from Your Prerequisite Steps (continue from this basis)\n\n"
        + "\n\n".join(sections)
    )


async def _resume_downstream_cascade(
    task_id: str,
    studio_id: str,
    retried_step_id: str,
    retried_deliverable: str,
):
    """
    上游步骤重试成功后，自动找出所有依赖它的下游步骤（无论之前是 blocked 还是
    accepted），按拓扑顺序依次重新执行。作为异步生成器，yield SSE 状态事件。

    · blocked 下游：之前被级联跳过，现在可以恢复
    · accepted 下游：上游产出变了，重新跑一遍确保结果是最新的
    """
    task = await task_store.get(task_id)
    if not task:
        return

    step_map = {st.step_id: st for st in task.sub_tasks if st.step_id}

    # 已完成表：所有 accepted 步骤（不含刚重试的那个，它的产出单独传入）
    completed: dict[str, dict] = {
        st.step_id: {
            "role": st.assign_to_role,
            "step": st.step_label,
            "deliverable": st.deliverable or "",
        }
        for st in task.sub_tasks
        if st.status == "accepted" and st.step_id and st.step_id != retried_step_id
    }
    # 把刚重试成功的产出也放进去
    retried_st = step_map.get(retried_step_id)
    if retried_st:
        completed[retried_step_id] = {
            "role": retried_st.assign_to_role,
            "step": retried_st.step_label,
            "deliverable": retried_deliverable,
        }

    # BFS 找出所有（直接或间接）依赖 retried_step_id 的步骤
    def get_all_downstream(start: str) -> list[str]:
        result: list[str] = []
        visited = {start}
        queue = [start]
        while queue:
            cur = queue.pop(0)
            for sid, st in step_map.items():
                if sid not in visited and cur in (st.depends_on or []):
                    visited.add(sid)
                    queue.append(sid)
                    result.append(sid)
        return result

    downstream_ids = get_all_downstream(retried_step_id)
    # 对 blocked 和 accepted 的下游均重新执行
    to_rerun = [
        sid for sid in downstream_ids
        if step_map.get(sid) and step_map[sid].status in ("blocked", "accepted")
    ]

    if not to_rerun:
        return

    # 拓扑排序（Kahn）：每轮收集所有"依赖已解决"的节点
    def topo_sort(ids: list[str]) -> list[str]:
        in_set = set(ids)
        pending_deps = {
            sid: {d for d in (step_map[sid].depends_on or []) if d in in_set}
            for sid in ids
        }
        ordered: list[str] = []
        while pending_deps:
            ready = [sid for sid, deps in pending_deps.items() if not deps]
            if not ready:
                # 环：把剩下的按 id 顺序追加，避免卡死
                ordered.extend(pending_deps.keys())
                break
            # 保持与原 ids 列表一致的相对顺序，方便人工阅读日志
            ready.sort(key=lambda x: ids.index(x))
            for sid in ready:
                ordered.append(sid)
                pending_deps.pop(sid, None)
            for deps in pending_deps.values():
                deps.difference_update(ready)
        return ordered

    ordered = topo_sort(to_rerun)
    newly_blocked: set[str] = set()

    def _evt(msg: str) -> dict:
        return {"event": "status", "data": json.dumps({"status": "executing", "message": msg, "task_id": task_id})}

    for sid in ordered:
        st = step_map[sid]
        deps = st.depends_on or []

        # 若任意依赖还没完成，跳过
        unresolvable = [d for d in deps if d not in completed]
        if unresolvable:
            newly_blocked.add(sid)
            msg = (
                enc("backendTaskStatus.cascade_deps_incomplete_skipped", label=st.step_label)
                if st.status == "blocked"
                else enc("backendTaskStatus.cascade_deps_incomplete_deferred", label=st.step_label)
            )
            yield _evt(msg)
            continue

        dep_results = {k: v for k, v in completed.items() if k in deps}
        input_ctx = _inject_context_by_deps(st.input_context, deps, dep_results)
        ui_node_id = st.group_id or ""

        msg = (
            enc("backendTaskStatus.cascade_running_resume", label=st.step_label)
            if st.status == "blocked"
            else enc("backendTaskStatus.cascade_running_rerun", label=st.step_label)
        )
        yield _evt(msg)

        await task_store.update_node_status(ui_node_id, "running", "")
        await task_store.update_sub_task_status(st.id, "running")

        result = await _run_employee_with_review(
            task_id=task_id,
            sub_task_id=st.id,
            ui_node_id=ui_node_id,
            emp_role=st.assign_to_role,
            input_ctx=input_ctx,
            studio_id=studio_id,
            step_lab=st.step_label,
            original_spec=st.input_context,
        )

        if result.get("status") == "accepted":
            completed[sid] = {
                "role": st.assign_to_role,
                "step": st.step_label,
                "deliverable": result.get("deliverable", ""),
            }
            yield _evt(enc("backendTaskStatus.cascade_step_ok", label=st.step_label))
        else:
            newly_blocked.add(sid)
            yield _evt(
                enc(
                    "backendTaskStatus.cascade_step_blocked",
                    label=st.step_label,
                    detail=(result.get("blocker_reason") or "-")[:60],
                )
            )


# ──────────────────────────────────────────────
# 员工执行 + Leader 质检循环
# ──────────────────────────────────────────────
async def _run_employee_with_review(
    task_id: str,
    sub_task_id: str,
    ui_node_id: str,
    emp_role: str,
    input_ctx: str,
    studio_id: str,
    step_lab: str,
    original_spec: str,
) -> dict:

    studio = await studio_store.get(studio_id)
    agent_md, soul, available_tools = "", "", None
    agent_id: str | None = None

    if studio:
        for sa in studio.sub_agents:
            if sa.role == emp_role:
                agent_md = sa.agent_md
                soul = sa.soul
                if sa.skills:
                    available_tools = sa.skills
                agent_id = sa.id
                await _mark_agent_working(sa.id)
                break

    attempt = 0
    extra_feedback = ""
    res: dict = {"status": "blocked", "blocker_reason": "Not executed"}

    # 计时 + 模型名，供本步统计成本用
    _step_started_at = asyncio.get_event_loop().time()
    _model_name = llm_service.get_model_for_role("sub_agent")
    _model_display_name = llm_service.get_model_display_name(_model_name)
    await task_store.mark_sub_task_started(sub_task_id)

    try:
        while attempt <= MAX_REVIEW_RETRIES:
            actual_input = input_ctx
            if extra_feedback:
                actual_input += (
                    "\n\n---\n"
                    "[Leader quality review feedback: revise and resubmit to address the issues below]\n"
                    f"{extra_feedback}"
                )

            await task_store.update_node_status(ui_node_id, "running")
            await task_store.update_sub_task_status(sub_task_id, "running")

            async def _progress(msg: str) -> None:
                # 把模型的"我现在在做什么"实时透传到 UI 节点上
                # 仅截取前 120 字符，避免污染最终 deliverable 长度
                try:
                    await task_store.update_node_status(
                        ui_node_id, "running", f"[{emp_role}] {msg[:120]}"
                    )
                except Exception:
                    pass

            res = await sub_agent_executor.run(
                emp_role, agent_md, soul, actual_input, available_tools,
                progress_callback=_progress,
                task_id=task_id,
                sub_task_id=sub_task_id,
                studio_id=studio_id,
            )

            if res.get("status") == "blocked":
                blocker = res.get("blocker_reason", "Blocked")
                await task_store.update_sub_task_status(sub_task_id, "blocked", blocker_reason=blocker)
                await task_store.update_node_status(ui_node_id, "error", f"[BLOCKED]: {blocker}")
                break

            # 员工完成 → 送质检
            deliverable = res.get("deliverable", "")
            await task_store.update_sub_task_review(sub_task_id, "pending_review")
            await task_store.update_node_status(ui_node_id, "running", f"[Reviewing] {deliverable[:80]}...")

            review = await studio_leader.review_sub_task(studio_id, step_lab, original_spec, deliverable)

            if review["verdict"] == "accept":
                await task_store.update_sub_task_status(sub_task_id, "accepted", deliverable=deliverable)
                await task_store.update_node_status(ui_node_id, "completed", deliverable)
                res["status"] = "accepted"
                break
            else:
                extra_feedback = review["feedback"]
                await task_store.update_sub_task_review(sub_task_id, "revision_requested", extra_feedback)
                await task_store.update_node_status(ui_node_id, "running", f"[Revision requested] {extra_feedback[:80]}")
                attempt += 1
        else:
            # 达到最大重试次数，强制接受最后一次产出
            deliverable = res.get("deliverable", "(Maximum retry count reached; accepting the last deliverable.)")
            await task_store.update_sub_task_status(sub_task_id, "accepted", deliverable=deliverable)
            await task_store.update_node_status(ui_node_id, "completed", deliverable)
            res["status"] = "accepted"

        # 累计 token 消耗到成员和工作室
        tokens_used = res.get("tokens", 0) or 0
        if tokens_used > 0 and studio:
            sa_id = next((sa.id for sa in studio.sub_agents if sa.role == emp_role), None)
            if sa_id:
                await studio_store.add_tokens(sa_id, studio_id, tokens_used)

        # 落库单步 tokens / duration / cost，前端才能看到"每一步花了多少"
        duration_ms = int((asyncio.get_event_loop().time() - _step_started_at) * 1000)
        cost_usd = estimate_cost_usd(tokens_used, _model_name)
        try:
            await task_store.record_sub_task_metrics(
                sub_task_id=sub_task_id,
                tokens=tokens_used,
                duration_ms=duration_ms,
                cost_usd=cost_usd,
                model_name=_model_display_name or _model_name,
            )
        except Exception as mex:
            logger.warning(f"record_sub_task_metrics failed for {sub_task_id}: {mex}")

        return res
    finally:
        # 无论成功/失败/异常都释放员工忙碌标记
        if agent_id:
            try:
                await _mark_agent_idle(agent_id)
            except Exception as _e:
                logger.warning(f"释放员工 {agent_id} is_working 失败: {_e}")


# ──────────────────────────────────────────────
# POST /tasks/{task_id}/retry-step — 用户补充信息后重试被阻塞的节点（SSE）
# ──────────────────────────────────────────────
@router.post("/{task_id}/retry-step")
async def retry_step(task_id: str, request: Request, payload: dict):
    """
    payload: { node_id: str, extra_context: str }
    node_id 对应前端 PathNode.id（即 path_nodes 表中的 id）。
    通过 sub_tasks.group_id == node_id 找到对应的子任务记录。
    """
    node_id = payload.get("node_id", "")
    extra_context = payload.get("extra_context", "").strip()
    if not node_id:
        raise HTTPException(400, "node_id 不能为空")

    task = await task_store.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")

    # 找到对应子任务：group_id 存储的就是 ui_node_id
    sub_task = next((st for st in task.sub_tasks if st.group_id == node_id), None)
    if not sub_task:
        raise HTTPException(404, f"找不到节点 {node_id} 对应的子任务")

    async def event_generator():
        def _status(msg: str):
            return {"event": "status", "data": json.dumps({"status": "executing", "message": msg, "task_id": task_id})}

        studio_id = sub_task.studio_id or task.studio_id or "studio_0"
        input_ctx = sub_task.input_context
        if extra_context:
            input_ctx += (
                "\n\n---\n"
                "## User Supplemental Information (retry using this context)\n\n"
                f"{extra_context}"
            )

        yield _status(enc("backendTaskStatus.retry_running", label=sub_task.step_label))

        # 重置节点和子任务状态
        await task_store.update_node_status(node_id, "running", "")
        await task_store.update_sub_task_status(sub_task.id, "running")
        # 把任务拉回执行状态，让 /stream 能感知
        await task_store.update_task_status(task_id, "executing")

        result = await _run_employee_with_review(
            task_id=task_id,
            sub_task_id=sub_task.id,
            ui_node_id=node_id,
            emp_role=sub_task.assign_to_role,
            input_ctx=input_ctx,
            studio_id=studio_id,
            step_lab=sub_task.step_label,
            original_spec=sub_task.input_context,
        )

        if result.get("status") == "accepted":
            yield _status(enc("backendTaskStatus.retry_success", label=sub_task.step_label))
            # 自动级联重执行所有下游步骤（含已完成的）
            async for evt in _resume_downstream_cascade(
                task_id, studio_id, sub_task.step_id, result.get("deliverable", "")
            ):
                yield evt
        else:
            yield _status(
                enc(
                    "backendTaskStatus.retry_still_blocked",
                    label=sub_task.step_label,
                    detail=(result.get("blocker_reason") or "-")[:60],
                )
            )

        # 重新检查整体是否还有阻塞
        task_snap = await task_store.get(task_id)
        if task_snap:
            still_blocked = any(st.status == "blocked" for st in task_snap.sub_tasks)
            await task_monitor._finalize_task(task_id, task_snap.question, still_blocked)

        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_generator())


# ──────────────────────────────────────────────
# POST /tasks/{task_id}/edit-step — 用户手动编辑某步 output 并级联重算下游（SSE）
# ──────────────────────────────────────────────
@router.post("/{task_id}/edit-step")
async def edit_step(task_id: str, request: Request, payload: dict):
    """
    payload: { node_id: str, new_output: str, cascade: bool = True }

    行为：
      1. 用 new_output 覆盖对应 PathNode.output + SubTask.deliverable
      2. sub_task 状态强制置为 accepted，标记 edited_by_user=1
      3. 若 cascade，自动重跑所有下游步骤（复用 _resume_downstream_cascade）
      4. SSE 推送：edit_committed → 级联状态 → done

    这样用户可以：手动改一句话 → 下游重新消化新内容，而不用整个流程重来。
    """
    node_id = (payload.get("node_id") or "").strip()
    new_output = payload.get("new_output", "")
    cascade = bool(payload.get("cascade", True))

    if not node_id:
        raise HTTPException(400, "node_id 不能为空")
    if not isinstance(new_output, str) or not new_output.strip():
        raise HTTPException(400, "new_output 不能为空")

    task = await task_store.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")

    sub_task = next((st for st in task.sub_tasks if st.group_id == node_id), None)
    if not sub_task:
        raise HTTPException(404, f"找不到节点 {node_id} 对应的子任务")

    if task.status not in (
        "completed", "completed_with_blockers", "timeout_killed", "executing", "terminated"
    ):
        raise HTTPException(400, f"当前任务状态 {task.status} 不支持编辑某步产出")

    async def event_generator():
        def _status(msg: str, status: str = "executing"):
            return {"event": "status", "data": json.dumps(
                {"status": status, "message": msg, "task_id": task_id}
            )}

        # 1. 持久化编辑
        await task_store.manual_edit_sub_task(sub_task.id, new_output)
        await task_store.update_node_status(node_id, "completed", new_output)

        yield {"event": "edit_committed", "data": json.dumps({
            "node_id": node_id,
            "sub_task_id": sub_task.id,
            "step_label": sub_task.step_label,
            "edited_at": datetime.now().isoformat(),
        })}
        yield _status(enc("backendTaskStatus.edit_manual_ok", label=sub_task.step_label))

        # 2. 级联下游
        if cascade:
            studio_id = sub_task.studio_id or task.studio_id or "studio_0"
            # 进入执行态，让 /stream 的前端能识别
            await task_store.update_task_status(task_id, "executing")
            async for evt in _resume_downstream_cascade(
                task_id, studio_id, sub_task.step_id, new_output
            ):
                yield evt

            # 重新 finalize
            task_snap = await task_store.get(task_id)
            if task_snap:
                still_blocked = any(st.status == "blocked" for st in task_snap.sub_tasks)
                await task_monitor._finalize_task(
                    task_id, task_snap.question, still_blocked
                )

        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_generator())


# ──────────────────────────────────────────────
# GET /tasks/{task_id}/stream — SSE 状态推送
# ──────────────────────────────────────────────
@router.get("/{task_id}/stream")
async def task_stream_view(task_id: str, request: Request):
    async def event_tailer():
        last_nodes_len = 0
        last_sig: tuple[str | None, str] | None = None
        pause_event_sent = False
        # 同时跟踪 status 和 output，任意一个变化都推 node_updated，
        # 让前端能实时看到模型在做什么（ReAct 步骤 / 质检阶段等）
        sent_node_signature: dict[str, tuple[str, str]] = {}
        # 心跳间隔（秒）：每隔一段时间无论有没有变化，都推一次 heartbeat，
        # 让前端能识别"服务端还活着"，并据此判断节点是否疑似卡死
        HEARTBEAT_INTERVAL_S = 3.0
        last_heartbeat_ts = 0.0

        while True:
            if await request.is_disconnected():
                break

            t = await task_store.get(task_id)
            if not t:
                yield {"event": "error", "data": "Not found"}
                break

            msg = (t.status_message or "")
            status_sig: tuple[str | None, str] = (t.status, msg)
            if status_sig != last_sig:
                payload: dict = {"status": t.status, "task_id": task_id, "message": msg}
                sid = t.studio_id or t.plan_studio_id
                if sid:
                    payload["studio_id"] = sid
                    stu = await studio_store.get(sid)
                    if stu and stu.scenario:
                        payload["studio_scenario"] = stu.scenario
                yield {"event": "status", "data": json.dumps(payload, default=_json_serial)}
                last_sig = status_sig

            if len(t.nodes) > last_nodes_len:
                for n in t.nodes[last_nodes_len:]:
                    yield {"event": "node_added", "data": json.dumps(n.model_dump(), default=_json_serial)}
                    sent_node_signature[n.id] = (n.status, n.output or "")
                last_nodes_len = len(t.nodes)

            for n in t.nodes:
                sig = (n.status, n.output or "")
                prev = sent_node_signature.get(n.id)
                if sig != prev:
                    yield {"event": "node_updated", "data": json.dumps(
                        {"node_id": n.id, "status": n.status, "output": n.output},
                        default=_json_serial
                    )}
                    sent_node_signature[n.id] = sig

            now = asyncio.get_event_loop().time()
            if now - last_heartbeat_ts >= HEARTBEAT_INTERVAL_S:
                running_nodes = [
                    {"id": n.id, "agent_role": n.agent_role, "step_label": n.step_label}
                    for n in t.nodes if n.status == "running"
                ]
                yield {"event": "heartbeat", "data": json.dumps({
                    "task_id": task_id,
                    "task_status": t.status,
                    "ts_ms": int(datetime.now().timestamp() * 1000),
                    "running_count": len(running_nodes),
                    "running_nodes": running_nodes,
                })}
                last_heartbeat_ts = now

            if (
                t.status in ("need_clarification", "await_leader_plan_approval")
                and not pause_event_sent
            ):
                psid = t.plan_studio_id or t.studio_id or ""
                stu = await studio_store.get(psid) if psid else None
                scen = (stu.scenario or "") if stu else ""
                if t.status == "need_clarification":
                    pause_data = {
                        "action": "need_clarification",
                        "studio_id": psid,
                        "studio_scenario": scen,
                        "questions": t.clarification_questions,
                        "task_id": task_id,
                    }
                else:
                    pause_data = {
                        "action": "review_plan",
                        "studio_id": psid,
                        "studio_scenario": scen,
                        "steps": t.plan_steps,
                        "task_id": task_id,
                    }
                yield {"event": "done_pause", "data": json.dumps(pause_data, default=_json_serial)}
                pause_event_sent = True
                yield {"event": "done", "data": json.dumps({"status": t.status, "task_id": task_id})}
                break

            if t.status in (
                "completed", "completed_with_blockers", "timeout_killed", "terminated", "failed",
            ):
                yield {"event": "done", "data": json.dumps({"status": t.status, "task_id": task_id})}
                break

            await asyncio.sleep(1.0)

    return EventSourceResponse(event_tailer())


# ──────────────────────────────────────────────
# POST /tasks/{task_id}/terminate — 终止正在执行的任务
# ──────────────────────────────────────────────
@router.post("/{task_id}/terminate")
async def terminate_task(task_id: str):
    """
    发送终止信号给正在执行的任务。
    任务状态变为 terminated，保留已有方案步骤和节点，以便用户重新规划后继续执行。
    """
    task = await task_store.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task.status not in ("planning", "executing"):
        raise HTTPException(400, f"任务当前状态 {task.status} 不支持终止（只能终止规划中或执行中的任务）")

    # 向 DAG 执行循环发送取消信号
    if task_id in _cancel_registry:
        _cancel_registry[task_id].set()
        logger.info(f"[Task {task_id}] 已发送取消信号")

    # 立即 cancel 所有正在跑的子步骤 asyncio.Task，强制中断 LLM/工具调用
    cancelled_cnt = _cancel_all_running(task_id)
    if cancelled_cnt:
        logger.info(f"[Task {task_id}] 已 cancel {cancelled_cnt} 个运行中的协程")

    worker_cnt = await terminate_task_worker(task_id)
    if worker_cnt:
        logger.info(f"[Task {task_id}] 已终止隔离执行进程")

    # 立即更新任务状态，让前端感知
    await task_store.update_task_status(task_id, "terminated")

    return {
        "status": "ok",
        "message": (
            f"任务已终止（中断 {cancelled_cnt} 个运行中协程，"
            f"终止 {worker_cnt} 个隔离执行进程），方案步骤已保留，可重新规划后继续执行"
        ),
    }


# ──────────────────────────────────────────────
# 批注系统
# ──────────────────────────────────────────────
@router.get("/{task_id}/annotations")
async def list_annotations(task_id: str):
    return await task_store.list_annotations(task_id)


@router.delete("/{task_id}/annotations/{ann_id}")
async def delete_annotation(task_id: str, ann_id: str):
    await task_store.delete_annotation(ann_id)
    return {"status": "ok"}


@router.post("/{task_id}/annotate")
async def annotate(task_id: str, payload: dict):
    """
    创建批注并通过 SSE 流式返回 AI 回答。
    payload: { node_id, selected_text, question }
    """
    node_id = payload.get("node_id", "") or "__synthesis__"
    selected_text = payload.get("selected_text", "")
    question = payload.get("question", "")

    if not selected_text or not question:
        raise HTTPException(400, "selected_text / question 不能为空")

    task = await task_store.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")

    root_node = next((n for n in task.nodes if n.type == "agent_zero"), None)
    target_node = next((n for n in task.nodes if n.id == node_id), None)
    if node_id.startswith(OUTPUT_NODE_PREFIX):
        output_path = node_id[len(OUTPUT_NODE_PREFIX):]
        node_context = await _read_output_annotation_context(task_id, output_path)
        node_role = "Output file"
        node_label = output_path
    elif node_id == "__synthesis__" and root_node:
        node_id = root_node.id
        target_node = root_node
        node_context = root_node.output or ""
        node_role = root_node.agent_role or "Agent0"
        node_label = root_node.step_label or "Synthesis"
    else:
        node_context = target_node.output if target_node else ""
        node_role = target_node.agent_role if target_node else "Unknown"
        node_label = target_node.step_label if target_node else ""

    ann_id = uuid.uuid4().hex[:12]
    await task_store.create_annotation(ann_id, task_id, node_id, selected_text, question)

    async def event_generator():
        from services.llm_service import llm_service

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a task-result annotation assistant. The user selected text while reading "
                    "a studio member's output and asked a question.\n"
                    "Answer precisely using the full output context. Keep the answer concise, accurate, "
                    "and directly relevant.\n"
                    "Markdown is allowed."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"## Output Source\n"
                    f"- Step: {node_label}\n"
                    f"- Role: {node_role}\n\n"
                    f"## Full Output Content\n{node_context}\n\n"
                    f"---\n\n"
                    f"## User-Selected Text\n> {selected_text}\n\n"
                    f"## User Question\n{question}"
                ),
            },
        ]

        full_answer = ""
        try:
            stream = await llm_service.chat(
                messages=messages,
                role="sub_agent",
                stream=True,
                temperature=0.5,
            )
            async for chunk in stream:
                if chunk:
                    full_answer += chunk
                    yield {
                        "event": "chunk",
                        "data": json.dumps({"text": chunk, "ann_id": ann_id}),
                    }
        except Exception as e:
            logger.exception(f"批注 AI 回答失败: {e}")
            err_msg = f"回答生成失败: {type(e).__name__}"
            full_answer = err_msg
            yield {"event": "chunk", "data": json.dumps({"text": err_msg, "ann_id": ann_id})}

        # 保存完整回答
        await task_store.update_annotation_answer(ann_id, full_answer)
        yield {
            "event": "done",
            "data": json.dumps({"ann_id": ann_id, "answer": full_answer}),
        }

    return EventSourceResponse(event_generator())


async def _read_output_annotation_context(task_id: str, output_path: str) -> str:
    if not output_path:
        return ""
    try:
        sandbox = await sandbox_store.get_by_task(task_id)
        if not sandbox:
            return ""
        return sandbox_store.read_file(sandbox, output_path, max_chars=120_000)
    except Exception:
        return ""


@router.post("/{task_id}/process-selection")
async def process_selection(task_id: str, payload: dict):
    """
    基于结果页选中的片段派生一个新任务。
    payload: { node_id, selected_text, instruction }
    """
    node_id = payload.get("node_id", "")
    selected_text = payload.get("selected_text", "")
    instruction = payload.get("instruction", "")

    if not node_id or not selected_text or not instruction:
        raise HTTPException(400, "node_id / selected_text / instruction 不能为空")

    task = await task_store.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")

    root_node = next((n for n in task.nodes if n.type == "agent_zero"), None)
    target_node = next((n for n in task.nodes if n.id == node_id), None)
    if node_id.startswith(OUTPUT_NODE_PREFIX):
        output_path = node_id[len(OUTPUT_NODE_PREFIX):]
        node_context = await _read_output_annotation_context(task_id, output_path)
        node_role = "Output file"
        node_label = output_path
    elif node_id == "__synthesis__" and root_node:
        target_node = root_node
        node_context = root_node.output or ""
        node_role = root_node.agent_role or "Agent0"
        node_label = root_node.step_label or "Synthesis"
    else:
        node_context = (target_node.output if target_node else "") or (root_node.output if root_node else "")
        node_role = target_node.agent_role if target_node else (root_node.agent_role if root_node else "Agent0")
        node_label = target_node.step_label if target_node else "Synthesis"

    clarification_pairs = []
    questions_by_id = {
        str(q.get("id", "")): str(q.get("question", ""))
        for q in (task.clarification_questions or [])
        if q.get("id")
    }
    for qid, answer in (task.clarification_answers or {}).items():
        if not answer:
            continue
        question = questions_by_id.get(str(qid), str(qid))
        clarification_pairs.append(f"- {question}: {answer}")

    sub_task_context = []
    for sub_task in task.sub_tasks[-6:]:
        summary = (sub_task.distilled_summary or sub_task.deliverable or "").strip()
        if not summary:
            continue
        snippet = summary[:240] + ("…" if len(summary) > 240 else "")
        sub_task_context.append(
            f"- {sub_task.step_label} / {sub_task.assign_to_role}: {snippet}"
        )

    derived_question = (
        "You are continuing work based on the result of an existing task.\n\n"
        f"## Original Task Goal\n{task.question}\n\n"
        f"## Original Task ID\n{task.id}\n"
        f"## Original Task Status\n{task.status}\n\n"
        f"## Source Segment\n"
        f"- Step: {node_label}\n"
        f"- Role: {node_role}\n\n"
        f"## User-Selected Text\n> {selected_text}\n\n"
        f"## How the User Wants It Processed\n{instruction}\n\n"
        f"## Conversation Context\n"
        f"{chr(10).join(clarification_pairs) if clarification_pairs else 'No extra clarifications.'}\n\n"
        f"## Recent Step Summaries\n"
        f"{chr(10).join(sub_task_context) if sub_task_context else 'No step summaries.'}\n\n"
        f"## Full Source Context\n{node_context or 'No full context available.'}\n\n"
        "Based on the context above, treat this as a new task and continue execution."
    )

    new_task = await task_store.create(question=derived_question, studio_id=task.studio_id)
    preferred_studio_id = task.studio_id if task.studio_id and task.studio_id != "studio_0" else None
    await _schedule_ask_pipeline(new_task.id, derived_question, preferred_studio_id=preferred_studio_id)

    logger.info(
        f"[Task {task_id}] 结果片段派生新任务 task={new_task.id} from node={node_id}"
    )
    return {
        "status": "ok",
        "task_id": new_task.id,
        "message": "已基于当前选区创建新任务",
    }


@router.post("/{task_id}/iterate-selection")
async def iterate_selection(task_id: str, payload: dict):
    """
    基于结果页选中的片段，在当前任务内继续发起一轮迭代。
    payload: { node_id, selected_text, instruction }
    """
    node_id = payload.get("node_id", "")
    selected_text = payload.get("selected_text", "")
    instruction = payload.get("instruction", "")

    if not node_id or not selected_text or not instruction:
        raise HTTPException(400, "node_id / selected_text / instruction 不能为空")

    task = await task_store.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")

    if task.status in ("planning", "executing"):
        raise HTTPException(409, "任务当前正在运行，无法发起新的迭代")

    root_node = next((n for n in task.nodes if n.type == "agent_zero"), None)
    target_node = next((n for n in task.nodes if n.id == node_id), None)
    if node_id.startswith(OUTPUT_NODE_PREFIX):
        output_path = node_id[len(OUTPUT_NODE_PREFIX):]
        node_context = await _read_output_annotation_context(task_id, output_path)
        node_role = "Output file"
        node_label = output_path
    elif node_id == "__synthesis__" and root_node:
        target_node = root_node
        node_context = root_node.output or ""
        node_role = root_node.agent_role or "Agent0"
        node_label = root_node.step_label or "Synthesis"
    else:
        node_context = (target_node.output if target_node else "") or (root_node.output if root_node else "")
        node_role = target_node.agent_role if target_node else (root_node.agent_role if root_node else "Agent0")
        node_label = target_node.step_label if target_node else "Synthesis"

    clarification_pairs = []
    questions_by_id = {
        str(q.get("id", "")): str(q.get("question", ""))
        for q in (task.clarification_questions or [])
        if q.get("id")
    }
    for qid, answer in (task.clarification_answers or {}).items():
        if not answer:
            continue
        question = questions_by_id.get(str(qid), str(qid))
        clarification_pairs.append(f"- {question}: {answer}")

    sub_task_context = []
    for sub_task in task.sub_tasks[-8:]:
        summary = (sub_task.distilled_summary or sub_task.deliverable or sub_task.blocker_reason or "").strip()
        if not summary:
            continue
        snippet = summary[:240] + ("…" if len(summary) > 240 else "")
        sub_task_context.append(
            f"- {sub_task.step_label} / {sub_task.assign_to_role} / {sub_task.status}: {snippet}"
        )

    iteration_goal = (
        "You are incrementally iterating on an existing task artifact, not starting a completely independent new job.\n\n"
        f"## Current Task Goal\n{task.question}\n\n"
        f"## Current Task ID\n{task.id}\n"
        f"## Current Task Status\n{task.status}\n\n"
        f"## Iteration Goal\n"
        f"The user requested modifications or enhancements to the current artifact. Continue iterating "
        f"**inside the current task**, and plan incremental steps using the selected segment and user request below.\n\n"
        f"## Source of the Segment Being Modified\n"
        f"- Step: {node_label}\n"
        f"- Role: {node_role}\n\n"
        f"## User-Selected Text\n> {selected_text}\n\n"
        f"## User Iteration Request\n{instruction}\n\n"
        f"## Conversation Context\n"
        f"{chr(10).join(clarification_pairs) if clarification_pairs else 'No extra clarifications.'}\n\n"
        f"## Recent Output Summaries from Current Task\n"
        f"{chr(10).join(sub_task_context) if sub_task_context else 'No step summaries.'}\n\n"
        f"## Full Context Containing the Current Segment\n{node_context or 'No full context available.'}\n\n"
        "## Studio and Sandbox Strategy\n"
        "For this round, let Agent Zero reevaluate the suitable studio path based on the new request. "
        "Do not mechanically reuse the previous studio only for historical consistency.\n"
        "This iteration is still the **same task** (same task_id). The sandbox, tool runtime environment, "
        "existing code, and frontend artifacts are bound to this task. Iterate on top of the original sandbox "
        "and existing artifacts. Do not create a disconnected sandbox or parallel environment.\n\n"
        "Plan only the necessary incremental steps around the current task and selected segment. Do not rebuild from scratch by default."
    )

    await task_store.begin_iteration(
        task_id,
        instruction=instruction,
        title="Iterate from Selection",
        source_node_id=node_id,
    )
    await task_store.update_task_status(task_id, "planning")
    await task_store.set_status_message(task_id, enc("backendTaskStatus.agent0_replan_iteration"))
    # 不传 preferred_studio_id：与「基于结果迭代」一致，由 Agent0 重新路由；沙箱仍属当前 task
    await _schedule_ask_pipeline(
        task_id,
        iteration_goal,
        preferred_studio_id=None,
    )

    logger.info(
        f"[Task {task_id}] 在原任务内发起选区迭代 from node={node_id} (Agent0 路由，无 preferred_studio)"
    )
    return {
        "status": "ok",
        "task_id": task_id,
        "message": "已在当前任务内发起新一轮迭代",
    }


@router.post("/{task_id}/iterate")
async def iterate_task(task_id: str, payload: dict):
    """
    基于当前结果整体，在当前任务内继续发起一轮迭代。
    payload: { instruction: str, messages?: [{role, content}] }
    """
    instruction = (payload.get("instruction", "") or "").strip()
    messages = payload.get("messages") or []
    if not instruction:
        raise HTTPException(400, "instruction 不能为空")

    task = await task_store.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task.status in ("planning", "executing"):
        raise HTTPException(409, "任务当前正在运行，无法发起新的迭代")

    root_node = next((n for n in task.nodes if n.type == "agent_zero"), None)
    synthesis = (root_node.output if root_node else "") or ""

    clarification_pairs = []
    questions_by_id = {
        str(q.get("id", "")): str(q.get("question", ""))
        for q in (task.clarification_questions or [])
        if q.get("id")
    }
    for qid, answer in (task.clarification_answers or {}).items():
        if not answer:
            continue
        question = questions_by_id.get(str(qid), str(qid))
        clarification_pairs.append(f"- {question}: {answer}")

    sub_task_context = []
    for sub_task in task.sub_tasks[-8:]:
        summary = (sub_task.distilled_summary or sub_task.deliverable or sub_task.blocker_reason or "").strip()
        if not summary:
            continue
        snippet = summary[:240] + ("…" if len(summary) > 240 else "")
        sub_task_context.append(
            f"- {sub_task.step_label} / {sub_task.assign_to_role} / {sub_task.status}: {snippet}"
        )

    recent_chat = []
    for msg in messages[-6:]:
        role = (msg.get("role") or "").strip()
        content = (msg.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            recent_chat.append(f"{role.upper()}: {content}")

    iteration_goal = (
        "You are incrementally iterating on the current result of an existing task, not creating a brand-new job.\n\n"
        f"## Current Task Goal\n{task.question}\n\n"
        f"## Current Task ID\n{task.id}\n"
        f"## Current Task Status\n{task.status}\n\n"
        f"## New User Request\n{instruction}\n\n"
        f"## Result Chat Context\n"
        f"{chr(10).join(recent_chat) if recent_chat else 'No extra chat context.'}\n\n"
        f"## Requirement Clarifications\n"
        f"{chr(10).join(clarification_pairs) if clarification_pairs else 'No extra clarifications.'}\n\n"
        f"## Recent Output Summaries from Current Task\n"
        f"{chr(10).join(sub_task_context) if sub_task_context else 'No step summaries.'}\n\n"
        f"## Current Synthesis Result\n{synthesis or 'No synthesis result yet.'}\n\n"
        "## Studio and Sandbox Strategy\n"
        "For this round, let Agent Zero reevaluate the suitable studio path based on the new request. "
        "Do not mechanically reuse the previous studio only for historical consistency.\n"
        "This iteration is still the **same task** (same task_id). The sandbox, tool runtime environment, "
        "existing code, and frontend artifacts are bound to this task. Iterate on top of the original sandbox "
        "and existing artifacts. Do not create a disconnected sandbox or parallel environment.\n\n"
        "Plan only the necessary incremental steps around the current task."
    )

    source_node_id = root_node.id if root_node else None
    await task_store.begin_iteration(
        task_id,
        instruction=instruction,
        title="Iterate from Result",
        source_node_id=source_node_id,
    )
    await task_store.update_task_status(task_id, "planning")
    await task_store.set_status_message(task_id, enc("backendTaskStatus.agent0_iterate_from_result"))
    # 不传 preferred_studio_id：不复用上一轮 studio 路由绑死；沙箱仍为当前 task（见 sandbox_owner_*）
    await _schedule_ask_pipeline(
        task_id,
        iteration_goal,
        preferred_studio_id=None,
    )
    return {"status": "ok", "task_id": task_id, "message": "已在当前任务内发起结果迭代"}


@router.post("/{task_id}/result-chat")
async def result_chat(task_id: str, payload: dict):
    """
    围绕当前任务结果进行多轮对话。
    payload: { messages: [{role, content}] }
    """
    messages = payload.get("messages") or []
    if not isinstance(messages, list) or not messages:
        raise HTTPException(400, "messages 不能为空")

    task = await task_store.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")

    root_node = next((n for n in task.nodes if n.type == "agent_zero"), None)
    synthesis = (root_node.output if root_node else "") or ""

    sub_task_context = []
    for sub_task in task.sub_tasks[-10:]:
        summary = (sub_task.distilled_summary or sub_task.deliverable or sub_task.blocker_reason or "").strip()
        if not summary:
            continue
        snippet = summary[:220] + ("…" if len(summary) > 220 else "")
        sub_task_context.append(
            f"- {sub_task.step_label} / {sub_task.assign_to_role} / {sub_task.status}: {snippet}"
        )

    clarification_pairs = []
    questions_by_id = {
        str(q.get("id", "")): str(q.get("question", ""))
        for q in (task.clarification_questions or [])
        if q.get("id")
    }
    for qid, answer in (task.clarification_answers or {}).items():
        if not answer:
            continue
        question = questions_by_id.get(str(qid), str(qid))
        clarification_pairs.append(f"- {question}: {answer}")

    llm_messages = [
        {
            "role": "system",
            "content": (
                "You are a task-result chat assistant. The user is asking follow-up questions "
                "about a task that is completed or nearly completed.\n"
                "Answer using the task goal, synthesis result, step-output summaries, and chat context.\n"
                "Prioritize accurate explanation of the current result, cite the basis when useful, "
                "and suggest next steps when necessary.\n"
                "Keep the answer concise and specific. Markdown is allowed."
            ),
        },
        {
            "role": "system",
            "content": (
                f"## Original Task Goal\n{task.question}\n\n"
                f"## Current Task Status\n{task.status}\n\n"
                f"## Requirement Clarifications\n{chr(10).join(clarification_pairs) if clarification_pairs else 'None'}\n\n"
                f"## Synthesis Result\n{synthesis or 'No synthesis result yet.'}\n\n"
                f"## Recent Step Summaries\n{chr(10).join(sub_task_context) if sub_task_context else 'No step summaries.'}"
            ),
        },
    ]
    for msg in messages[-12:]:
        role = (msg.get("role") or "").strip()
        content = (msg.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            llm_messages.append({"role": role, "content": content})

    async def event_generator():
        full_answer = ""
        try:
            stream = await llm_service.chat(
                messages=llm_messages,
                role="agent_zero",
                stream=True,
                temperature=0.4,
            )
            async for chunk in stream:
                if chunk:
                    full_answer += chunk
                    yield {"event": "chunk", "data": json.dumps({"text": chunk})}
        except Exception as e:
            logger.exception(f"结果对话失败: {e}")
            yield {"event": "chunk", "data": json.dumps({"text": f'回答生成失败: {type(e).__name__}'})}

        yield {"event": "done", "data": json.dumps({"answer": full_answer})}

    return EventSourceResponse(event_generator())


# ──────────────────────────────────────────────
# 启动：崩溃/重启恢复（供 main.py lifespan 调用）
# ──────────────────────────────────────────────
async def recover_interrupted_executions() -> None:
    """
    应用启动时调用：把上次进程崩溃前留在中间态的任务清理到一个可预期的状态。

    策略：
      - `planning` / `executing`：无法可靠续跑（内存中的 DAG 状态已丢失），直接标记为
        `terminated`，用户可以通过"沿用原方案重新执行"恢复。
      - `need_clarification` / `await_leader_plan_approval`：保持不动（纯等用户输入）。
    """
    from storage.database import get_db
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, status FROM tasks WHERE status IN ('planning', 'executing')"
        )
        rows = await cursor.fetchall()
        for row in rows:
            tid = row["id"]
            old_status = row["status"]
            reason = enc("backendTaskStatus.recovery_terminated")
            await db.execute(
                """UPDATE tasks
                   SET status='terminated',
                       failure_reason=?,
                       status_message=?,
                       completed_at=?,
                       updated_at=?,
                       last_activity_at=?
                   WHERE id=?""",
                (
                    reason, reason,
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                    tid,
                ),
            )
            logger.info(f"Recovery: task {tid} from '{old_status}' → 'terminated'")
        await db.commit()
    except Exception as e:
        logger.error(f"recover_interrupted_executions failed: {e}")
    finally:
        await db.close()


async def create_and_run_scheduled_task(job, run_id: str) -> str | None:
    """
    定时任务回调：根据 ScheduledJob 创建一个新 Task 并启动其编排流水线。

    返回值：新创建的 task_id；失败返回 None。
    """
    try:
        message = job.message or ""
        studio_id = job.target_studio_id or "studio_0"
        task = await task_store.create(
            question=message,
            studio_id=studio_id,
            sandbox_owner_type="schedule",
            sandbox_owner_id=job.id,
        )

        # 预先创建 CEO 节点，方便前端展示
        root_node = PathNode(
            id=str(uuid.uuid4())[:8],
            type="agent_zero", agent_role="Scheduler",
            step_label=f"Scheduled Task Trigger ({job.name or job.id})",
            input=message, output="",
            status="pending", position={"x": 400 + _iteration_x_offset(task), "y": 50},
        )
        await task_store.add_node(task.id, root_node)

        if should_run_inline():
            asyncio.create_task(
                run_scheduled_task_pipeline(task.id, studio_id, message, job.name or job.id)
            )
        else:
            await start_task_worker(
                "scheduled",
                task.id,
                {
                    "task_id": task.id,
                    "studio_id": studio_id,
                    "message": message,
                    "job_label": job.name or job.id,
                },
            )
        return task.id
    except Exception as e:
        logger.exception(f"create_and_run_scheduled_task failed: {e}")
        return None


async def run_scheduled_task_pipeline(task_id: str, studio_id: str, message: str, job_label: str = "") -> None:
    """Run a scheduled task pipeline inside an execution worker."""
    try:
        await task_store.update_task_status(task_id, "planning")
        plan_data = await _run_leader_planning(task_id, studio_id, message)

        # 定时任务模式下遇到澄清需求直接落到 need_clarification 等人工介入
        if plan_data.get("action") == "need_clarification":
            await task_store.save_clarification(
                task_id, studio_id, plan_data.get("questions", [])
            )
            await task_store.update_task_status(task_id, "need_clarification")
            return

        steps = plan_data.get("steps", [])
        _validate_dag(steps)
        await task_store.save_plan_steps(task_id, studio_id, steps)

        await task_store.update_task_status(task_id, "executing")
        route_cmd = {"studio_id": studio_id, "steps": steps}
        await _execute_background_orchestration(task_id, studio_id, route_cmd, "")
    except Exception as e:
        logger.exception(f"Scheduled task {task_id} execution failed ({job_label}): {e}")
        try:
            await task_store.update_task_status(task_id, "failed")
            await task_store.set_status_message(task_id, enc("backendTaskStatus.scheduled_failed_detail", detail=str(e)[:480]))
        except Exception:
            pass
