import { useRef, useEffect, useMemo, useState } from 'react'
import type { Annotation } from '../../types'
import MarkdownRenderer from '../common/MarkdownRenderer'
import { Loader, Send, X, Quote, Trash2 } from 'lucide-react'
import { useI18n } from '../../i18n/useI18n'
import { useLocaleStore } from '../../stores/localeStore'
import { parseSSEStream } from '../../api/sse'
import {
  applyHighlights,
  clearHighlights,
  annotationSignature,
} from '../../utils/annotationHighlight'
import './AnnotationPopover.css'

interface Props {
  annotation: Annotation
  anchorRect: DOMRect
  onClose: () => void
  onDelete: (id: string) => void
  taskId: string
  onCreated: () => void
  annotations?: Annotation[]
  onAnnotationHighlightClick?: (annotation: Annotation, anchorRect: DOMRect) => void
}

function HighlightedAnnotationAnswer({
  annotationId,
  content,
  annotations,
  onHighlightClick,
}: {
  annotationId: string
  content: string
  annotations: Annotation[]
  onHighlightClick?: (annotation: Annotation, anchorRect: DOMRect) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const annSignature = useMemo(() => annotationSignature(annotations), [annotations])

  useEffect(() => {
    const container = ref.current
    if (!container) return
    if (annotations.length === 0) {
      clearHighlights(container)
      return
    }
    const raf = requestAnimationFrame(() => {
      if (ref.current) applyHighlights(ref.current, annotations)
    })
    return () => {
      cancelAnimationFrame(raf)
      if (container.isConnected) clearHighlights(container)
    }
  }, [content, annSignature, annotations])

  const handleClick = (e: React.MouseEvent) => {
    const mark = (e.target as HTMLElement).closest('mark.ann-hl') as HTMLElement | null
    if (!mark?.dataset.annId || !onHighlightClick) return
    const child = annotations.find(item => item.id === mark.dataset.annId)
    if (child) onHighlightClick(child, mark.getBoundingClientRect())
  }

  return (
    <div
      ref={ref}
      className="ann-popover__answer"
      data-node-id={`annotation:${annotationId}`}
      onClick={handleClick}
    >
      <MarkdownRenderer content={content} />
    </div>
  )
}

export default function AnnotationPopover({
  annotation,
  anchorRect,
  onClose,
  onDelete,
  taskId,
  onCreated,
  annotations = [],
  onAnnotationHighlightClick,
}: Props) {
  const { t } = useI18n()
  const uiLocale = useLocaleStore(s => s.locale)
  const ref = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const [followUp, setFollowUp] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [followUpAnswer, setFollowUpAnswer] = useState('')
  const [saved, setSaved] = useState(false)
  const [dragPosition, setDragPosition] = useState<{ x: number; y: number } | null>(null)
  const dragOffsetRef = useRef<{ x: number; y: number } | null>(null)
  const width = Math.min(380, window.innerWidth - 24)
  const margin = 12

  useEffect(() => {
    abortRef.current?.abort()
    setFollowUp('')
    setFollowUpAnswer('')
    setStreaming(false)
    setSaved(false)
    setDragPosition(null)
  }, [annotation.id])

  useEffect(() => {
    const handleMove = (e: MouseEvent) => {
      const offset = dragOffsetRef.current
      if (!offset) return
      const dragWidth = Math.min(380, window.innerWidth - 24)
      const nextX = Math.max(8, Math.min(e.clientX - offset.x, window.innerWidth - dragWidth - 8))
      const nextY = Math.max(8, Math.min(e.clientY - offset.y, window.innerHeight - 80))
      setDragPosition({ x: nextX, y: nextY })
    }
    const handleUp = () => {
      dragOffsetRef.current = null
    }
    document.addEventListener('mousemove', handleMove)
    document.addEventListener('mouseup', handleUp)
    return () => {
      document.removeEventListener('mousemove', handleMove)
      document.removeEventListener('mouseup', handleUp)
    }
  }, [])

  useEffect(() => {
    const handleOutside = (e: MouseEvent) => {
      if ((e.target as HTMLElement).closest('.selection-toolbar')) return
      if ((e.target as HTMLElement).closest('mark.ann-hl')) return
      if ((e.target as HTMLElement).closest('.annotation-popover-selection-scope')) return
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

  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  const handleFollowUpSubmit = async () => {
    if (!followUp.trim() || streaming) return
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl
    setStreaming(true)
    setFollowUpAnswer('')
    try {
      const contextText = [
        annotation.selected_text,
        annotation.question,
        annotation.answer,
      ].filter(Boolean).join('\n\n')
      const res = await fetch(`/api/tasks/${taskId}/annotate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          node_id: `annotation:${annotation.id}`,
          selected_text: contextText.slice(0, 6000),
          question: followUp.trim(),
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
              setFollowUpAnswer(accum)
            }
            if (ev === 'done') {
              settled = true
              setStreaming(false)
              setSaved(true)
              setFollowUp('')
              onCreated()
            }
            if (ev === 'error') {
              settled = true
              setFollowUpAnswer(prev => prev || t('annotationComposer.requestFailed'))
              setStreaming(false)
            }
          } catch { /* ignore */ }
        },
        () => {
          if (!settled && !ctrl.signal.aborted) {
            setFollowUpAnswer(prev => prev || t('annotationComposer.streamDisconnect'))
          }
          setStreaming(false)
        },
      )
    } catch (err) {
      if ((err as Error)?.name !== 'AbortError') {
        setFollowUpAnswer(t('annotationComposer.requestFailed'))
      }
      setStreaming(false)
    }
  }

  const handleFollowUpKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'Enter' || e.shiftKey) return
    if (e.nativeEvent.isComposing || e.keyCode === 229) return
    e.preventDefault()
    handleFollowUpSubmit()
  }

  // 定位：优先在标记下方，空间不够则上方
  const left = Math.max(margin, Math.min(anchorRect.left, window.innerWidth - width - margin))
  const top = anchorRect.bottom + 8
  const spaceBelow = window.innerHeight - anchorRect.bottom
  const placeAbove = spaceBelow < 300
  const style: React.CSSProperties = {
    position: 'fixed',
    width,
    ...(dragPosition
      ? { left: dragPosition.x, top: dragPosition.y }
      : {
          left,
          ...(placeAbove
            ? { bottom: window.innerHeight - anchorRect.top + 8 }
            : { top }),
        }),
    zIndex: 1100,
  }

  const handleDragStart = (e: React.MouseEvent) => {
    if (e.button !== 0) return
    if ((e.target as HTMLElement).closest('button')) return
    const rect = ref.current?.getBoundingClientRect()
    if (!rect) return
    dragOffsetRef.current = { x: e.clientX - rect.left, y: e.clientY - rect.top }
    setDragPosition({ x: rect.left, y: rect.top })
    e.preventDefault()
  }

  return (
    <div ref={ref} className="ann-popover annotation-popover-selection-scope" style={style}>
      <div className="ann-popover__header ann-popover__header--draggable" onMouseDown={handleDragStart}>
        <span className="ann-popover__title">{t('annotationPopover.title')}</span>
        <button type="button" className="ann-popover__close" onClick={onClose}><X size={14} /></button>
      </div>

      <div className="ann-popover__quote" data-node-id={`annotation:${annotation.id}`}>
        <Quote size={11} />
        <span>
          {annotation.selected_text.length > 150
            ? annotation.selected_text.slice(0, 150) + '…'
            : annotation.selected_text}
        </span>
      </div>

      <div className="ann-popover__question" data-node-id={`annotation:${annotation.id}`}>{annotation.question}</div>

      {annotation.answer ? (
        <HighlightedAnnotationAnswer
          annotationId={annotation.id}
          content={annotation.answer}
          annotations={annotations}
          onHighlightClick={onAnnotationHighlightClick}
        />
      ) : (
        <div className="ann-popover__no-answer">{t('annotationPopover.generating')}</div>
      )}

      <div className="ann-popover__followup">
        <textarea
          className="ann-popover__followup-input"
          value={followUp}
          onChange={e => {
            setFollowUp(e.target.value)
            if (saved) setSaved(false)
          }}
          onKeyDown={handleFollowUpKeyDown}
          placeholder={t('annotationPopover.followUpPlaceholder')}
          rows={3}
          disabled={streaming}
        />
        {followUpAnswer && (
          <div className="ann-popover__answer ann-popover__answer--followup" data-node-id={`annotation:${annotation.id}`}>
            <MarkdownRenderer content={followUpAnswer} />
          </div>
        )}
        <div className="ann-popover__followup-actions">
          {saved && <span className="ann-popover__saved">{t('annotationPopover.followUpSaved')}</span>}
          {streaming && <span className="ann-popover__saved"><Loader size={12} className="animate-spin" /> {t('annotationPanel.answering')}</span>}
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={handleFollowUpSubmit}
            disabled={!followUp.trim() || streaming}
          >
            {streaming ? <Loader size={13} className="animate-spin" /> : <Send size={13} />}
            {t('annotationPopover.followUpSubmit')}
          </button>
        </div>
      </div>

      <div className="ann-popover__footer">
        <span className="ann-popover__time">
          {new Date(annotation.created_at).toLocaleString(uiLocale === 'zh' ? 'zh-CN' : 'en-US', {
            month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
          })}
        </span>
        <button
          type="button"
          className="ann-popover__delete"
          onClick={() => { onDelete(annotation.id); onClose() }}
        >
          <Trash2 size={12} /> {t('annotationPopover.delete')}
        </button>
      </div>
    </div>
  )
}
