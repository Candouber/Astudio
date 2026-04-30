import { useTaskStore } from '../../stores/taskStore'
import { X, ChevronDown, ChevronUp, RotateCcw, Send, Loader, GitBranch } from 'lucide-react'
import { useState } from 'react'
import { parseSSEStream } from '../../api/sse'
import { connectTaskStream } from '../../api/sse'
import { useI18n } from '../../i18n/useI18n'
import { translateStatusMessage } from '../../i18n/translateStatusMessage'
import './SubTaskPanel.css'

const STATUS_KEYS: Record<string, string> = {
  pending: 'subTaskPanel.statusPending',
  running: 'subTaskPanel.statusRunning',
  pending_review: 'subTaskPanel.statusPendingReview',
  revision_requested: 'subTaskPanel.statusRevisionRequested',
  completed: 'subTaskPanel.statusCompleted',
  accepted: 'subTaskPanel.statusAccepted',
  blocked: 'subTaskPanel.statusBlocked',
  error: 'subTaskPanel.statusError',
}

function nodeStatusLabel(status: string, t: (path: string) => string) {
  const key = STATUS_KEYS[status]
  return key ? t(key) : status
}

export default function SubTaskPanel() {
  const { t, locale } = useI18n()
  const selectedNodeId = useTaskStore(s => s.selectedNodeId)
  const nodes = useTaskStore(s => s.nodes)
  const currentTask = useTaskStore(s => s.currentTask)
  const streamBuffers = useTaskStore(s => s.streamBuffers)
  const selectNode = useTaskStore(s => s.selectNode)
  const updateTaskStatus = useTaskStore(s => s.updateTaskStatus)
  const setExecuting = useTaskStore(s => s.setExecuting)
  const setStatus = useTaskStore(s => s.setStatus)
  const clearPlan = useTaskStore(s => s.clearPlan)
  const clearClarification = useTaskStore(s => s.clearClarification)

  const [showFullOutput, setShowFullOutput] = useState(false)
  const [retryOpen, setRetryOpen] = useState(false)
  const [retryText, setRetryText] = useState('')
  const [retrying, setRetrying] = useState(false)
  const [retryStatus, setRetryStatus] = useState('')
  const [iterateOpen, setIterateOpen] = useState(false)
  const [iterateText, setIterateText] = useState('')
  const [iterating, setIterating] = useState(false)
  const [iterateStatus, setIterateStatus] = useState('')

  const node = nodes.find(n => n.id === selectedNodeId)
  if (!node) return null

  const streamContent = streamBuffers[node.id] || ''
  const displayOutput = node.output || streamContent
  const isCompleted = node.status === 'completed'
  const isBlocked = node.status === 'error'
  const isSkipped = isBlocked && node.output?.startsWith('[SKIPPED]')
  const canRetry = (isBlocked && !isSkipped) || isCompleted

  // 阻塞原因（去掉前缀标签）
  const blockerReason = displayOutput
    .replace(/^\[BLOCKED\]\s*/, '')
    .replace(/^\[SKIPPED\]\s*/, '')
    .replace(/^\[CRASH\]:\s*/, '')

  const handleRetry = async () => {
    // 完成节点可以不填额外信息直接重新执行
    if (!isCompleted && !retryText.trim()) return
    if (!currentTask) return
    setRetrying(true)
    setRetryStatus(t('subTaskPanel.submitting'))
    try {
      const res = await fetch(`/api/tasks/${currentTask.id}/retry-step`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ node_id: node.id, extra_context: retryText }),
      })
      if (!res.ok || !res.body) throw new Error(t('common.requestFailed'))
      updateTaskStatus('executing')
      parseSSEStream(
        res.body.getReader(),
        (ev, raw) => {
          try {
            const data = JSON.parse(raw)
            if (ev === 'status') setRetryStatus(data.message || '')
            if (ev === 'done') { setRetrying(false); setRetryOpen(false); setRetryText('') }
          } catch { /* ignore */ }
        },
        () => setRetrying(false),
      )
    } catch {
      setRetryStatus(t('subTaskPanel.requestFailedRetry'))
      setRetrying(false)
    }
  }

  const handleIterate = async () => {
    if (!currentTask || !iterateText.trim() || iterating) return
    setIterating(true)
    setIterateStatus(t('subTaskPanel.submitting'))
    const selectedText = (displayOutput || node.input || node.step_label || '').slice(0, 6000)
    try {
      const res = await fetch(`/api/tasks/${currentTask.id}/iterate-selection`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          node_id: node.id,
          selected_text: selectedText || node.step_label,
          instruction: iterateText.trim(),
        }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data?.detail || data?.message || t('common.requestFailed'))
      clearPlan()
      clearClarification()
      updateTaskStatus('planning')
      setStatus(t('subTaskPanel.iteratePlanning'))
      setExecuting(true)
      connectTaskStream(currentTask.id)
      setIterateOpen(false)
      setIterateText('')
      setIterateStatus('')
    } catch {
      setIterateStatus(t('subTaskPanel.iterateFailed'))
    } finally {
      setIterating(false)
    }
  }

  return (
    <div className="subtask-panel">
      {/* Header */}
      <div className="subtask-panel__header">
        <div>
          <div className="subtask-panel__role">{node.agent_role}</div>
          <h3 className="subtask-panel__title">{node.step_label}</h3>
        </div>
        <button type="button" className="btn btn-icon" onClick={() => selectNode(null)}>
          <X size={16} />
        </button>
      </div>

      <div className={`status-badge status--${node.status}`}>
        {nodeStatusLabel(node.status, t)}
      </div>

      {/* Input */}
      {node.input && (
        <div className="subtask-panel__section">
          <h4 className="subtask-panel__section-title">{t('subTaskPanel.briefing')}</h4>
          <div className="subtask-panel__content-box">{node.input}</div>
        </div>
      )}

      {/* Blocked reason (清晰展示，不截断) */}
      {isBlocked && blockerReason && (
        <div className="subtask-panel__section">
          <h4 className="subtask-panel__section-title" style={{ color: isSkipped ? '#b45309' : 'var(--accent-danger)' }}>
            {isSkipped ? t('subTaskPanel.skipReason') : t('subTaskPanel.blockReason')}
          </h4>
          <div className="subtask-panel__content-box subtask-panel__blocker-text">
            {blockerReason}
          </div>
        </div>
      )}

      {/* Output (only for non-blocked nodes) */}
      {!isBlocked && displayOutput && (
        <div className="subtask-panel__section">
          <div className="subtask-panel__section-header">
            <h4 className="subtask-panel__section-title">{t('subTaskPanel.outputTitle')}</h4>
            {displayOutput.length > 300 && (
              <button type="button" className="btn btn-icon" onClick={() => setShowFullOutput(!showFullOutput)}>
                {showFullOutput ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              </button>
            )}
          </div>
          <div className={`subtask-panel__content-box subtask-panel__output ${!showFullOutput && displayOutput.length > 300 ? 'subtask-panel__output--collapsed' : ''}`}>
            {displayOutput}
            {node.status === 'running' && <span className="subtask-panel__cursor">▍</span>}
          </div>
        </div>
      )}

      {/* Retry section */}
      {canRetry && (
        <div className="subtask-panel__section">
          {!retryOpen ? (
            <button
              type="button"
              className={`btn ${isCompleted ? 'btn-secondary' : 'btn-warning'}`}
              style={{ width: '100%' }}
              onClick={() => setRetryOpen(true)}
            >
              <RotateCcw size={14} />
              {isCompleted ? t('subTaskPanel.rerunCompleted') : t('subTaskPanel.rerunBlocked')}
            </button>
          ) : (
            <div className="subtask-panel__retry-form">
              <h4 className="subtask-panel__section-title">
                {isCompleted ? t('subTaskPanel.retryExecTitle') : t('subTaskPanel.retrySupplementTitle')}
              </h4>
              <textarea
                className="input-base"
                rows={4}
                placeholder={
                  isCompleted
                    ? t('subTaskPanel.phRetryCompleted')
                    : t('subTaskPanel.phRetryBlocked')
                }
                value={retryText}
                onChange={e => setRetryText(e.target.value)}
                disabled={retrying}
                autoFocus
              />
              {retryStatus && (
                <p className="subtask-panel__retry-status">
                  {retrying && <Loader size={12} className="animate-spin" />}
                  {translateStatusMessage(locale, retryStatus)}
                </p>
              )}
              <div className="subtask-panel__retry-actions">
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={handleRetry}
                  disabled={retrying || (!isCompleted && !retryText.trim())}
                >
                  <Send size={13} />
                  {retrying ? t('subTaskPanel.executing') : t('subTaskPanel.confirm')}
                </button>
                <button type="button" className="btn btn-secondary" onClick={() => { setRetryOpen(false); setRetryText('') }} disabled={retrying}>
                  {t('common.cancel')}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="subtask-panel__section">
        {!iterateOpen ? (
          <button
            type="button"
            className="btn btn-primary"
            style={{ width: '100%' }}
            onClick={() => setIterateOpen(true)}
          >
            <GitBranch size={14} />
            {t('subTaskPanel.iterateOpen')}
          </button>
        ) : (
          <div className="subtask-panel__retry-form">
            <h4 className="subtask-panel__section-title">{t('subTaskPanel.iterateSection')}</h4>
            <textarea
              className="input-base"
              rows={4}
              placeholder={t('subTaskPanel.iteratePlaceholder')}
              value={iterateText}
              onChange={e => setIterateText(e.target.value)}
              disabled={iterating}
              autoFocus
            />
            {iterateStatus && (
              <p className="subtask-panel__retry-status">
                {iterating && <Loader size={12} className="animate-spin" />}
                {iterateStatus}
              </p>
            )}
            <div className="subtask-panel__retry-actions">
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleIterate}
                disabled={iterating || !iterateText.trim()}
              >
                <Send size={13} />
                {iterating ? t('subTaskPanel.iterateSubmitting') : t('subTaskPanel.confirmIterate')}
              </button>
              <button type="button" className="btn btn-secondary" onClick={() => { setIterateOpen(false); setIterateText('') }} disabled={iterating}>
                {t('common.cancel')}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
