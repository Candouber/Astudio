import { useRef, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useChatStore, type ChatMessage } from '../stores/chatStore'
import { startChatStream } from '../api/sse'
import { useI18n } from '../i18n/useI18n'
import { translateStatusMessage } from '../i18n/translateStatusMessage'
import type { Locale } from '../stores/localeStore'
import {
  Send, Bot, User, ExternalLink, Loader, ClipboardList,
  Compass, Sparkles, Brain, HelpCircle, ClipboardCheck,
  CheckCircle2, AlertTriangle, TimerOff, StopCircle, XCircle, Building2,
  Paperclip, FileText, X,
} from 'lucide-react'
import './ChatHome.css'

interface StatusMeta {
  /** i18n key under chatHome.status.*；未知状态时为空并用 fallbackRaw 展示 */
  labelKey?: string
  fallbackRaw?: string
  icon: typeof Loader
  tone: 'progress' | 'wait' | 'success' | 'warn' | 'danger' | 'neutral'
  inProgress?: boolean
}

const STATUS_META: Record<string, Omit<StatusMeta, 'fallbackRaw'>> = {
  routing:                     { labelKey: 'chatHome.status.routing',                     icon: Compass,      tone: 'progress', inProgress: true },
  matched:                     { labelKey: 'chatHome.status.matched',                     icon: CheckCircle2, tone: 'progress', inProgress: true },
  hr_studio:                   { labelKey: 'chatHome.status.hr_studio',                  icon: Sparkles,     tone: 'progress', inProgress: true },
  planning:                    { labelKey: 'chatHome.status.planning',                     icon: Brain,        tone: 'progress', inProgress: true },
  need_clarification:          { labelKey: 'chatHome.status.need_clarification',          icon: HelpCircle,   tone: 'warn' },
  await_leader_plan_approval:  { labelKey: 'chatHome.status.await_leader_plan_approval', icon: ClipboardCheck, tone: 'warn' },
  executing:                   { labelKey: 'chatHome.status.executing',                    icon: Loader,       tone: 'progress', inProgress: true },
  completed:                   { labelKey: 'chatHome.status.completed',                    icon: CheckCircle2, tone: 'success' },
  completed_with_blockers:     { labelKey: 'chatHome.status.completed_with_blockers',      icon: AlertTriangle, tone: 'warn' },
  timeout_killed:              { labelKey: 'chatHome.status.timeout_killed',             icon: TimerOff,     tone: 'danger' },
  terminated:                  { labelKey: 'chatHome.status.terminated',                   icon: StopCircle,   tone: 'danger' },
  failed:                      { labelKey: 'chatHome.status.failed',                       icon: XCircle,      tone: 'danger' },
}

function getStatusMeta(status?: string): StatusMeta {
  if (!status) {
    return { labelKey: undefined, fallbackRaw: undefined, icon: Loader, tone: 'progress', inProgress: true }
  }
  const row = STATUS_META[status]
  if (row) return { ...row, fallbackRaw: undefined }
  return { fallbackRaw: status, icon: Loader, tone: 'neutral' }
}

function statusLabel(meta: StatusMeta, t: (key: string) => string): string {
  if (meta.labelKey) return t(meta.labelKey)
  if (meta.fallbackRaw) return meta.fallbackRaw
  return t('chatHome.processing')
}

export default function ChatHome() {
  const { t, locale } = useI18n()
  const messages = useChatStore(s => s.messages)
  const isStreaming = useChatStore(s => s.isStreaming)
  const [input, setInput] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [isDragging, setIsDragging] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const streamCtrlRef = useRef<AbortController | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // 注：不随路由卸载 abort /stream —— 任务在后台继续，重连时仍可再订阅同一 task 的 /stream

  const handleSend = () => {
    const q = input.trim()
    if ((!q && files.length === 0) || isStreaming) return
    const sendingFiles = files

    useChatStore.getState().addMessage({
      id: `user-${Date.now()}`,
      role: 'user',
      content: q || t('chatHome.analyzeAttachments'),
      thinkingText: '',
      timestamp: Date.now(),
      attachments: sendingFiles.map(file => ({ name: file.name, size: file.size })),
    })

    setInput('')
    setFiles([])
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
    if (fileInputRef.current) fileInputRef.current.value = ''
    // 切换到新一轮流之前先中断上一轮
    if (streamCtrlRef.current) {
      try { streamCtrlRef.current.abort() } catch { /* ignore */ }
    }
    streamCtrlRef.current = startChatStream(q, sendingFiles)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'Enter' || e.shiftKey) return
    if (e.nativeEvent.isComposing || e.keyCode === 229) return
    e.preventDefault()
    handleSend()
  }

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    const el = e.target
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 160) + 'px'
  }

  const addFiles = (list: FileList | File[]) => {
    const next = Array.from(list)
    if (!next.length) return
    setFiles(prev => {
      const merged = [...prev]
      for (const file of next) {
        if (merged.length >= 8) break
        if (!merged.some(item => item.name === file.name && item.size === file.size)) {
          merged.push(file)
        }
      }
      return merged
    })
  }

  const removeFile = (index: number) => {
    setFiles(prev => prev.filter((_, i) => i !== index))
  }

  return (
    <div
      className={`chat-home ${isDragging ? 'chat-home--dragging' : ''}`}
      onDragOver={(e) => {
        e.preventDefault()
        if (!isStreaming) setIsDragging(true)
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={(e) => {
        e.preventDefault()
        setIsDragging(false)
        if (!isStreaming) addFiles(e.dataTransfer.files)
      }}
    >
      {messages.length === 0 ? (
        <div className="chat-home__empty">
          <img className="chat-home__hero-icon" src="/astudio-icon.png" alt="" />
          <h1>{t('chatHome.heroTitle')}</h1>
          <p>{t('chatHome.heroSubtitle')}</p>
        </div>
      ) : (
        <div className="chat-home__messages">
          {messages.map(msg => (
            <MessageBubble key={msg.id} msg={msg} navigate={navigate} t={t} locale={locale} />
          ))}
          <div ref={bottomRef} />
        </div>
      )}

      <div className="chat-home__input-area">
        {files.length > 0 && (
          <div className="chat-home__attachments">
            {files.map((file, index) => (
              <div className="chat-home__attachment" key={`${file.name}-${file.size}-${index}`}>
                <FileText size={14} />
                <span title={file.name}>{file.name}</span>
                <small>{formatBytes(file.size)}</small>
                <button type="button" onClick={() => removeFile(index)} disabled={isStreaming} aria-label={t('chatHome.removeAttachment')}>
                  <X size={13} />
                </button>
              </div>
            ))}
          </div>
        )}
        <div className={`chat-home__input-box ${isDragging ? 'chat-home__input-box--dragging' : ''}`}>
          <input
            ref={fileInputRef}
            className="chat-home__file-input"
            type="file"
            multiple
            accept=".xlsx,.csv,.pdf,.png,.jpg,.jpeg,.webp,.txt,.md,.json"
            onChange={e => {
              if (e.target.files) addFiles(e.target.files)
            }}
            disabled={isStreaming}
          />
          <button
            type="button"
            className="chat-home__attach"
            onClick={() => fileInputRef.current?.click()}
            disabled={isStreaming || files.length >= 8}
            title={t('chatHome.uploadAttachment')}
          >
            <Paperclip size={16} />
          </button>
          <textarea
            ref={textareaRef}
            className="chat-home__textarea"
            placeholder={t('chatHome.placeholder')}
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            rows={1}
            disabled={isStreaming}
          />
          <button
            className="chat-home__send btn btn-primary"
            onClick={handleSend}
            disabled={(!input.trim() && files.length === 0) || isStreaming}
          >
            {isStreaming ? <Loader size={16} className="animate-pulse" /> : <Send size={16} />}
          </button>
        </div>
        <p className="chat-home__hint">{t('chatHome.hint')}</p>
      </div>
    </div>
  )
}

function formatBytes(size: number) {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function MessageBubble({
  msg,
  navigate,
  t,
  locale,
}: {
  msg: ChatMessage
  navigate: ReturnType<typeof useNavigate>
  t: (key: string) => string
  locale: Locale
}) {
  const isUser = msg.role === 'user'

  return (
    <div className={`chat-msg ${isUser ? 'chat-msg--user' : 'chat-msg--assistant'}`}>
      <div className="chat-msg__avatar">
        {isUser ? <User size={16} /> : <Bot size={16} />}
      </div>
      <div className="chat-msg__body">
        {/* Thinking indicator：仅在 streaming 且还没有正式内容时显示 */}
        {!isUser && msg.isStreaming && !msg.content && msg.thinkingText && (
          <div className="chat-msg__thinking">
            <span className="chat-msg__thinking-dot" />
            <span className="chat-msg__thinking-dot" />
            <span className="chat-msg__thinking-dot" />
            <span className="chat-msg__thinking-text">{translateStatusMessage(locale, msg.thinkingText)}</span>
          </div>
        )}

        {/* 正式内容 */}
        {(msg.content || isUser) && (
          <div className="chat-msg__content">
            {msg.content}
            {/* 流式光标：仅在有内容且仍在流时显示 */}
            {!isUser && msg.isStreaming && msg.content && (
              <span className="chat-msg__cursor" />
            )}
          </div>
        )}
        {isUser && msg.attachments && msg.attachments.length > 0 && (
          <div className="chat-msg__attachments">
            {msg.attachments.map((file, index) => (
              <span key={`${file.name}-${index}`}>
                <FileText size={13} />
                {file.name}
                <small>{formatBytes(file.size)}</small>
              </span>
            ))}
          </div>
        )}

        {/* need_clarification：醒目的行动卡片 */}
        {!isUser && msg.taskId && msg.taskStatus === 'need_clarification' && (
          <div className="chat-msg__clarify-card">
            <div className="chat-msg__clarify-header">
              <ClipboardList size={18} />
              <span>{t('chatHome.clarifyTitle')}</span>
            </div>
            {msg.studioName && (
              <div className="chat-msg__clarify-studio">
                <Building2 size={13} /> {t('chatHome.clarifyStudio')}{msg.studioName}
              </div>
            )}
            <p className="chat-msg__clarify-desc">
              {t('chatHome.clarifyDesc')}
            </p>
            <button
              className="btn btn-primary chat-msg__clarify-btn"
              onClick={() => navigate(`/tasks/${msg.taskId}`)}
            >
              <ExternalLink size={14} /> {t('chatHome.clarifyCta')}
            </button>
          </div>
        )}

        {/* 普通任务卡片（非 need_clarification） */}
        {!isUser && msg.taskId && msg.taskStatus !== 'need_clarification' && (
          <TaskStatusCard msg={msg} navigate={navigate} t={t} />
        )}
      </div>
    </div>
  )
}

function TaskStatusCard({
  msg,
  navigate,
  t,
}: {
  msg: ChatMessage
  navigate: ReturnType<typeof useNavigate>
  t: (key: string) => string
}) {
  const meta = getStatusMeta(msg.taskStatus)
  const Icon = meta.icon
  const label = statusLabel(meta, t)
  return (
    <div className={`task-status-card task-status-card--${meta.tone}`}>
      <div className="task-status-card__row">
        <span className={`status-chip status-chip--${meta.tone}`}>
          <Icon size={13} className={meta.inProgress ? 'animate-pulse' : ''} />
          <span>{label}</span>
        </span>
        {msg.studioName && (
          <span className="status-chip status-chip--studio" title={msg.studioId}>
            <Building2 size={13} />
            <span>{msg.studioName}</span>
          </span>
        )}
      </div>
      <button
        className="task-status-card__link"
        onClick={() => navigate(`/tasks/${msg.taskId}`)}
      >
        <ExternalLink size={13} />
        {t('chatHome.viewTaskDetail')}
      </button>
    </div>
  )
}
