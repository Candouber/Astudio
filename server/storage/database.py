import asyncio
import json
import os
from pathlib import Path

import aiosqlite
from loguru import logger


def _resolve_data_dir() -> Path:
    configured = os.environ.get("ASTUDIO_DATA_DIR") or os.environ.get("ANTIT_DATA_DIR")
    if configured:
        return Path(configured).expanduser()

    user_data_dir = os.environ.get("ASTUDIO_USER_DATA_DIR")
    if user_data_dir:
        return Path(user_data_dir).expanduser() / "data"

    return Path(__file__).parent.parent.parent / "data"


DATA_DIR = _resolve_data_dir()
DB_PATH = DATA_DIR / "canvas.db"
STUDIOS_DIR = DATA_DIR / "studios"
SANDBOXES_DIR = DATA_DIR / "sandboxes"
CONFIG_PATH = DATA_DIR / "config.yaml"

_POOL_SIZE = int(os.environ.get("ASTUDIO_DB_POOL_SIZE") or os.environ.get("ANTIT_DB_POOL_SIZE", "12"))
_POOL_ACQUIRE_TIMEOUT_SECONDS = float(
    os.environ.get("ASTUDIO_DB_POOL_ACQUIRE_TIMEOUT")
    or os.environ.get("ANTIT_DB_POOL_ACQUIRE_TIMEOUT", "2.0")
)
_pool: asyncio.Queue | None = None
_pool_init_lock = asyncio.Lock()
_pool_all_conns: list = []
_pool_checked_out = 0
_overflow_conn_count = 0


def ensure_data_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STUDIOS_DIR.mkdir(parents=True, exist_ok=True)
    SANDBOXES_DIR.mkdir(parents=True, exist_ok=True)


async def _create_raw_conn() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(str(DB_PATH))
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.execute("PRAGMA busy_timeout=5000")
    return conn


async def _ensure_pool() -> asyncio.Queue:
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_init_lock:
        if _pool is not None:
            return _pool
        ensure_data_dirs()
        q: asyncio.Queue = asyncio.Queue(maxsize=_POOL_SIZE)
        for _ in range(_POOL_SIZE):
            conn = await _create_raw_conn()
            _pool_all_conns.append(conn)
            await q.put(conn)
        _pool = q
    return _pool


class _PooledConnHandle:
    """Compatibility wrapper: close() returns the connection to the pool."""

    def __init__(self, conn: aiosqlite.Connection, pool: asyncio.Queue | None):
        self._conn = conn
        self._pool = pool
        self._released = False

    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        return self._conn.executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        return self._conn.executescript(*args, **kwargs)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, v):
        self._conn.row_factory = v

    async def close(self) -> None:
        global _pool_checked_out
        if self._released:
            return
        self._released = True
        rollback_cancelled = False
        rollback_failed = False

        try:
            try:
                await self._conn.rollback()
            except asyncio.CancelledError:
                rollback_cancelled = True
            except Exception:
                rollback_failed = True

            if self._pool is None:
                await self._conn.close()
                return

            self._pool.put_nowait(self._conn)
        except Exception:
            try:
                await self._conn.close()
            except Exception:
                pass
        finally:
            if self._pool is not None:
                _pool_checked_out = max(0, _pool_checked_out - 1)
            if rollback_failed:
                logger.debug("Database rollback failed while returning pooled connection")
            if rollback_cancelled:
                logger.warning("Database close was cancelled after rollback started; connection was returned to pool")
                raise asyncio.CancelledError


async def get_db():
    """Borrow a pooled database connection."""
    global _pool_checked_out, _overflow_conn_count
    pool = await _ensure_pool()
    try:
        conn = await asyncio.wait_for(pool.get(), timeout=_POOL_ACQUIRE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        _overflow_conn_count += 1
        logger.error(
            "Database pool exhausted; opening overflow connection "
            f"(pool_size={_POOL_SIZE}, checked_out={_pool_checked_out}, available={pool.qsize()})"
        )
        conn = await _create_raw_conn()
        return _PooledConnHandle(conn, None)
    _pool_checked_out += 1
    return _PooledConnHandle(conn, pool)


def db_pool_status() -> dict:
    available = _pool.qsize() if _pool is not None else None
    return {
        "pool_size": _POOL_SIZE,
        "available": available,
        "checked_out": _pool_checked_out,
        "overflow_opened": _overflow_conn_count,
    }


async def close_all_pool_conns() -> None:
    global _pool
    if _pool is None:
        return
    while not _pool.empty():
        try:
            _pool.get_nowait()
        except asyncio.QueueEmpty:
            break
    for c in _pool_all_conns:
        try:
            await c.close()
        except Exception:
            pass
    _pool_all_conns.clear()
    _pool = None


async def init_database():
    ensure_data_dirs()

    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")

        # 工作室表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS studios (
                id TEXT PRIMARY KEY,
                scenario TEXT NOT NULL,
                description TEXT,
                core_capabilities TEXT DEFAULT '[]',
                recent_topics TEXT DEFAULT '[]',
                user_facts TEXT DEFAULT '[]',
                task_count INTEGER DEFAULT 0,
                is_working BOOLEAN DEFAULT FALSE,
                total_tokens INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP
            )
        """)

        # 工作室 sub-agent 配置表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sub_agent_configs (
                id TEXT PRIMARY KEY,
                studio_id TEXT NOT NULL,
                role TEXT NOT NULL,
                agent_md_path TEXT,
                soul_path TEXT,
                skills TEXT DEFAULT '[]', -- JSON array
                is_working BOOLEAN DEFAULT FALSE,
                total_tokens INTEGER DEFAULT 0,
                FOREIGN KEY (studio_id) REFERENCES studios(id) ON DELETE CASCADE
            )
        """)

        # 任务表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                current_iteration_id TEXT,
                sandbox_owner_type TEXT DEFAULT 'task',
                sandbox_owner_id TEXT,
                studio_id TEXT,
                question TEXT NOT NULL,
                status TEXT DEFAULT 'planning',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                last_activity_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                failure_reason TEXT DEFAULT '',
                FOREIGN KEY (studio_id) REFERENCES studios(id) ON DELETE SET NULL
            )
        """)

        # 任务迭代表：Task 是长期工作空间，Iteration 是一次规划/执行/修改分支
        await db.execute("""
            CREATE TABLE IF NOT EXISTS task_iterations (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                parent_iteration_id TEXT,
                source_node_id TEXT,
                title TEXT DEFAULT '',
                instruction TEXT DEFAULT '',
                status TEXT DEFAULT 'planning',
                plan_steps TEXT DEFAULT '[]',
                plan_studio_id TEXT,
                summary TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                FOREIGN KEY (parent_iteration_id) REFERENCES task_iterations(id) ON DELETE SET NULL
            )
        """)

        # 子任务/工单表 (流转实体)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sub_tasks (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                iteration_id TEXT,
                studio_id TEXT,
                group_id TEXT,
                step_id TEXT DEFAULT '',        -- Leader 分配的逻辑步骤 ID（用于 DAG 依赖）
                depends_on TEXT DEFAULT '[]',   -- JSON 数组，依赖的 step_id 列表
                step_label TEXT,
                assign_to_role TEXT,
                input_context TEXT,
                status TEXT DEFAULT 'pending',  -- pending|running|pending_review|revision_requested|accepted|blocked
                deliverable TEXT,
                blocker_reason TEXT,
                review_feedback TEXT,           -- Leader 质检反馈（revision_requested 时）
                attempt_index INTEGER DEFAULT 1,
                retry_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                distilled_summary TEXT,
                tokens INTEGER DEFAULT 0,
                duration_ms INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                model_name TEXT,
                edited_by_user INTEGER DEFAULT 0,
                edited_at TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                FOREIGN KEY (iteration_id) REFERENCES task_iterations(id) ON DELETE SET NULL,
                FOREIGN KEY (studio_id) REFERENCES studios(id) ON DELETE SET NULL
            )
        """)

        # 沙箱表：需要先于兼容迁移创建，否则全新 DB 在 UPDATE sandboxes 时会失败
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sandboxes (
                id TEXT PRIMARY KEY,
                owner_type TEXT DEFAULT 'task',
                owner_id TEXT,
                task_id TEXT NOT NULL,
                path TEXT NOT NULL,
                status TEXT DEFAULT 'ready',
                title TEXT DEFAULT '',
                description TEXT DEFAULT '',
                runtime_type TEXT DEFAULT 'local',
                dev_port INTEGER,
                preview_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active_at TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sandbox_runs (
                id TEXT PRIMARY KEY,
                sandbox_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                command TEXT NOT NULL,
                cwd TEXT DEFAULT '.',
                status TEXT DEFAULT 'running',
                pid INTEGER,
                exit_code INTEGER,
                stdout_path TEXT,
                stderr_path TEXT,
                preview_url TEXT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP,
                FOREIGN KEY (sandbox_id) REFERENCES sandboxes(id) ON DELETE CASCADE
            )
        """)

        # 为已存在的 DB 做向前兼容迁移（新增列）
        # 对所有表做向前兼容迁移：涵盖 CREATE TABLE 里的全部非主键列
        # try/except 确保"列已存在"时静默跳过，安全幂等
        migrations = {
            "sub_tasks": [
                ("iteration_id",     "TEXT"),
                ("studio_id",        "TEXT"),
                ("group_id",         "TEXT"),
                ("step_id",          "TEXT DEFAULT ''"),
                ("depends_on",       "TEXT DEFAULT '[]'"),
                ("step_label",       "TEXT"),
                ("assign_to_role",   "TEXT"),
                ("input_context",    "TEXT"),
                ("deliverable",      "TEXT"),
                ("blocker_reason",   "TEXT"),
                ("review_feedback",  "TEXT"),
                ("attempt_index",    "INTEGER DEFAULT 1"),
                ("retry_count",      "INTEGER DEFAULT 0"),
                ("updated_at",       "TIMESTAMP"),
                ("distilled_summary","TEXT"),
                # ── 成本 / 耗时观测字段 ──
                ("tokens",           "INTEGER DEFAULT 0"),
                ("duration_ms",      "INTEGER DEFAULT 0"),
                ("cost_usd",         "REAL DEFAULT 0"),
                ("started_at",       "TIMESTAMP"),
                ("finished_at",      "TIMESTAMP"),
                ("model_name",       "TEXT"),
                # ── 人类编辑痕迹 ──
                ("edited_by_user",   "INTEGER DEFAULT 0"),
                ("edited_at",        "TIMESTAMP"),
            ],
            "sub_agent_configs": [
                ("agent_md_path",    "TEXT"),
                ("soul_path",        "TEXT"),
                ("skills",           "TEXT DEFAULT '[]'"),
                ("is_working",       "BOOLEAN DEFAULT FALSE"),
                ("total_tokens",     "INTEGER DEFAULT 0"),
            ],
            "studios": [
                ("description",      "TEXT"),
                ("core_capabilities","TEXT DEFAULT '[]'"),
                ("recent_topics",    "TEXT DEFAULT '[]'"),
                ("user_facts",       "TEXT DEFAULT '[]'"),
                ("task_count",       "INTEGER DEFAULT 0"),
                ("is_working",       "BOOLEAN DEFAULT FALSE"),
                ("updated_at",       "TIMESTAMP"),
                ("last_active",      "TIMESTAMP"),
                ("total_tokens",     "INTEGER DEFAULT 0"),
            ],
            "tasks": [
                ("current_iteration_id",      "TEXT"),
                ("sandbox_owner_type",        "TEXT DEFAULT 'task'"),
                ("sandbox_owner_id",          "TEXT"),
                ("studio_id",                "TEXT"),
                ("updated_at",               "TIMESTAMP"),
                ("started_at",               "TIMESTAMP"),
                ("last_activity_at",         "TIMESTAMP"),
                ("completed_at",             "TIMESTAMP"),
                ("failure_reason",           "TEXT DEFAULT ''"),
                ("plan_steps",               "TEXT DEFAULT '[]'"),
                ("plan_studio_id",           "TEXT"),
                ("clarification_questions",  "TEXT DEFAULT '[]'"),  # Leader 待确认问题 JSON
                ("clarification_answers",    "TEXT DEFAULT '{}'"),  # 用户回答 JSON
                ("status_message",           "TEXT DEFAULT ''"),    # 规划/执行阶段人可读进展（与 status 正交）
            ],
            "path_nodes": [
                ("iteration_id",     "TEXT"),
                ("agent_role",       "TEXT"),
                ("step_label",       "TEXT"),
                ("input",            "TEXT"),
                ("output",           "TEXT"),
                ("distilled_summary","TEXT"),
                ("parent_id",        "TEXT"),
                ("position_x",       "REAL DEFAULT 0"),
                ("position_y",       "REAL DEFAULT 0"),
            ],
            "path_edges": [
                ("iteration_id",     "TEXT"),
            ],
            "sandboxes": [
                ("owner_type",       "TEXT DEFAULT 'task'"),
                ("owner_id",         "TEXT"),
                ("task_id",          "TEXT"),
                ("path",             "TEXT"),
                ("status",           "TEXT DEFAULT 'ready'"),
                ("title",            "TEXT DEFAULT ''"),
                ("description",      "TEXT DEFAULT ''"),
                ("runtime_type",     "TEXT DEFAULT 'local'"),
                ("dev_port",         "INTEGER"),
                ("preview_url",      "TEXT"),
                ("created_at",       "TIMESTAMP"),
                ("updated_at",       "TIMESTAMP"),
                ("last_active_at",   "TIMESTAMP"),
            ],
            "sandbox_runs": [
                ("sandbox_id",       "TEXT"),
                ("task_id",          "TEXT"),
                ("command",          "TEXT"),
                ("cwd",              "TEXT DEFAULT '.'"),
                ("status",           "TEXT DEFAULT 'running'"),
                ("pid",              "INTEGER"),
                ("exit_code",        "INTEGER"),
                ("stdout_path",      "TEXT"),
                ("stderr_path",      "TEXT"),
                ("preview_url",      "TEXT"),
                ("started_at",       "TIMESTAMP"),
                ("finished_at",      "TIMESTAMP"),
            ],
            "skill_pool": [
                # 历史 DB 先建了表但没有这两列，升级时自动补
                ("kind",   "TEXT DEFAULT 'builtin'"),
                ("config", "TEXT DEFAULT '{}'"),
            ],
        }
        for table, cols in migrations.items():
            for col, definition in cols:
                try:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
                except Exception as e:
                    # 仅吞"列已存在"这一特定错误，其余错误抛出以免掩盖 schema 损坏
                    msg = str(e).lower()
                    if "duplicate column" in msg or "already exists" in msg:
                        continue
                    # 'no such table' 也容忍：可能该表还没创建完，下一轮会再建
                    if "no such table" in msg:
                        continue
                    raise

        # SQLite 不支持 `ALTER TABLE ... ADD COLUMN ... DEFAULT CURRENT_TIMESTAMP`
        # 这类动态默认值，因此时间列统一先裸加，再在迁移后为历史数据补值。
        await db.execute("""
            UPDATE tasks
            SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP),
                last_activity_at = COALESCE(last_activity_at, updated_at, created_at, CURRENT_TIMESTAMP),
                failure_reason = COALESCE(failure_reason, ''),
                sandbox_owner_type = COALESCE(sandbox_owner_type, 'task'),
                sandbox_owner_id = COALESCE(sandbox_owner_id, id)
        """)
        await db.execute("""
            UPDATE sub_tasks
            SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
        """)
        await db.execute("""
            UPDATE studios
            SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
        """)
        await db.execute("""
            UPDATE sandboxes
            SET owner_type = COALESCE(owner_type, 'task'),
                owner_id = COALESCE(owner_id, task_id),
                updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP),
                last_active_at = COALESCE(last_active_at, updated_at, created_at, CURRENT_TIMESTAMP)
        """)

        # 公共 Skill 池表（员工可绑定的技能名单，真正的执行实现由 tools/registry.py 提供）
        # `kind` 支持 builtin（对应 server/tools 里的 Python 实现）与 http（用户自定义 HTTP 接口）
        # `config` 是按 kind 自描述的 JSON：http 时含 url/method/headers/parameters 等
        await db.execute("""
            CREATE TABLE IF NOT EXISTS skill_pool (
                slug TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                category TEXT DEFAULT '通用',
                enabled INTEGER DEFAULT 1,
                builtin INTEGER DEFAULT 0,
                kind TEXT DEFAULT 'builtin',
                config TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 画布节点表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS path_nodes (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                iteration_id TEXT,
                type TEXT NOT NULL,
                agent_role TEXT,
                step_label TEXT,
                input TEXT,
                output TEXT,
                status TEXT DEFAULT 'pending',
                distilled_summary TEXT,
                parent_id TEXT,
                position_x REAL DEFAULT 0,
                position_y REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                FOREIGN KEY (iteration_id) REFERENCES task_iterations(id) ON DELETE SET NULL,
                FOREIGN KEY (parent_id) REFERENCES path_nodes(id) ON DELETE SET NULL
            )
        """)

        # 追问记录表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS deep_dives (
                id TEXT PRIMARY KEY,
                node_id TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (node_id) REFERENCES path_nodes(id) ON DELETE CASCADE
            )
        """)

        # 路径边表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS path_edges (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                iteration_id TEXT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                type TEXT DEFAULT 'main',
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                FOREIGN KEY (iteration_id) REFERENCES task_iterations(id) ON DELETE SET NULL,
                FOREIGN KEY (source_id) REFERENCES path_nodes(id) ON DELETE CASCADE,
                FOREIGN KEY (target_id) REFERENCES path_nodes(id) ON DELETE CASCADE
            )
        """)

        # 定时任务表（Scheduler 用）
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id TEXT PRIMARY KEY,
                name TEXT,
                message TEXT NOT NULL,
                enabled BOOLEAN DEFAULT 1,
                schedule_kind TEXT NOT NULL,
                at_time TIMESTAMP,
                every_seconds INTEGER,
                cron_expr TEXT,
                timezone TEXT,
                target_studio_id TEXT,
                approval_policy TEXT DEFAULT 'auto_execute',
                overlap_policy TEXT DEFAULT 'skip',
                delete_after_run BOOLEAN DEFAULT 0,
                next_run_at TIMESTAMP,
                last_run_at TIMESTAMP,
                last_status TEXT,
                last_error TEXT,
                created_by TEXT DEFAULT 'agent',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_job_runs (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                task_id TEXT,
                status TEXT DEFAULT 'running',
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP,
                error TEXT,
                FOREIGN KEY (job_id) REFERENCES scheduled_jobs(id) ON DELETE CASCADE
            )
        """)

        # 批注表（任务结果页的文字批注 + AI 对话）
        await db.execute("""
            CREATE TABLE IF NOT EXISTS annotations (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                selected_text TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                FOREIGN KEY (node_id) REFERENCES path_nodes(id) ON DELETE CASCADE
            )
        """)

        # ── 关键索引（幂等） ─────────────────────────────────────────────────
        for ddl in [
            "CREATE INDEX IF NOT EXISTS idx_sub_tasks_task_id ON sub_tasks(task_id)",
            "CREATE INDEX IF NOT EXISTS idx_sub_tasks_iteration_id ON sub_tasks(iteration_id)",
            "CREATE INDEX IF NOT EXISTS idx_sub_tasks_studio_id ON sub_tasks(studio_id)",
            "CREATE INDEX IF NOT EXISTS idx_path_nodes_task_id ON path_nodes(task_id)",
            "CREATE INDEX IF NOT EXISTS idx_path_nodes_iteration_id ON path_nodes(iteration_id)",
            "CREATE INDEX IF NOT EXISTS idx_path_edges_task_id ON path_edges(task_id)",
            "CREATE INDEX IF NOT EXISTS idx_path_edges_iteration_id ON path_edges(iteration_id)",
            "CREATE INDEX IF NOT EXISTS idx_task_iterations_task_id ON task_iterations(task_id)",
            "CREATE INDEX IF NOT EXISTS idx_annotations_task_id ON annotations(task_id)",
            "CREATE INDEX IF NOT EXISTS idx_annotations_node_id ON annotations(node_id)",
            "CREATE INDEX IF NOT EXISTS idx_deep_dives_node_id ON deep_dives(node_id)",
            "CREATE INDEX IF NOT EXISTS idx_sub_agents_studio_id ON sub_agent_configs(studio_id)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_studio_id ON tasks(studio_id)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
            "CREATE INDEX IF NOT EXISTS idx_sch_runs_job_id ON scheduled_job_runs(job_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_sandboxes_owner ON sandboxes(owner_type, owner_id)",
            "CREATE INDEX IF NOT EXISTS idx_sandboxes_task_id ON sandboxes(task_id)",
            "CREATE INDEX IF NOT EXISTS idx_sandbox_runs_sandbox_id ON sandbox_runs(sandbox_id)",
        ]:
            try:
                await db.execute(ddl)
            except Exception:
                pass

        # ── 为历史任务补一个默认 iteration，并把旧节点/边/子任务归属过去 ───────
        await db.execute("""
            INSERT OR IGNORE INTO task_iterations
                (id, task_id, title, instruction, status, plan_steps, plan_studio_id,
                 created_at, updated_at, started_at, completed_at)
            SELECT
                'it_' || id || '_0',
                id,
                '初始执行',
                question,
                status,
                COALESCE(plan_steps, '[]'),
                plan_studio_id,
                COALESCE(created_at, CURRENT_TIMESTAMP),
                COALESCE(updated_at, created_at, CURRENT_TIMESTAMP),
                started_at,
                completed_at
            FROM tasks
        """)
        await db.execute("""
            UPDATE tasks
            SET current_iteration_id = COALESCE(current_iteration_id, 'it_' || id || '_0')
        """)
        await db.execute("""
            UPDATE path_nodes
            SET iteration_id = COALESCE(iteration_id, 'it_' || task_id || '_0')
        """)
        await db.execute("""
            UPDATE path_edges
            SET iteration_id = COALESCE(iteration_id, 'it_' || task_id || '_0')
        """)
        await db.execute("""
            UPDATE sub_tasks
            SET iteration_id = COALESCE(iteration_id, 'it_' || task_id || '_0')
        """)

        # ── Studio 0 默认工作室（Agent Zero 直答） ────────────────────────────
        await db.execute("""
            INSERT OR IGNORE INTO studios (id, scenario, description, core_capabilities)
            VALUES (
                'studio_0',
                'Agent Zero 直答',
                '由 0 号 Agent 直接处理的简单问题与快速解答',
                '["快速问答","知识检索","简单计算","信息总结"]'
            )
        """)
        await db.execute("""
            INSERT OR IGNORE INTO sub_agent_configs (id, studio_id, role, skills)
            VALUES (
                'agent_zero_sa',
                'studio_0',
                'Agent Zero',
                '["knowledge_qa","summarization","reasoning"]'
            )
        """)
        await db.execute("""
            INSERT OR IGNORE INTO sub_agent_configs (id, studio_id, role, skills)
            VALUES (
                'hr_agent_sa',
                'studio_0',
                'HR 招聘专员',
                '["web_search","write_file"]'
            )
        """)
        # Skill 工程师：负责处理 "帮我做一个 / 找一个 / 加载一个 skill" 类任务
        # 同时带 write_file / execute_code 便于生成 SKILL.md 之外的辅助脚本
        await db.execute("""
            INSERT OR IGNORE INTO sub_agent_configs (id, studio_id, role, skills)
            VALUES (
                'skill_engineer_sa',
                'studio_0',
                'Skill 工程师',
                '["skill_creator","find_skill","use_skill","web_search","write_file","execute_code"]'
            )
        """)
        cursor = await db.execute(
            "SELECT skills FROM sub_agent_configs WHERE id = 'agent_zero_sa'"
        )
        row = await cursor.fetchone()
        if row:
            try:
                skills = json.loads(row["skills"] or "[]")
            except Exception:
                skills = []
            for slug in ("web_search", "execute_code", "read_file", "write_file", "list_files", "schedule_task"):
                if slug not in skills:
                    skills.append(slug)
            await db.execute(
                "UPDATE sub_agent_configs SET role = '系统管理员', skills = ? WHERE id = 'agent_zero_sa'",
                (json.dumps(skills, ensure_ascii=False),),
            )

        # ── Seed 内置 Skill 池 ──────────────────────────────────────────────
        # 注意：这里用 INSERT OR IGNORE，builtin 标记为 1，**不会覆盖**用户在 UI 上
        from storage.skill_store import DEFAULT_SKILLS  # 延迟导入避免循环
        for skill in DEFAULT_SKILLS:
            await db.execute(
                """INSERT OR IGNORE INTO skill_pool
                   (slug, name, description, category, enabled, builtin)
                   VALUES (?, ?, ?, ?, 1, 1)""",
                (
                    skill["slug"],
                    skill["name"],
                    skill.get("description", ""),
                    skill.get("category", "通用"),
                ),
            )

        await db.commit()
        logger.info(f"Database initialized: {DB_PATH}")
