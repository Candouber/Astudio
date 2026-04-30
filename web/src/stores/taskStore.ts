import { create } from 'zustand'
import type { PathNode, PathEdge, Task, PlanStep, TaskStatus, ClarificationQuestion } from '../types'
import { api } from '../api/client'
import { translate } from '../i18n/t'
import { useChatStore } from './chatStore'
import { useLocaleStore } from './localeStore'

const ACTIVE_TASK_STATUSES = new Set<TaskStatus>(['planning', 'executing'])

function syncChatTaskStatus(
  taskId: string,
  status: TaskStatus,
  statusMessage?: string | null,
) {
  const isActive = ACTIVE_TASK_STATUSES.has(status)
  useChatStore.getState().updateTaskMessage(taskId, {
    taskStatus: status,
    isStreaming: isActive,
    thinkingText: isActive ? (statusMessage || '') : '',
  })
}

function parseTimestamp(value?: string | null): number | null {
  if (!value) return null
  const ts = Date.parse(value)
  return Number.isFinite(ts) ? ts : null
}

function buildNodeActivityFromTask(task: Task) {
  const activity: Record<string, { startedAt: number; lastUpdatedAt: number; latestMessage: string }> = {}
  const taskFallbackTs =
    parseTimestamp(task.last_activity_at) ??
    parseTimestamp(task.updated_at) ??
    parseTimestamp(task.created_at) ??
    Date.now()

  for (const st of task.sub_tasks || []) {
    const nodeId = st.group_id
    if (!nodeId) continue

    const node = task.nodes.find(n => n.id === nodeId)
    if (!node || node.status !== 'running') continue

    const startedAt =
      parseTimestamp(st.started_at) ??
      parseTimestamp(st.created_at) ??
      taskFallbackTs
    const lastUpdatedAt =
      parseTimestamp(st.updated_at) ??
      parseTimestamp(st.finished_at) ??
      taskFallbackTs

    activity[nodeId] = {
      startedAt,
      lastUpdatedAt,
      latestMessage: node.output || '',
    }
  }

  return activity
}

interface TaskState {
  tasks: Task[]
  currentTask: Task | null
  expectedTaskId: string | null
  nodes: PathNode[]
  edges: PathEdge[]
  selectedNodeId: string | null
  isExecuting: boolean
  statusMessage: string
  streamBuffers: Record<string, string>
  nodeActivity: Record<string, { startedAt: number; lastUpdatedAt: number; latestMessage: string }>
  lastHeartbeat: number
  planSteps: PlanStep[]
  planStudioId: string
  annotations: Record<string, string>
  clarificationQuestions: ClarificationQuestion[]
  clarificationTaskId: string
  clarificationStudioId: string

  fetchTasks: () => Promise<void>
  deleteTask: (taskId: string) => Promise<void>
  setTask: (task: Task) => void
  setExpectedTaskId: (taskId: string | null) => void
  fetchTask: (taskId: string) => Promise<void>
  updateTaskStatus: (status: TaskStatus) => void
  addNode: (node: PathNode) => void
  addEdge: (edge: PathEdge) => void
  updateNodeStatus: (nodeId: string, status: PathNode['status'], output?: string) => void
  updateNodeSummary: (nodeId: string, summary: string) => void
  appendStreamChunk: (nodeId: string, chunk: string) => void
  selectNode: (nodeId: string | null) => void
  markNodeActivity: (nodeId: string, message: string) => void
  markHeartbeat: (tsMs: number) => void
  setExecuting: (executing: boolean) => void
  setStatus: (message: string) => void
  setPlanSteps: (steps: PlanStep[], studioId: string) => void
  setAnnotation: (stepId: string, text: string) => void
  clearPlan: () => void
  setClarificationQuestions: (taskId: string, questions: ClarificationQuestion[], studioId: string) => void
  clearClarification: () => void
  resetCurrent: () => void
}

export const useTaskStore = create<TaskState>((set, get) => ({
  tasks: [],
  currentTask: null,
  expectedTaskId: null,
  nodes: [],
  edges: [],
  selectedNodeId: null,
  isExecuting: false,
  statusMessage: '',
  streamBuffers: {},
  nodeActivity: {},
  lastHeartbeat: 0,
  planSteps: [],
  planStudioId: '',
  annotations: {},
  clarificationQuestions: [],
  clarificationTaskId: '',
  clarificationStudioId: '',

  fetchTasks: async () => {
    try {
      const tasks = await api.listTasks()
      set({ tasks })
    } catch (err) {
      console.error('Failed to fetch tasks:', err)
    }
  },

  deleteTask: async (taskId) => {
    try {
      await api.deleteTask(taskId)
      set(s => ({ tasks: s.tasks.filter(t => t.id !== taskId) }))
      const fresh = await api.listTasks()
      set({ tasks: fresh })
    } catch (err: unknown) {
      console.error('Failed to delete task:', err)
      try { const tasks = await api.listTasks(); set({ tasks }) } catch { /* ignore */ }
      const loc = useLocaleStore.getState().locale
      const msg =
        err instanceof Error ? err.message : translate(loc, 'errors.deleteTaskFailed')
      alert(`⚠️ ${msg}`)
    }
  },

  setTask: (task) => {
    const expected = get().expectedTaskId
    if (expected && expected !== task.id) return // 串台保护
    const isActive = task.status === 'planning' || task.status === 'executing'
    const rebuiltActivity = buildNodeActivityFromTask(task)
    syncChatTaskStatus(task.id, task.status, task.status_message)
    set({
      currentTask: task,
      nodes: task.nodes,
      edges: task.edges,
      statusMessage: task.status_message || '',
      nodeActivity: rebuiltActivity,
      ...(isActive ? {} : { lastHeartbeat: 0, isExecuting: false }),
    })
  },

  setExpectedTaskId: (taskId) => set({ expectedTaskId: taskId }),

  fetchTask: async (taskId) => {
    try {
      const task = await api.getTask(taskId)
      if (get().expectedTaskId && get().expectedTaskId !== taskId) return
      const hasSteps =
        task.status === 'await_leader_plan_approval' &&
        (task.plan_steps?.length ?? 0) > 0
      const hasClarification =
        task.status === 'need_clarification' &&
        (task.clarification_questions?.length ?? 0) > 0
      const rebuiltActivity = buildNodeActivityFromTask(task)
      syncChatTaskStatus(task.id, task.status, task.status_message)
      set({
        currentTask: task,
        nodes: task.nodes,
        edges: task.edges,
        statusMessage: task.status_message || '',
        nodeActivity: rebuiltActivity,
        ...((task.status === 'planning' || task.status === 'executing')
          ? {}
          : { lastHeartbeat: 0, isExecuting: false }),
        ...(hasSteps
          ? {
              planSteps: task.plan_steps,
              planStudioId: task.plan_studio_id || task.studio_id || '',
            }
          : {}),
        ...(hasClarification
          ? {
              clarificationQuestions: task.clarification_questions,
              clarificationTaskId: task.id,
              clarificationStudioId: task.plan_studio_id || task.studio_id || '',
            }
          : {}),
      })
    } catch (err: unknown) {
      console.error('Failed to fetch task:', err)
      if (get().expectedTaskId === taskId) {
        set({
          currentTask: {
            id: taskId,
            question: '',
            nodes: [],
            edges: [],
            sub_tasks: [],
            plan_steps: [],
            clarification_questions: [],
            clarification_answers: {},
            status: 'failed',
            created_at: new Date().toISOString(),
          } as Task,
        })
      }
    }
  },

  updateTaskStatus: (status) => {
    const task = get().currentTask
    if (task) syncChatTaskStatus(task.id, status, task.status_message)
    set((state) => {
      const isActive = status === 'planning' || status === 'executing'
      return {
        currentTask: state.currentTask
          ? { ...state.currentTask, status }
          : null,
        ...(isActive ? {} : { lastHeartbeat: 0, isExecuting: false }),
      }
    })
  },

  addNode: (node) => set((state) => ({
    nodes: [...state.nodes, node],
  })),

  addEdge: (edge) => set((state) => ({
    edges: [...state.edges, edge],
  })),

  updateNodeStatus: (nodeId, status, output) => set((state) => ({
    nodes: state.nodes.map(n =>
      n.id === nodeId
        ? { ...n, status, ...(output !== undefined ? { output } : {}) }
        : n
    ),
  })),

  updateNodeSummary: (nodeId, summary) => set((state) => ({
    nodes: state.nodes.map(n =>
      n.id === nodeId ? { ...n, distilled_summary: summary } : n
    ),
  })),

  appendStreamChunk: (nodeId, chunk) => set((state) => ({
    streamBuffers: {
      ...state.streamBuffers,
      [nodeId]: (state.streamBuffers[nodeId] || '') + chunk,
    },
  })),

  selectNode: (nodeId) => set({ selectedNodeId: nodeId }),

  markNodeActivity: (nodeId, message) => set((state) => {
    const now = Date.now()
    const prev = state.nodeActivity[nodeId]
    return {
      nodeActivity: {
        ...state.nodeActivity,
        [nodeId]: {
          startedAt: prev?.startedAt ?? now,
          lastUpdatedAt: now,
          latestMessage: message || prev?.latestMessage || '',
        },
      },
    }
  }),

  markHeartbeat: (tsMs) => set({ lastHeartbeat: tsMs }),

  setExecuting: (executing) => set({ isExecuting: executing }),
  setStatus: (message) => {
    const task = get().currentTask
    if (task) syncChatTaskStatus(task.id, task.status, message)
    set((state) => ({
      statusMessage: message,
      currentTask: state.currentTask
        ? { ...state.currentTask, status_message: message }
        : null,
    }))
  },

  setPlanSteps: (steps, studioId) => set({
    planSteps: steps,
    planStudioId: studioId,
  }),

  setAnnotation: (stepId, text) => set((state) => ({
    annotations: { ...state.annotations, [stepId]: text },
  })),

  clearPlan: () => set({
    planSteps: [],
    planStudioId: '',
    annotations: {},
  }),

  setClarificationQuestions: (taskId, questions, studioId) => set({
    clarificationQuestions: questions,
    clarificationTaskId: taskId,
    clarificationStudioId: studioId,
  }),
  clearClarification: () => set({
    clarificationQuestions: [],
    clarificationTaskId: '',
    clarificationStudioId: '',
  }),

  resetCurrent: () => set({
    currentTask: null,
    expectedTaskId: null,
    nodes: [],
    edges: [],
    selectedNodeId: null,
    isExecuting: false,
    statusMessage: '',
    streamBuffers: {},
    nodeActivity: {},
    lastHeartbeat: 0,
    planSteps: [],
    planStudioId: '',
    annotations: {},
    clarificationQuestions: [],
    clarificationTaskId: '',
    clarificationStudioId: '',
  }),
}))
