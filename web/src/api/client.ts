import axios from 'axios'
import type {
  Studio,
  Task,
  AppConfig,
  Annotation,
  Sandbox,
  SandboxFile,
  SandboxRun,
  ScheduledJob,
  ScheduledJobCreate,
  ScheduledJobUpdate,
  ScheduledRunResult,
  SkillPoolItem,
  SkillPoolUpdate,
  SkillImportRequest,
  SkillAiCreateRequest,
  SkillAiCreateResponse,
  SkillProbeResult,
  SkillMdPayload,
  TaskAttachment,
  ModelCapabilities,
} from '../types'

const httpClient = axios.create({
  baseURL: '/api',
})

export type OAuthStatus = 'not_started' | 'pending' | 'authenticated' | 'failed'

export interface OAuthStatusResponse {
  status: OAuthStatus
  account_id: string | null
  error: string | null
  auth_url?: string | null
}

export const api = {
  // Config
  getConfig: () => httpClient.get<AppConfig>('/config/').then(res => res.data),
  updateConfig: (config: AppConfig) => httpClient.put<AppConfig>('/config/', config).then(res => res.data),
  testProvider: (payload: {
    name: string;
    api_key?: string | null;
    endpoint?: string | null;
    test_model: string;
    litellm_provider?: string | null;
    model_aliases?: Record<string, string>;
  }) =>
    httpClient.post<{
      success: boolean;
      message: string;
      reply?: string;
      model?: string;
      capabilities?: ModelCapabilities;
    }>('/config/test', payload).then(res => res.data),
  getModelCapabilities: (model: string) =>
    httpClient.post<ModelCapabilities>('/config/model-capabilities', { model }).then(res => res.data),

  // OAuth
  initiateOAuth: (providerName: string) =>
    httpClient.post<{ status: string; message: string }>(`/config/oauth/${providerName}/initiate`).then(res => res.data),
  getOAuthStatus: (providerName: string) =>
    httpClient.get<OAuthStatusResponse>(`/config/oauth/${providerName}/status`).then(res => res.data),
  revokeOAuth: (providerName: string) =>
    httpClient.delete<{ success: boolean; message: string }>(`/config/oauth/${providerName}/revoke`).then(res => res.data),
  submitOAuthCallback: (providerName: string, url: string) =>
    httpClient.post<{ success: boolean; message: string }>(`/config/oauth/${providerName}/callback`, { url }).then(res => res.data),

  // Studios
  getStudios: () => httpClient.get<Studio[]>('/studios/').then(res => res.data),
  getStudio: (id: string) => httpClient.get<Studio>(`/studios/${id}`).then(res => res.data),
  getStudioTasks: (id: string) => httpClient.get<Task[]>(`/studios/${id}/tasks`).then(res => res.data),
  deleteStudio: (id: string) => httpClient.delete(`/studios/${id}`),

  // Tasks
  /** 创建任务并立即返回 task_id；路由与 Leader 规划在后台继续。 */
  postAsk: (question: string) =>
    httpClient
      .post<{ task_id: string; status: string }>('/tasks/ask', { question })
      .then((res) => res.data),
  postAskWithAttachments: (question: string, files: File[]) => {
    const form = new FormData()
    form.append('question', question)
    files.forEach(file => form.append('files', file))
    return httpClient
      .post<{ task_id: string; status: string; attachments: TaskAttachment[] }>('/tasks/ask-with-attachments', form)
      .then((res) => res.data)
  },
  listTasks: () => httpClient.get<Task[]>('/tasks/').then(res => res.data),
  getTask: (id: string) => httpClient.get<Task>(`/tasks/${id}`).then(res => res.data),
  deleteTask: (id: string) => httpClient.delete(`/tasks/${id}`),
  terminateTask: (id: string) =>
    httpClient.post<{ status: string; message: string }>(`/tasks/${id}/terminate`).then(res => res.data),
  proceedTask: (taskId: string, payload: { route_cmd?: Record<string, unknown>; feedback?: string }) =>
    httpClient.post<{ status: string; message: string }>(`/tasks/${taskId}/proceed`, payload).then(res => res.data),
  rerunOriginal: (taskId: string) =>
    httpClient.post<{ status: string; message: string }>(`/tasks/${taskId}/rerun-original`).then(res => res.data),

  // Annotations
  listAnnotations: (taskId: string) =>
    httpClient.get<Annotation[]>(`/tasks/${taskId}/annotations`).then(res => res.data),
  deleteAnnotation: (taskId: string, annId: string) =>
    httpClient.delete(`/tasks/${taskId}/annotations/${annId}`),

  // Studio members
  addMember: (studioId: string, payload: { role: string; skills: string[]; agent_md?: string }) =>
    httpClient.post<Studio>(`/studios/${studioId}/members`, payload).then(res => res.data),
  updateMember: (
    studioId: string,
    memberId: string,
    payload: { role?: string; skills?: string[]; agent_md?: string; soul?: string },
  ) =>
    httpClient.put<Studio>(`/studios/${studioId}/members/${memberId}`, payload).then(res => res.data),
  deleteMember: (studioId: string, memberId: string) =>
    httpClient.delete(`/studios/${studioId}/members/${memberId}`),

  // Sandboxes
  listSandboxes: () =>
    httpClient.get<Sandbox[]>('/sandboxes/').then(res => res.data),
  ensureTaskSandbox: (taskId: string) =>
    httpClient
      .post<{ sandbox: Sandbox; created: boolean }>(`/sandboxes/tasks/${taskId}`)
      .then(res => res.data),
  getTaskSandbox: (taskId: string) =>
    httpClient.get<Sandbox>(`/sandboxes/tasks/${taskId}/current`).then(res => res.data),
  getSandbox: (id: string) =>
    httpClient.get<Sandbox>(`/sandboxes/${id}`).then(res => res.data),
  deleteSandbox: (id: string, deleteFiles: boolean = true) =>
    httpClient.delete(`/sandboxes/${id}`, { params: { delete_files: deleteFiles } }),
  getSandboxStartCommand: (id: string) =>
    httpClient
      .get<{ command?: string; cwd?: string; source?: string; message?: string }>(
        `/sandboxes/${id}/start-command`,
      )
      .then(res => res.data),
  listSandboxFiles: (id: string, directory: string = '.') =>
    httpClient
      .get<SandboxFile[]>(`/sandboxes/${id}/files`, { params: { directory } })
      .then(res => res.data),
  readSandboxFile: (id: string, path: string) =>
    httpClient
      .get<{ path: string; content: string }>(`/sandboxes/${id}/files/read`, { params: { path } })
      .then(res => res.data),
  writeSandboxFile: (id: string, path: string, content: string) =>
    httpClient
      .put<{ path: string; size: number }>(`/sandboxes/${id}/files/write`, { path, content })
      .then(res => res.data),
  runSandboxCommand: (
    id: string,
    payload: { command: string; cwd?: string; background?: boolean; timeout_seconds?: number },
  ) => httpClient.post<SandboxRun>(`/sandboxes/${id}/run`, payload).then(res => res.data),
  stopSandbox: (id: string) =>
    httpClient.post<{ status: string; stopped: number }>(`/sandboxes/${id}/stop`).then(res => res.data),
  listSandboxRuns: (id: string) =>
    httpClient.get<SandboxRun[]>(`/sandboxes/${id}/runs`).then(res => res.data),
  getSandboxRunLogs: (id: string, runId: string) =>
    httpClient
      .get<{ stdout: string; stderr: string }>(`/sandboxes/${id}/runs/${runId}/logs`)
      .then(res => res.data),
  startSandboxPreview: (id: string) =>
    httpClient.post<{ preview_url: string }>(`/sandboxes/${id}/preview`).then(res => res.data),

  // Schedules
  listSchedules: () =>
    httpClient.get<ScheduledJob[]>('/schedules/').then(res => res.data),
  getSchedule: (id: string) =>
    httpClient.get<ScheduledJob>(`/schedules/${id}`).then(res => res.data),
  createSchedule: (payload: ScheduledJobCreate) =>
    httpClient.post<ScheduledJob>('/schedules/', payload).then(res => res.data),
  updateSchedule: (id: string, payload: ScheduledJobUpdate) =>
    httpClient.put<ScheduledJob>(`/schedules/${id}`, payload).then(res => res.data),
  deleteSchedule: (id: string) =>
    httpClient.delete(`/schedules/${id}`),
  runScheduleNow: (id: string) =>
    httpClient.post<{ status: string }>(`/schedules/${id}/run-now`).then(res => res.data),
  listScheduleRunResults: (params: { limit?: number; job_id?: string | null } = {}) =>
    httpClient
      .get<ScheduledRunResult[]>('/schedules/runs/results', { params })
      .then(res => res.data),

  // Skill pool
  listSkills: (includeDisabled: boolean = true) =>
    httpClient
      .get<SkillPoolItem[]>('/skills/', { params: { include_disabled: includeDisabled } })
      .then(res => res.data),
  probeSkill: (url: string) =>
    httpClient.post<SkillProbeResult>('/skills/probe', { url }).then(res => res.data),
  importSkill: (payload: SkillImportRequest) =>
    httpClient.post<SkillPoolItem>('/skills/import', payload).then(res => res.data),
  aiCreateSkill: (payload: SkillAiCreateRequest) =>
    httpClient.post<SkillAiCreateResponse>('/skills/ai-create', payload).then(res => res.data),
  refreshSkill: (slug: string) =>
    httpClient.post<SkillPoolItem>(`/skills/${slug}/refresh`).then(res => res.data),
  getSkillMd: (slug: string) =>
    httpClient.get<SkillMdPayload>(`/skills/${slug}/skill-md`).then(res => res.data),
  updateSkill: (slug: string, payload: SkillPoolUpdate) =>
    httpClient.put<SkillPoolItem>(`/skills/${slug}`, payload).then(res => res.data),
  deleteSkill: (slug: string) =>
    httpClient.delete(`/skills/${slug}`),
}
