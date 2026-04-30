import { useEffect, useRef, useState } from 'react'
import { ArrowUpRight, Loader, Quote, Send, Sparkles, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { connectTaskStream } from '../../api/sse'
import { useTaskStore } from '../../stores/taskStore'
import { useI18n } from '../../i18n/useI18n'
import './AnnotationPopover.css'

interface PendingProcessSelection {
  selectedText: string
  nodeId: string
  anchorRect: DOMRect
}

interface Props {
  taskId: string
  pending: PendingProcessSelection
  onClose: () => void
}

export default function ProcessSelectionPopover({ taskId, pending, onClose }: Props) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const ref = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const [instruction, setInstruction] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [createdTaskId, setCreatedTaskId] = useState('')
  const [iteratingCurrentTask, setIteratingCurrentTask] = useState(false)
  const [error, setError] = useState('')

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
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
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

  const updateTaskStatus = useTaskStore(s => s.updateTaskStatus)
  const setExecuting = useTaskStore(s => s.setExecuting)
  const setStatus = useTaskStore(s => s.setStatus)
  const clearPlan = useTaskStore(s => s.clearPlan)
  const clearClarification = useTaskStore(s => s.clearClarification)

  const handleSubmit = async (mode: 'iterate' | 'new_task') => {
    if (!instruction.trim() || streaming) return

    setStreaming(true)
    setCreatedTaskId('')
    setIteratingCurrentTask(false)
    setError('')

    if (abortRef.current) abortRef.current.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl

    try {
      const endpoint =
        mode === 'iterate'
          ? `/api/tasks/${taskId}/iterate-selection`
          : `/api/tasks/${taskId}/process-selection`

      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          node_id: pending.nodeId,
          selected_text: pending.selectedText,
          instruction: instruction.trim(),
        }),
        signal: ctrl.signal,
      })

      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data?.detail || data?.message || t('common.requestFailed'))

      if (mode === 'iterate') {
        clearPlan()
        clearClarification()
        updateTaskStatus('planning')
        setStatus(t('processSelection.iterPlanning'))
        setExecuting(true)
        connectTaskStream(taskId)
        setIteratingCurrentTask(true)
        setStreaming(false)
        onClose()
        return
      }

      setCreatedTaskId(String(data.task_id || ''))
      setStreaming(false)
    } catch (err) {
      if ((err as Error)?.name !== 'AbortError') {
        setError((err as Error)?.message || t('processSelection.requestFailed'))
      }
      setStreaming(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'Enter' || e.shiftKey) return
    if (e.nativeEvent.isComposing || e.keyCode === 229) return
    e.preventDefault()
    handleSubmit('iterate')
  }

  const anchorRect = pending.anchorRect
  const width = Math.min(460, window.innerWidth - 24)
  const margin = 12
  const left = Math.max(margin, Math.min(anchorRect.left, window.innerWidth - width - margin))
  const spaceBelow = window.innerHeight - anchorRect.bottom
  const placeAbove = spaceBelow < 420
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
          <Sparkles size={14} />
          {t('processSelection.title')}
        </span>
        <button type="button" className="ann-popover__close" onClick={onClose} aria-label={t('processSelection.closeAria')}>
          <X size={14} />
        </button>
      </div>

      <div className="ann-popover__quote">
        <Quote size={11} />
        <span>
          {pending.selectedText.length > 160
            ? pending.selectedText.slice(0, 160) + '…'
            : pending.selectedText}
        </span>
      </div>

      <div className="ann-popover__composer">
        <textarea
          ref={inputRef}
          className="ann-popover__input"
          rows={3}
          placeholder={t('processSelection.placeholder')}
          value={instruction}
          onChange={e => setInstruction(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={streaming}
        />
        <div className="ann-popover__composer-actions">
          {createdTaskId && <span className="ann-popover__saved">{t('processSelection.taskCreatedTag', { id: createdTaskId })}</span>}
          <div className="ann-popover__action-group">
            <button
              type="button"
              className="btn btn-sm btn-primary"
              onClick={() => handleSubmit('iterate')}
              disabled={streaming || !instruction.trim() || Boolean(createdTaskId) || iteratingCurrentTask}
            >
              {streaming && !createdTaskId && !iteratingCurrentTask
                ? <Loader size={13} className="animate-spin" />
                : <Send size={13} />}
              {streaming ? t('processSelection.processing') : iteratingCurrentTask ? t('processSelection.sentIterate') : t('processSelection.continueIterate')}
            </button>
            <button
              type="button"
              className="btn btn-sm btn-secondary"
              onClick={() => handleSubmit('new_task')}
              disabled={streaming || !instruction.trim() || Boolean(createdTaskId) || iteratingCurrentTask}
            >
              {t('processSelection.deriveTask')}
            </button>
          </div>
        </div>
      </div>

      {createdTaskId && (
        <div className="ann-popover__answer ann-popover__answer--composer">
          <p className="ann-popover__result-line">
            {t('processSelection.newTaskHint')}
          </p>
          <button
            type="button"
            className="btn btn-sm btn-secondary"
            onClick={() => navigate(`/tasks/${createdTaskId}`)}
          >
            {t('processSelection.viewTask')}
            <ArrowUpRight size={13} />
          </button>
        </div>
      )}

      {error && (
        <div className="ann-popover__answer ann-popover__answer--composer">
          <p className="ann-popover__result-line">{error}</p>
        </div>
      )}
    </div>
  )
}
