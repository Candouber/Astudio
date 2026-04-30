import { useEffect, useRef, useState } from 'react'
import { Bot, GitBranch, Loader, MessageSquareText, Send, X } from 'lucide-react'
import { parseSSEStream } from '../../api/sse'
import { connectTaskStream } from '../../api/sse'
import MarkdownRenderer from '../common/MarkdownRenderer'
import { useTaskStore } from '../../stores/taskStore'
import { useI18n } from '../../i18n/useI18n'
import './ResultChat.css'

interface ResultChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
}

interface Props {
  taskId: string
  open?: boolean
  onOpenChange?: (open: boolean) => void
  hideLauncher?: boolean
}

export default function ResultChat({ taskId, open: controlledOpen, onOpenChange, hideLauncher = false }: Props) {
  const { t } = useI18n()
  const [innerOpen, setInnerOpen] = useState(false)
  const [messages, setMessages] = useState<ResultChatMessage[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const updateTaskStatus = useTaskStore(s => s.updateTaskStatus)
  const setExecuting = useTaskStore(s => s.setExecuting)
  const setStatus = useTaskStore(s => s.setStatus)
  const clearPlan = useTaskStore(s => s.clearPlan)
  const clearClarification = useTaskStore(s => s.clearClarification)
  const open = controlledOpen ?? innerOpen
  const setOpen = (next: boolean) => {
    if (controlledOpen === undefined) setInnerOpen(next)
    onOpenChange?.(next)
  }

  useEffect(() => {
    if (open) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
      inputRef.current?.focus()
    }
  }, [open, messages])

  const send = async () => {
    const question = input.trim()
    if (!question || streaming) return

    const userMessage: ResultChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: question,
    }
    const assistantId = `assistant-${Date.now()}`
    const assistantMessage: ResultChatMessage = {
      id: assistantId,
      role: 'assistant',
      content: '',
    }
    const nextMessages = [...messages, userMessage, assistantMessage]

    setMessages(nextMessages)
    setInput('')
    setStreaming(true)

    try {
      const res = await fetch(`/api/tasks/${taskId}/result-chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: nextMessages
            .filter(m => m.content.trim())
            .map(({ role, content }) => ({ role, content })),
        }),
      })

      if (!res.ok || !res.body) throw new Error(t('common.requestFailed'))

      let accum = ''
      parseSSEStream(
        res.body.getReader(),
        (ev, raw) => {
          try {
            const data = JSON.parse(raw)
            if (ev === 'chunk') {
              accum += data.text || ''
              setMessages(prev => prev.map(m => (
                m.id === assistantId ? { ...m, content: accum } : m
              )))
            }
            if (ev === 'done') {
              setStreaming(false)
            }
          } catch { /* ignore */ }
        },
        () => setStreaming(false),
      )
    } catch {
      setMessages(prev => prev.map(m => (
        m.id === assistantId ? { ...m, content: t('resultChat.streamFailed') } : m
      )))
      setStreaming(false)
    }
  }

  const iterate = async () => {
    const instruction = input.trim()
    if (!instruction || streaming) return
    setStreaming(true)
    try {
      const res = await fetch(`/api/tasks/${taskId}/iterate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          instruction,
          messages: messages.map(({ role, content }) => ({ role, content })),
        }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data?.detail || data?.message || t('common.requestFailed'))

      clearPlan()
      clearClarification()
      updateTaskStatus('planning')
      setStatus(t('resultChat.iteratePlanning'))
      setExecuting(true)
      connectTaskStream(taskId)
      setInput('')
      setOpen(false)
    } catch {
      const assistantId = `assistant-${Date.now()}`
      setMessages(prev => [
        ...prev,
        { id: assistantId, role: 'assistant', content: t('resultChat.iterateFailed') },
      ])
    } finally {
      setStreaming(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'Enter' || e.shiftKey) return
    if (e.nativeEvent.isComposing || e.keyCode === 229) return
    e.preventDefault()
    send()
  }

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    e.target.style.height = 'auto'
    e.target.style.height = `${Math.min(e.target.scrollHeight, 140)}px`
  }

  return (
    <>
      {!open && !hideLauncher && (
        <button type="button" className="result-chat__launcher" onClick={() => setOpen(true)}>
          <MessageSquareText size={17} />
          {t('resultChat.launcher')}
        </button>
      )}

      {open && (
        <div className="result-chat">
          <div className="result-chat__header">
            <div className="result-chat__title">
              <Bot size={16} />
              <span>{t('resultView.resultChat')}</span>
            </div>
            <button type="button" className="result-chat__close" onClick={() => setOpen(false)} aria-label={t('resultChat.closeAria')}>
              <X size={15} />
            </button>
          </div>

          <div className="result-chat__body">
            {messages.length === 0 && (
              <div className="result-chat__empty">
                <MessageSquareText size={26} />
                <p>{t('resultChat.emptyHint')}</p>
              </div>
            )}
            {messages.map(message => (
              <div
                key={message.id}
                className={`result-chat__msg result-chat__msg--${message.role}`}
              >
                {message.content ? (
                  <MarkdownRenderer content={message.content} />
                ) : (
                  <span className="result-chat__typing">
                    <Loader size={13} className="animate-spin" />
                    {t('resultChat.thinking')}
                  </span>
                )}
              </div>
            ))}
            <div ref={bottomRef} />
          </div>

          <div className="result-chat__input-row">
            <textarea
              ref={inputRef}
              className="result-chat__input"
              rows={1}
              placeholder={t('resultChat.inputPlaceholder')}
              value={input}
              onChange={handleInput}
              onKeyDown={handleKeyDown}
              disabled={streaming}
            />
            <button
              className="result-chat__iterate"
              onClick={iterate}
              disabled={streaming || !input.trim()}
              title={t('resultChat.iterateBtnTitle')}
            >
              <GitBranch size={15} />
            </button>
            <button
              className="result-chat__send"
              onClick={send}
              disabled={streaming || !input.trim()}
            >
              {streaming ? <Loader size={15} className="animate-spin" /> : <Send size={15} />}
            </button>
          </div>
        </div>
      )}
    </>
  )
}
