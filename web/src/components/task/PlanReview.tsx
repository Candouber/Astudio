import { useState } from 'react'
import { useTaskStore } from '../../stores/taskStore'
import { api } from '../../api/client'
import { connectTaskStream } from '../../api/sse'
import { useI18n } from '../../i18n/useI18n'
import PlanStepCard from './PlanStepCard'
import { CheckCircle, RotateCcw, Send, FileText, Loader } from 'lucide-react'
import './PlanReview.css'

interface Props {
  taskId: string
  question: string
}

export default function PlanReview({ taskId, question }: Props) {
  const { t } = useI18n()
  const planSteps = useTaskStore(s => s.planSteps)
  const planStudioId = useTaskStore(s => s.planStudioId)
  const currentTask = useTaskStore(s => s.currentTask)
  const annotations = useTaskStore(s => s.annotations)
  const clearPlan = useTaskStore(s => s.clearPlan)
  const setExecuting = useTaskStore(s => s.setExecuting)
  const updateTaskStatus = useTaskStore(s => s.updateTaskStatus)

  const [rejecting, setRejecting] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [loading, setLoading] = useState(false)
  const persistedSteps = currentTask?.plan_steps || []
  const effectivePlanSteps = planSteps.length > 0 ? planSteps : persistedSteps
  const effectiveStudioId = planStudioId || currentTask?.plan_studio_id || currentTask?.studio_id || ''

  const handleApprove = async () => {
    setLoading(true)
    try {
      const annotatedSteps = effectivePlanSteps.map(step => ({
        ...step,
        input_context: annotations[step.id]
          ? `${step.input_context}${t('planReview.userAnnotationSuffix')}${annotations[step.id]}`
          : step.input_context,
      }))

      await api.proceedTask(taskId, {
        route_cmd: { studio_id: effectiveStudioId, steps: annotatedSteps },
      })

      clearPlan()
      updateTaskStatus('executing')
      setExecuting(true)
      connectTaskStream(taskId)
    } catch (err) {
      console.error('Failed to proceed:', err)
    } finally {
      setLoading(false)
    }
  }

  const handleReject = async () => {
    if (!feedback.trim()) return
    setLoading(true)
    try {
      await api.proceedTask(taskId, {
        route_cmd: { studio_id: effectiveStudioId, steps: effectivePlanSteps },
        feedback: feedback.trim(),
      })

      clearPlan()
      updateTaskStatus('planning')
      setExecuting(true)
      connectTaskStream(taskId)
    } catch (err) {
      console.error('Failed to reject:', err)
    } finally {
      setLoading(false)
      setRejecting(false)
      setFeedback('')
    }
  }

  const handleRetryPlan = async () => {
    const fallbackStudioId = planStudioId || currentTask?.plan_studio_id || currentTask?.studio_id
    setLoading(true)
    try {
      await api.proceedTask(taskId, {
        route_cmd: fallbackStudioId ? { studio_id: fallbackStudioId } : {},
        feedback: t('planReview.apiFeedbackMissing'),
      })

      clearPlan()
      updateTaskStatus('planning')
      setExecuting(true)
      connectTaskStream(taskId)
    } catch (err) {
      console.error('Failed to retry plan:', err)
    } finally {
      setLoading(false)
    }
  }

  if (effectivePlanSteps.length === 0) {
    // currentTask 已加载，但 plan_steps 在 DB 中也是空（旧任务或方案数据丢失）
    if (currentTask) {
      return (
        <div className="plan-review plan-review--loading">
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
            {t('planReview.planMissingTitle')}
          </p>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.82rem' }}>
            {t('planReview.planMissingHint')}
          </p>
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleRetryPlan}
            disabled={loading}
          >
            <RotateCcw size={16} />
            {loading ? t('planReview.regenPlanning') : t('planReview.regenPlan')}
          </button>
        </div>
      )
    }
    // currentTask 还没加载完，真正的 loading 状态
    return (
      <div className="plan-review plan-review--loading">
        <Loader size={24} className="animate-pulse" />
        <p>{t('planReview.loadingPlan')}</p>
      </div>
    )
  }

  return (
    <div className="plan-review">
      {/* Header */}
      <div className="plan-review__header card">
        <div className="plan-review__title-row">
          <FileText size={20} className="plan-review__icon" />
          <div>
            <h2>{t('planReview.title')}</h2>
            <p className="plan-review__question">{question}</p>
          </div>
        </div>
        <div className="plan-review__meta">
          <span className="status-badge status--await_leader_plan_approval">{t('planReview.badge')}</span>
          <span className="plan-review__step-count">{t('planReview.stepCount', { count: effectivePlanSteps.length })}</span>
        </div>
      </div>

      {/* Steps */}
      <div className="plan-review__steps">
        {effectivePlanSteps.map((step, i) => (
          <PlanStepCard
            key={step.id}
            step={step}
            index={i}
            allSteps={effectivePlanSteps}
          />
        ))}
      </div>

      {/* Action Bar */}
      <div className="plan-review__actions card">
        {!rejecting ? (
          <>
            <button
              type="button"
              className="btn btn-primary plan-review__approve"
              onClick={handleApprove}
              disabled={loading}
            >
              <CheckCircle size={16} />
              {loading ? t('planReview.submitting') : t('planReview.approveExecute')}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setRejecting(true)}
              disabled={loading}
            >
              <RotateCcw size={16} />
              {t('planReview.rejectEdit')}
            </button>
          </>
        ) : (
          <div className="plan-review__reject-form">
            <textarea
              className="input-base"
              placeholder={t('planReview.rejectPlaceholder')}
              rows={3}
              value={feedback}
              onChange={e => setFeedback(e.target.value)}
              autoFocus
            />
            <div className="plan-review__reject-actions">
              <button
                type="button"
                className="btn btn-danger"
                onClick={handleReject}
                disabled={loading || !feedback.trim()}
              >
                <Send size={14} />
                {loading ? t('planReview.submitting') : t('planReview.submitFeedback')}
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => { setRejecting(false); setFeedback('') }}
                disabled={loading}
              >
                {t('common.cancel')}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
