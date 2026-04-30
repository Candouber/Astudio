import { useChatStore, type ChatMessage } from '../stores/chatStore'
import { useTaskStore } from '../stores/taskStore'
import { useLocaleStore } from '../stores/localeStore'
import { translate } from '../i18n/t'
import { api } from './client'
import type { PlanStep, ClarificationQuestion, PathNode } from '../types'

const ACTIVE_TASK_STATUSES = new Set(['planning', 'executing'])

function updateChatTaskFromStream(
  taskId: string,
  status: string,
  message?: string,
  studio?: { id?: string; scenario?: string },
) {
  const patch: Partial<ChatMessage> = {
    taskStatus: status,
    isStreaming: ACTIVE_TASK_STATUSES.has(status),
    thinkingText: ACTIVE_TASK_STATUSES.has(status) ? (message || '') : '',
  }
  if (studio?.id) patch.studioId = studio.id
  if (studio?.scenario) patch.studioName = studio.scenario
  useChatStore.getState().updateTaskMessage(taskId, patch)
}

export function parseSSEStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  onEvent: (event: string, data: string) => void,
  onDone: () => void,
) {
  const decoder = new TextDecoder()
  let buffer = ''
  let eventType = ''
  const dataLines: string[] = []

  const dispatch = () => {
    if (dataLines.length === 0 && !eventType) return
    const data = dataLines.join('\n')
    const ev = eventType || 'message'
    try {
      onEvent(ev, data)
    } catch {
      // Listener errors should not break the stream parser.
    }
    eventType = ''
    dataLines.length = 0
  }

  const pump = async () => {
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) { dispatch(); onDone(); return }

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split(/\r?\n/)
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line === '') {
            dispatch()
          } else if (line.startsWith(':')) {
            continue
          } else if (line.startsWith('event:')) {
            eventType = line.slice(6).trim()
          } else if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).replace(/^ /, ''))
          }
        }
      }
    } catch {
      onDone()
    }
  }
  pump()
}

export function startChatStream(question: string, files: File[] = []): AbortController {
  const controller = new AbortController()
  const chat = useChatStore.getState()
  const loc = () => useLocaleStore.getState().locale

  const msgId = `assistant-${Date.now()}`

  chat.addMessage({
    id: msgId,
    role: 'assistant',
    content: '',
    thinkingText: translate(loc(), 'sse.creatingTask'),
    timestamp: Date.now(),
    isStreaming: true,
  })
  chat.setStreaming(true)

  ;(async () => {
    let taskId: string
    try {
      const r = files.length
        ? await api.postAskWithAttachments(question, files)
        : await api.postAsk(question)
      taskId = r.task_id
    } catch {
      chat.updateMessage(msgId, {
        content: translate(loc(), 'sse.backendDown'),
        thinkingText: '',
        isStreaming: false,
      })
      chat.setStreaming(false)
      return
    }

    chat.updateMessage(msgId, {
      taskId,
      taskStatus: 'planning',
      thinkingText: translate(loc(), 'sse.taskCreated'),
    })
    const ts = useTaskStore.getState()
    ts.setTask({
      id: taskId,
      question: '',
      nodes: [],
      edges: [],
      sub_tasks: [],
      plan_steps: [],
      clarification_questions: [],
      clarification_answers: {},
      status: 'planning',
      created_at: new Date().toISOString(),
    })

    try {
      const res = await fetch(`/api/tasks/${taskId}/stream`, { signal: controller.signal })
      if (!res.ok || !res.body) {
        chat.updateMessage(msgId, {
          content: translate(loc(), 'sse.streamDisconnected'),
          thinkingText: '',
          isStreaming: false,
        })
        chat.setStreaming(false)
        return
      }
      parseSSEStream(
        res.body.getReader(),
        (ev, raw) => handleAskStreamEvent(ev, raw, msgId, taskId),
        () => {
          const cur = useChatStore.getState().messages.find(m => m.id === msgId)
          if (cur && !cur.content) {
            chat.updateMessage(msgId, {
              content: translate(loc(), 'sse.viewProgressFallback'),
              thinkingText: '',
            })
          }
          chat.updateMessage(msgId, { isStreaming: false, thinkingText: '' })
          chat.setStreaming(false)
        },
      )
    } catch (err: unknown) {
      const e = err as { name?: string }
      if (e.name === 'AbortError') return
      chat.updateMessage(msgId, {
        content: translate(loc(), 'sse.streamError'),
        thinkingText: '',
        isStreaming: false,
      })
      chat.setStreaming(false)
    }
  })()

  return controller
}

function handleAskStreamEvent(
  event: string,
  rawData: string,
  msgId: string,
  taskId: string,
) {
  const chat = useChatStore.getState()
  const taskStore = useTaskStore.getState()
  const loc = useLocaleStore.getState().locale

  try {
    const data = JSON.parse(rawData) as Record<string, unknown> & { task_id?: string }
    if (data.task_id && data.task_id !== taskId) return

    switch (event) {
      case 'status': {
        const message = (data.message as string) || ''
        if (message) {
          chat.setThinking(msgId, message)
          taskStore.setStatus(message)
        }
        if (data.task_id) {
          const patch: Partial<ChatMessage> = {
            taskId: data.task_id as string,
            taskStatus: (data.status as string) || 'planning',
          }
          if (data.studio_id) patch.studioId = data.studio_id as string
          if (data.studio_scenario) patch.studioName = data.studio_scenario as string
          chat.updateMessage(msgId, patch)
          taskStore.updateTaskStatus((data.status as never) || 'planning')
        }
        break
      }
      case 'node_added': {
        const st = (data as { status?: string; output?: string }).status
        const out = (data as { output?: string }).output
        if (st === 'completed' && out) {
          chat.setContent(msgId, out)
          chat.setThinking(msgId, '')
        }
        break
      }
      case 'done_pause': {
        const studioPatch: Partial<ChatMessage> = {}
        if (data.studio_id) studioPatch.studioId = data.studio_id as string
        if (data.studio_scenario) studioPatch.studioName = data.studio_scenario as string
        if (data.action === 'need_clarification') {
          const questions = (data.questions as ClarificationQuestion[]) || []
          chat.updateMessage(msgId, {
            content: translate(loc, 'sse.needClarification', { count: questions.length }),
            thinkingText: '',
            taskStatus: 'need_clarification',
            ...studioPatch,
          })
          useTaskStore.getState().setClarificationQuestions(
            (data.task_id as string) || taskId,
            questions,
            (data.studio_id as string) || '',
          )
        } else {
          const steps = (data.steps as PlanStep[]) || []
          chat.updateMessage(msgId, {
            content: translate(loc, 'sse.planReady', { count: steps.length }),
            thinkingText: '',
            taskStatus: 'await_leader_plan_approval',
            ...studioPatch,
          })
          useTaskStore.getState().setPlanSteps(
            steps,
            (data.studio_id as string) || '',
          )
        }
        chat.setStreaming(false)
        void useTaskStore.getState().fetchTask(taskId)
        break
      }
      case 'done':
        chat.updateMessage(msgId, { isStreaming: false, thinkingText: '' })
        chat.setStreaming(false)
        void useTaskStore.getState().fetchTask(taskId)
        break
      default:
        break
    }
  } catch {
    // ignore
  }
}

export function connectTaskStream(taskId: string): AbortController {
  const controller = new AbortController()
  const handleDisconnect = () => {
    const store = useTaskStore.getState()
    void store.fetchTask(taskId).finally(() => {
      const cur = useTaskStore.getState().currentTask
      if (cur?.id === taskId && (cur.status === 'planning' || cur.status === 'executing')) {
        useTaskStore.getState().setExecuting(false)
      }
    })
  }

  fetch(`/api/tasks/${taskId}/stream`, {
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok || !res.body) {
        handleDisconnect()
        return
      }
      parseSSEStream(
        res.body.getReader(),
        (ev, raw) => handleStreamEvent(ev, raw, taskId),
        handleDisconnect,
      )
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        console.error('Task stream error:', err)
        handleDisconnect()
      }
    })

  return controller
}

function handleStreamEvent(event: string, rawData: string, taskId: string) {
  const store = useTaskStore.getState()
  if (store.expectedTaskId && store.expectedTaskId !== taskId) return
  if (store.currentTask && store.currentTask.id !== taskId) return
  try {
    const data = JSON.parse(rawData) as {
      status?: string
      task_id?: string
      message?: string
      action?: string
      studio_id?: string
      studio_scenario?: string
      questions?: unknown[]
      steps?: unknown[]
    }
    if (data.task_id && data.task_id !== taskId) return
    switch (event) {
      case 'status':
        if (data.status) {
          updateChatTaskFromStream(taskId, data.status, data.message, {
            id: data.studio_id,
            scenario: data.studio_scenario,
          })
          store.updateTaskStatus(data.status as never)
        } else if (data.message) {
          useChatStore.getState().updateTaskMessage(taskId, { thinkingText: data.message })
        }
        if (data.message) {
          store.setStatus(data.message)
        }
        break
      case 'node_added': {
        const d = data as {
          id: string
          type: PathNode['type']
          agent_role?: string
          step_label?: string
          input?: string
          output?: string
          status?: string
          deep_dives?: never[]
          distilled_summary?: string
          parent_id?: string
          position?: { x: number; y: number }
        }
        store.addNode({
          id: d.id,
          type: d.type,
          agent_role: d.agent_role || '',
          step_label: d.step_label || '',
          input: d.input || '',
          output: d.output || '',
          status: (d.status || 'pending') as PathNode['status'],
          deep_dives: d.deep_dives || [],
          distilled_summary: d.distilled_summary || '',
          parent_id: d.parent_id,
          position: d.position || { x: 0, y: 0 },
        })
        if (d.status === 'running') {
          store.markNodeActivity(d.id, d.output || '')
        }
        break
      }
      case 'node_updated': {
        const d = data as { node_id: string; status: string; output?: string }
        store.updateNodeStatus(d.node_id, d.status as PathNode['status'], d.output)
        store.markNodeActivity(d.node_id, d.output || '')
        break
      }
      case 'done_pause': {
        if (data.action === 'need_clarification' && data.questions) {
          store.setClarificationQuestions(
            taskId,
            data.questions as never[],
            data.studio_id || '',
          )
          updateChatTaskFromStream(taskId, 'need_clarification', '', {
            id: data.studio_id,
            scenario: data.studio_scenario,
          })
          store.updateTaskStatus('need_clarification' as never)
        } else if (data.action === 'review_plan' && data.steps) {
          store.setPlanSteps(data.steps as never[], data.studio_id || '')
          updateChatTaskFromStream(taskId, 'await_leader_plan_approval', '', {
            id: data.studio_id,
            scenario: data.studio_scenario,
          })
          store.updateTaskStatus('await_leader_plan_approval' as never)
        }
        void store.fetchTask(taskId)
        break
      }
      case 'heartbeat': {
        const ts = (data as { ts_ms?: number }).ts_ms
        store.markHeartbeat(typeof ts === 'number' ? ts : Date.now())
        break
      }
      case 'done':
        store.setExecuting(false)
        void store.fetchTask(taskId)
        break
    }
  } catch {
    // ignore
  }
}
