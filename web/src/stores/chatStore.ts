import { create } from 'zustand'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string         // 实际答案内容
  thinkingText: string    // 流式过程中显示的状态文字（thinking 动画）
  timestamp: number
  taskId?: string
  taskStatus?: string
  // 路由后由后端推送：任务被分配到的工作室
  studioId?: string
  studioName?: string
  attachments?: { name: string; size: number }[]
  isStreaming?: boolean
}

interface ChatState {
  messages: ChatMessage[]
  isStreaming: boolean

  addMessage: (msg: ChatMessage) => void
  updateMessage: (id: string, patch: Partial<ChatMessage>) => void
  updateTaskMessage: (taskId: string, patch: Partial<ChatMessage>) => void
  setContent: (id: string, content: string) => void
  setThinking: (id: string, text: string) => void
  setStreaming: (v: boolean) => void
  clear: () => void
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  isStreaming: false,

  addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),

  updateMessage: (id, patch) =>
    set((s) => ({
      messages: s.messages.map((m) => (m.id === id ? { ...m, ...patch } : m)),
    })),

  updateTaskMessage: (taskId, patch) =>
    set((s) => ({
      messages: s.messages.map((m) => (m.taskId === taskId ? { ...m, ...patch } : m)),
    })),

  setContent: (id, content) =>
    set((s) => ({
      messages: s.messages.map((m) => (m.id === id ? { ...m, content } : m)),
    })),

  setThinking: (id, text) =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === id ? { ...m, thinkingText: text } : m,
      ),
    })),

  setStreaming: (v) => set({ isStreaming: v }),

  clear: () => set({ messages: [], isStreaming: false }),
}))
