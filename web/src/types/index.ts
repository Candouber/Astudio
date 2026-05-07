// src/types/index.ts

export interface StudioCard {
  description: string;
  core_capabilities: string[];
  recent_topics: string[];
  user_facts: string[];
  task_count: number;
  last_active?: string;
}

export interface SubAgentConfig {
  id: string;
  role: string;
  agent_md: string;
  soul: string;
  skills: string[];
  is_working: boolean;
  total_tokens: number;
}

export interface Studio {
  id: string;
  scenario: string;
  is_working: boolean;
  total_tokens: number;
  sub_agents: SubAgentConfig[];
  card: StudioCard;
  created_at: string;
  updated_at: string;
}

export interface DeepDive {
  id: string;
  question: string;
  answer: string;
  created_at: string;
}

export interface PathNode {
  id: string;
  iteration_id?: string | null;
  type: 'agent_zero' | 'sub_agent' | 'user_intervention' | 'diverge';
  agent_role: string;
  step_label: string;
  input: string;
  output: string;
  status: 'pending' | 'running' | 'completed' | 'error' | 'corrected' | 'deprecated';
  deep_dives: DeepDive[];
  distilled_summary: string;
  parent_id?: string;
  position: { x: number; y: number };
}

export interface PathEdge {
  id: string;
  iteration_id?: string | null;
  source: string;
  target: string;
  type: 'main' | 'correction' | 'diverge';
}

export type TaskStatus =
  | 'planning'
  | 'need_clarification'
  | 'await_leader_plan_approval'
  | 'executing'
  | 'terminated'
  | 'completed'
  | 'completed_with_blockers'
  | 'timeout_killed'
  | 'failed';

export interface SubTask {
  id: string;
  task_id: string;
  iteration_id?: string | null;
  studio_id?: string;
  group_id?: string;
  step_id: string;
  depends_on: string[];
  step_label: string;
  assign_to_role: string;
  input_context: string;
  status: 'pending' | 'running' | 'pending_review' | 'revision_requested' | 'accepted' | 'blocked';
  deliverable?: string;
  blocker_reason?: string;
  review_feedback?: string;
  distilled_summary?: string;
  attempt_index: number;
  retry_count: number;
  created_at: string;
  updated_at: string;
  // 成本 / 耗时观测
  tokens?: number;
  duration_ms?: number;
  cost_usd?: number;
  started_at?: string | null;
  finished_at?: string | null;
  model_name?: string | null;
  // 人类干预痕迹
  edited_by_user?: boolean;
  edited_at?: string | null;
}

export interface ClarificationQuestion {
  id: string;
  question: string;
}

export interface Task {
  id: string;
  current_iteration_id?: string | null;
  sandbox_owner_type?: string;
  sandbox_owner_id?: string | null;
  studio_id?: string;
  question: string;
  nodes: PathNode[];
  edges: PathEdge[];
  sub_tasks: SubTask[];
  iterations?: TaskIteration[];
  plan_steps: PlanStep[];
  plan_studio_id?: string;
  clarification_questions: ClarificationQuestion[];
  clarification_answers: Record<string, string>;
  status: TaskStatus;
  /** 与 status 正交的进展文案，来自后端轮询/列表 */
  status_message?: string;
  created_at: string;
  updated_at?: string;
  started_at?: string | null;
  last_activity_at?: string | null;
  completed_at?: string;
  failure_reason?: string;
}

export interface TaskIteration {
  id: string;
  task_id: string;
  parent_iteration_id?: string | null;
  source_node_id?: string | null;
  title: string;
  instruction: string;
  status: TaskStatus;
  plan_steps: PlanStep[];
  plan_studio_id?: string | null;
  summary?: string;
  created_at: string;
  updated_at?: string;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface TaskAttachment {
  id: string;
  filename: string;
  content_type?: string;
  extension?: string;
  size: number;
  path: string;
  summary?: string;
}

export interface PlanStep {
  id: string;
  step_label: string;
  assign_to_role: string;
  input_context: string;
  depends_on: string[];
}

export interface LLMProvider {
  name: string;
  api_key: string | null;
  endpoint: string | null;
  models: string[];
  model_aliases?: Record<string, string>;
  model_display_names?: Record<string, string>;
  is_oauth?: boolean;
  /** 与 LiteLLM 路由一致（如 openai）；OpenAI 兼容网关可填 openai 而与 name 区分开 */
  litellm_provider?: string | null;
}

export interface ModelAssignment {
  agent_zero: RoleModelConfig;
  sub_agents: RoleModelConfig;
  distillation: RoleModelConfig;
}

export type ReasoningEffort =
  | 'default'
  | 'none'
  | 'minimal'
  | 'low'
  | 'medium'
  | 'high'
  | 'xhigh'

export type ThinkingType = 'default' | 'disabled' | 'enabled' | 'adaptive'

export interface RoleModelConfig {
  model: string;
  reasoning_effort?: ReasoningEffort | null;
  thinking_type?: ThinkingType | null;
  thinking_budget_tokens?: number | null;
}

export type ModelCapabilitySupport = 'yes' | 'no' | 'partial' | 'unknown'

export interface ModelCapabilities {
  model: string
  supports_tools: ModelCapabilitySupport
  supports_tool_choice: ModelCapabilitySupport
  supports_reasoning_effort: ModelCapabilitySupport
  supports_thinking: ModelCapabilitySupport
  execution_agent_compatible: boolean
  warnings: string[]
}

export interface Annotation {
  id: string
  task_id: string
  node_id: string
  selected_text: string
  question: string
  answer: string
  created_at: string
}

export interface AppConfig {
  llm_providers: LLMProvider[];
  model_assignment: ModelAssignment;
}

// ── Sandbox ────────────────────────────────────────────────────────────────
export type SandboxStatus = 'ready' | 'running' | 'stopped' | 'error';
export type SandboxRunStatus = 'running' | 'ok' | 'error' | 'stopped';

export interface Sandbox {
  id: string;
  owner_type?: string;
  owner_id?: string;
  task_id: string;
  path: string;
  status: SandboxStatus;
  title: string;
  description: string;
  runtime_type: string;
  dev_port?: number | null;
  preview_url?: string | null;
  created_at: string;
  updated_at: string;
  last_active_at?: string | null;
}

export interface SandboxFile {
  name: string;
  path: string;
  kind: 'file' | 'directory';
  size: number;
  updated_at?: string | null;
}

export interface SandboxRun {
  id: string;
  sandbox_id: string;
  task_id: string;
  command: string;
  cwd: string;
  status: SandboxRunStatus;
  pid?: number | null;
  exit_code?: number | null;
  stdout_path?: string | null;
  stderr_path?: string | null;
  preview_url?: string | null;
  started_at: string;
  finished_at?: string | null;
}

// ── Schedule ───────────────────────────────────────────────────────────────
export type ScheduleKind = 'at' | 'every' | 'cron';
export type ScheduleApprovalPolicy = 'auto_execute' | 'require_plan_review';
export type ScheduleOverlapPolicy = 'skip' | 'queue';
export type ScheduleRunStatus = 'running' | 'ok' | 'error' | 'skipped';

export interface ScheduledJob {
  id: string;
  name: string;
  message: string;
  enabled: boolean;
  schedule_kind: ScheduleKind;
  at_time?: string | null;
  every_seconds?: number | null;
  cron_expr?: string | null;
  timezone?: string | null;
  target_studio_id?: string | null;
  approval_policy: ScheduleApprovalPolicy;
  overlap_policy: ScheduleOverlapPolicy;
  delete_after_run: boolean;
  created_by: string;
  next_run_at?: string | null;
  last_run_at?: string | null;
  last_status?: string | null;
  last_error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScheduledJobCreate {
  name?: string;
  message: string;
  enabled?: boolean;
  schedule_kind: ScheduleKind;
  at_time?: string | null;
  every_seconds?: number | null;
  cron_expr?: string | null;
  timezone?: string | null;
  target_studio_id?: string | null;
  approval_policy?: ScheduleApprovalPolicy;
  overlap_policy?: ScheduleOverlapPolicy;
  delete_after_run?: boolean;
  created_by?: string;
}

export interface ScheduledJobUpdate {
  name?: string;
  message?: string;
  enabled?: boolean;
  schedule_kind?: ScheduleKind;
  at_time?: string | null;
  every_seconds?: number | null;
  cron_expr?: string | null;
  timezone?: string | null;
  target_studio_id?: string | null;
  approval_policy?: ScheduleApprovalPolicy;
  overlap_policy?: ScheduleOverlapPolicy;
  delete_after_run?: boolean;
}

export interface ScheduledRunResult {
  run_id: string;
  job_id: string;
  task_id?: string | null;
  run_status: ScheduleRunStatus;
  started_at: string;
  finished_at?: string | null;
  run_error?: string | null;
  job_name: string;
  job_message: string;
  schedule_kind: ScheduleKind;
  cron_expr?: string | null;
  every_seconds?: number | null;
  timezone?: string | null;
  task_status?: string | null;
  task_question?: string | null;
  task_completed_at?: string | null;
  result_excerpt?: string | null;
}

// ── Skill Pool ─────────────────────────────────────────────────────────────
export type SkillKind = 'builtin' | 'bundle';

export type SkillProvider = 'clawhub' | 'skillhub_cn' | 'local' | 'github';

export interface BundleSkillSource {
  provider: SkillProvider;
  url?: string | null;
  username?: string | null;
  slug?: string | null;
  version?: string | null;
  source_type?: string | null;
  source_identifier?: string | null;
  default_branch?: string | null;
  skill_path?: string | null;
}

export interface BundleSkillConfig {
  source: BundleSkillSource;
  local_dir: string;
  summary?: string;
  files?: string[];
}

export interface SkillPoolItem {
  slug: string;
  name: string;
  description: string;
  category: string;
  enabled: boolean;
  builtin: boolean;
  kind: SkillKind;
  // kind=builtin 时为 {}，kind=bundle 时是 BundleSkillConfig 的序列化
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SkillPoolUpdate {
  name?: string;
  description?: string;
  category?: string;
  enabled?: boolean;
}

export interface SkillImportRequest {
  url: string;
  override_slug?: string | null;
  category?: string;
}

export interface SkillAiCreateRequest {
  slug: string;
  name: string;
  goal: string;
  category?: string;
}

export interface SkillAiCreateResponse {
  skill: SkillPoolItem | null;
  message: string;
}

export interface SkillProbeResult {
  provider: 'clawhub' | 'skillhub_cn';
  username: string | null;
  slug: string;
  suggested_slug: string;
}

export interface SkillMdPayload {
  slug: string;
  local_dir: string;
  content: string;
  files: string[];
}
