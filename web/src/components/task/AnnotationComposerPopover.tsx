import { useEffect, useRef, useState } from 'react'
import { Loader, MessageSquarePlus, Quote, Send, X } from 'lucide-react'
import { parseSSEStream } from '../../api/sse'
import MarkdownRenderer from '../common/MarkdownRenderer'
import { useI18n } from '../../i18n/useI18n'
import './AnnotationPopover.css'

interface PendingAnnotation {
  selectedText: string
  nodeId: string
  anchorRect: DOMRect
}

interface Props {
  taskId: string
  pending: PendingAnnotation
  onClose: () => void
  onCreated: () => void
}

export default function AnnotationComposerPopover({
  taskId,
  pending,
  onClose,
  onCreated,
}: Props) {
  const { t } = useI18n()
  const ref = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const [question, setQuestion] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [answer, setAnswer] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useEffect(() => {
    return () => {
      if (abortRef.current) {
        abortRef.current.abort()
        abortRef.current = null
      }
    }
  }, [])

  useEffect(() => {
    const handleOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose()
      }
    }
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    setTimeout(() => document.addEventListener('mousedown', handleOutside), 0)
    document.addEventListener('keydown', handleEsc)
    return () => {
      document.removeEventListener('mousedown', handleOutside)
      document.removeEventListener('keydown', handleEsc)
    }
  }, [onClose])

  const handleSubmit = async () => {
    if (!question.trim() || streaming || saved) return

    setStreaming(true)
    setAnswer('')

    // 先 abort 上一个请求再建新的，防止重复提交
    if (abortRef.current) abortRef.current.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl

    try {
      const res = await fetch(`/api/tasks/${taskId}/annotate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          node_id: pending.nodeId,
          selected_text: pending.selectedText,
          question: question.trim(),
        }),
        signal: ctrl.signal,
      })

      if (!res.ok || !res.body) throw new Error(t('common.requestFailed'))

      let accum = ''
      let settled = false
      parseSSEStream(
        res.body.getReader(),
        (ev, raw) => {
          if (ctrl.signal.aborted) return
          try {
            const data = JSON.parse(raw)
            if (ev === 'chunk') {
              accum += data.text || ''
              setAnswer(accum)
            }
            if (ev === 'done') {
              settled = true
              setStreaming(false)
              setSaved(true)
              onCreated()
            }
            if (ev === 'error') {
              settled = true
              const msg = typeof data?.message === 'string' && data.message
                ? data.message
                : t('annotationComposer.unknownError')
              setAnswer(prev => prev || t('annotationComposer.errorWithMsg', { msg }))
              setStreaming(false)
            }
          } catch { /* ignore */ }
        },
        () => {
          // 读流异常关闭（可能是网络掉线）：若未收到 done，降级为错误态
          if (!settled && !ctrl.signal.aborted) {
            setAnswer(prev => prev || t('annotationComposer.streamDisconnect'))
          }
          setStreaming(false)
        },
      )
    } catch (err) {
      if ((err as Error)?.name !== 'AbortError') {
        setAnswer(t('annotationComposer.requestFailed'))
      }
      setStreaming(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'Enter' || e.shiftKey) return
    if (e.nativeEvent.isComposing || e.keyCode === 229) return
    e.preventDefault()
    handleSubmit()
  }

  const anchorRect = pending.anchorRect
  const width = Math.min(420, window.innerWidth - 24)
  const margin = 12
  const left = Math.max(margin, Math.min(anchorRect.left, window.innerWidth - width - margin))
  const spaceBelow = window.innerHeight - anchorRect.bottom
  const placeAbove = spaceBelow < 360
  const style: React.CSSProperties = {
    position: 'fixed',
    width,
    left,
    ...(placeAbove
      ? { bottom: window.innerHeight - anchorRect.top + 8 }
      : { top: anchorRect.bottom + 8 }),
    zIndex: 1100,
  }

  return (
    <div ref={ref} className="ann-popover ann-popover--composer" style={style}>
      <div className="ann-popover__header">
        <span className="ann-popover__title">
          <MessageSquarePlus size={14} />
          {t('annotationComposer.newTitle')}
        </span>
        <button type="button" className="ann-popover__close" onClick={onClose} aria-label={t('annotationComposer.closeAria')}>
          <X size={14} />
        </button>
      </div>

      <div className="ann-popover__quote">
        <Quote size={11} />
        <span>
          {pending.selectedText.length > 150
            ? pending.selectedText.slice(0, 150) + '…'
            : pending.selectedText}
        </span>
      </div>

      <div className="ann-popover__composer">
        <textarea
          ref={inputRef}
          className="ann-popover__input"
          rows={3}
          placeholder={t('annotationComposer.placeholder')}
          value={question}
          onChange={e => setQuestion(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={streaming || saved}
        />
        <div className="ann-popover__composer-actions">
          {saved && <span className="ann-popover__saved">{t('annotationComposer.savedHighlight')}</span>}
          <button
            type="button"
            className="btn btn-sm btn-primary"
            onClick={handleSubmit}
            disabled={streaming || saved || !question.trim()}
          >
            {streaming ? <Loader size={13} className="animate-spin" /> : <Send size={13} />}
            {streaming ? t('annotationComposer.answering') : saved ? t('annotationComposer.submitted') : t('annotationComposer.submit')}
          </button>
        </div>
      </div>

      {answer && (
        <div className="ann-popover__answer ann-popover__answer--composer">
          <MarkdownRenderer content={answer} />
        </div>
      )}
    </div>
  )
}
