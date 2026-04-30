import { useState } from 'react'
import { useTaskStore } from '../../stores/taskStore'
import { parseSSEStream } from '../../api/sse'
import { useI18n } from '../../i18n/useI18n'
import { HelpCircle, Send, Loader } from 'lucide-react'
import './ClarificationForm.css'

interface Props {
  taskId: string
  question: string
}

export default function ClarificationForm({ taskId, question }: Props) {
  const { t } = useI18n()
  const clarificationQuestions = useTaskStore(s => s.clarificationQuestions)
  const clarificationStudioId = useTaskStore(s => s.clarificationStudioId)
  const currentTask = useTaskStore(s => s.currentTask)
  const clearClarification = useTaskStore(s => s.clearClarification)
  const setPlanSteps = useTaskStore(s => s.setPlanSteps)
  const updateTaskStatus = useTaskStore(s => s.updateTaskStatus)

  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(false)
  const [statusText, setStatusText] = useState('')

  const questions =
    clarificationQuestions.length > 0
      ? clarificationQuestions
      : (currentTask?.clarification_questions ?? [])

  if (questions.length === 0) {
    return (
      <div className="clarification-form clarification-form--empty">
        <p>{t('clarification.noData')}</p>
      </div>
    )
  }

  const handleSubmit = async () => {
    const defaultAns = t('clarification.defaultAnswer')
    const submittedAnswers = Object.fromEntries(
      questions.map(q => [q.id, (answers[q.id] || '').trim() || defaultAns]),
    )
    setLoading(true)
    setStatusText(t('clarification.submitting'))
    try {
      const res = await fetch(`/api/tasks/${taskId}/clarify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ answers: submittedAnswers }),
      })
      if (!res.ok || !res.body) throw new Error(t('common.requestFailed'))

      parseSSEStream(
        res.body.getReader(),
        (event, rawData) => {
          try {
            const data = JSON.parse(rawData)
            if (event === 'status') {
              setStatusText(data.message || '')
              if (data.status && data.status !== 'await_leader_plan_approval') {
                updateTaskStatus(data.status)
              }
            }
            if (event === 'done_pause' && data.action === 'review_plan') {
              const steps = (data.steps || []) as never[]
              if (steps.length === 0) {
                setStatusText(t('clarification.emptyPlan'))
                return
              }
              setPlanSteps(steps, clarificationStudioId)
              clearClarification()
              updateTaskStatus('await_leader_plan_approval')
              setStatusText('')
            }
          } catch { /* ignore */ }
        },
        () => setLoading(false),
      )
    } catch {
      setStatusText(t('clarification.submitFailed'))
      setLoading(false)
    }
  }

  return (
    <div className="clarification-form">
      <div className="clarification-form__header card">
        <div className="clarification-form__title-row">
          <HelpCircle size={20} className="clarification-form__icon" />
          <div>
            <h2>{t('clarification.title')}</h2>
            <p className="clarification-form__question">{question}</p>
          </div>
        </div>
        <span className="status-badge status--need_clarification">{t('clarification.badge')}</span>
      </div>

      <div className="clarification-form__body">
        <p className="clarification-form__desc">
          {t('clarification.desc')}
        </p>

        {questions.map((q, i) => (
          <div key={q.id} className="clarification-form__item card">
            <label className="clarification-form__label">
              <span className="clarification-form__index">Q{i + 1}</span>
              {q.question}
            </label>
            <textarea
              className="input-base clarification-form__textarea"
              rows={3}
              placeholder={t('clarification.placeholder')}
              value={answers[q.id] || ''}
              onChange={e => setAnswers(prev => ({ ...prev, [q.id]: e.target.value }))}
              disabled={loading}
            />
          </div>
        ))}
      </div>

      <div className="clarification-form__footer card">
        {statusText && (
          <p className="clarification-form__status">
            {loading && <Loader size={14} className="inline-icon animate-spin" />}
            {statusText}
          </p>
        )}
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleSubmit}
          disabled={loading}
        >
          {loading ? <Loader size={16} className="animate-spin" /> : <Send size={16} />}
          {loading ? t('clarification.planning') : t('clarification.submitCta')}
        </button>
      </div>
    </div>
  )
}
