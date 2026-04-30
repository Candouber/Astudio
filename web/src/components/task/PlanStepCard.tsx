import type { PlanStep } from '../../types'
import { useTaskStore } from '../../stores/taskStore'
import { User, GitBranch, FileText, MessageSquareText } from 'lucide-react'
import MarkdownRenderer from '../common/MarkdownRenderer'
import { useI18n } from '../../i18n/useI18n'
import './PlanStepCard.css'

interface Props {
  step: PlanStep
  index: number
  allSteps: PlanStep[]
}

export default function PlanStepCard({ step, index, allSteps }: Props) {
  const { t } = useI18n()
  const annotation = useTaskStore(s => s.annotations[step.id] || '')
  const setAnnotation = useTaskStore(s => s.setAnnotation)

  const depLabels = step.depends_on
    .map(depId => allSteps.find(s => s.id === depId)?.step_label)
    .filter(Boolean)

  return (
    <div className="plan-step card">
      <div className="plan-step__header">
        <span className="plan-step__index">{index + 1}</span>
        <div className="plan-step__title-group">
          <h4 className="plan-step__title">{step.step_label}</h4>
          <div className="plan-step__meta">
            <span className="plan-step__role">
              <User size={12} />
              {step.assign_to_role}
            </span>
            {depLabels.length > 0 && (
              <span className="plan-step__dep-count">
                <GitBranch size={12} />
                {t('planStepCard.depsCount', { n: depLabels.length })}
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="plan-step__section">
        <div className="plan-step__section-head">
          <FileText size={14} />
          <span>{t('planStepCard.execBrief')}</span>
        </div>
        <div className="plan-step__content">
          <MarkdownRenderer content={step.input_context} />
        </div>
      </div>

      {depLabels.length > 0 && (
        <div className="plan-step__section">
          <div className="plan-step__section-head">
            <GitBranch size={14} />
            <span>{t('planStepCard.depsPrereq')}</span>
          </div>
          <div className="plan-step__deps">
            {depLabels.map((label, i) => (
              <span key={i} className="plan-step__dep-tag">{label}</span>
            ))}
          </div>
        </div>
      )}

      <div className="plan-step__section plan-step__section--annotation">
        <div className="plan-step__section-head">
          <MessageSquareText size={14} />
          <span>{t('planStepCard.approvalNote')}</span>
        </div>
        <textarea
          className="input-base plan-step__annotation-input"
          placeholder={t('planStepCard.annotationPlaceholder')}
          rows={2}
          value={annotation}
          onChange={e => setAnnotation(step.id, e.target.value)}
        />
      </div>
    </div>
  )
}
