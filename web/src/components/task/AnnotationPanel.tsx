import { useState, useEffect, useRef } from 'react'
import type { Annotation } from '../../types'
import { parseSSEStream } from '../../api/sse'
import MarkdownRenderer from '../common/MarkdownRenderer'
import { X, Send, Loader, MessageSquareText, Trash2, Quote, ChevronDown, ChevronRight, ArrowUpRight } from 'lucide-react'
import { useI18n } from '../../i18n/useI18n'
import { useLocaleStore } from '../../stores/localeStore'
import './AnnotationPanel.css'

interface PendingAnnotation {
  selectedText: string
  nodeId: string
}

interface Props {
  taskId: string
  pending: PendingAnnotation | null
  annotations: Annotation[]
  onAnnotationsChange: () => void
  onClose: () => void
  onClearPending: () => void
  onDelete: (id: string) => void
  onJumpToAnnotation?: (annotation: Annotation) => void
}

export default function AnnotationPanel({
  taskId, pending, annotations, onAnnotationsChange,
  onClose, onClearPending, onDelete,
  onJumpToAnnotation,
}: Props) {
  const { t } = useI18n()
  const uiLocale = useLocaleStore(s => s.locale)
  const [question, setQuestion] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [streamAnswer, setStreamAnswer] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (pending && inputRef.current) {
      inputRef.current.focus()
    }
  }, [pending])

  const handleSubmit = async () => {
    if (!pending || !question.trim() || streaming) return

    setStreaming(true)
    setStreamAnswer('')

    try {
      const res = await fetch(`/api/tasks/${taskId}/annotate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          node_id: pending.nodeId,
          selected_text: pending.selectedText,
          question: question.trim(),
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
              setStreamAnswer(accum)
            }
            if (ev === 'done') {
              setStreaming(false)
              setQuestion('')
              onClearPending()
              onAnnotationsChange()
              setStreamAnswer('')
            }
          } catch { /* ignore */ }
        },
        () => setStreaming(false),
      )
    } catch {
      setStreaming(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'Enter' || e.shiftKey) return
    if (e.nativeEvent.isComposing || e.keyCode === 229) return
    e.preventDefault()
    handleSubmit()
  }

  return (
    <div className="ann-panel">
      {/* Header */}
      <div className="ann-panel__header">
        <MessageSquareText size={16} />
        <span className="ann-panel__title">{t('annotationPanel.title')}</span>
        <span className="ann-panel__count">{annotations.length}</span>
        <button type="button" className="btn btn-icon ann-panel__close" onClick={onClose}>
          <X size={16} />
        </button>
      </div>

      {/* New annotation form */}
      {pending && (
        <div className="ann-panel__new">
          <div className="ann-panel__quote">
            <Quote size={12} />
            <span>{pending.selectedText.length > 120 ? pending.selectedText.slice(0, 120) + '…' : pending.selectedText}</span>
          </div>
          <textarea
            ref={inputRef}
            className="ann-panel__input"
            rows={3}
            placeholder={t('annotationPanel.questionPlaceholder')}
            value={question}
            onChange={e => setQuestion(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={streaming}
          />
          <div className="ann-panel__form-actions">
            <button
              type="button"
              className="btn btn-sm btn-primary"
              onClick={handleSubmit}
              disabled={streaming || !question.trim()}
            >
              {streaming ? <Loader size={13} className="animate-spin" /> : <Send size={13} />}
              {streaming ? t('annotationPanel.answering') : t('annotationPanel.submit')}
            </button>
            <button
              type="button"
              className="btn btn-sm btn-secondary"
              onClick={onClearPending}
              disabled={streaming}
            >
              {t('common.cancel')}
            </button>
          </div>

          {streaming && streamAnswer && (
            <div className="ann-panel__stream">
              <MarkdownRenderer content={streamAnswer} />
            </div>
          )}
        </div>
      )}

      {/* Annotations list */}
      <div className="ann-panel__list">
        {annotations.length === 0 && !pending && (
          <div className="ann-panel__empty">
            <MessageSquareText size={28} />
            <p>{t('annotationPanel.emptyHint')}</p>
          </div>
        )}
        {annotations.map(ann => {
          const isOpen = expandedId === ann.id
          const localeTag = uiLocale === 'zh' ? 'zh-CN' : 'en-US'
          const fromText = ann.selected_text.length > 40 ? `${ann.selected_text.slice(0, 40)}…` : ann.selected_text
          return (
            <div key={ann.id} id={`ann-card-${ann.id}`} className={`ann-card ${isOpen ? 'ann-card--open' : ''}`}>
              <div
                className="ann-card__header"
                onClick={() => {
                  setExpandedId(prev => prev === ann.id ? null : ann.id)
                }}
              >
                <span className="ann-card__chevron">
                  {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                </span>
                <Quote size={11} className="ann-card__quote-icon" />
                <span className="ann-card__summary">
                  {t('annotationPanel.quoteFrom', { text: fromText })}
                </span>
                <span className="ann-card__time-mini">
                  {new Date(ann.created_at).toLocaleString(localeTag, { month: 'numeric', day: 'numeric' })}
                </span>
              </div>
              {isOpen && (
                <div className="ann-card__body">
                  <div className="ann-card__meta ann-card__meta--top">
                    <button
                      type="button"
                      className="ann-card__jump"
                      onClick={(e) => {
                        e.stopPropagation()
                        onJumpToAnnotation?.(ann)
                      }}
                      title={t('annotationPanel.jumpTitle')}
                    >
                      {t('annotationPanel.jumpCta')}
                      <ArrowUpRight size={12} />
                    </button>
                    <span className="ann-card__time">
                      {new Date(ann.created_at).toLocaleString(localeTag, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                    </span>
                  </div>
                  <div className="ann-card__quote-full">
                    <Quote size={10} />
                    <span>{ann.selected_text.length > 200 ? ann.selected_text.slice(0, 200) + '…' : ann.selected_text}</span>
                  </div>
                  <div className="ann-card__question">{ann.question}</div>
                  {ann.answer && (
                    <div className="ann-card__answer">
                      <MarkdownRenderer content={ann.answer} />
                    </div>
                  )}
                  <div className="ann-card__meta">
                    <div className="ann-card__meta-actions">
                      <button
                        type="button"
                        className="ann-card__delete"
                        onClick={(e) => { e.stopPropagation(); onDelete(ann.id) }}
                        title={t('annotationPanel.deleteTitle')}
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
