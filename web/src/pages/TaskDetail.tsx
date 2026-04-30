import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTaskStore } from '../stores/taskStore'
import { api } from '../api/client'
import { connectTaskStream } from '../api/sse'
import PlanReview from '../components/task/PlanReview'
import ClarificationForm from '../components/task/ClarificationForm'
import ExecMonitor from '../components/task/ExecMonitor'
import ResultView from '../components/task/ResultView'
import SandboxDock from '../components/task/SandboxDock'
import { useI18n } from '../i18n/useI18n'
import { ArrowLeft, Box, FileText, GitBranch, Loader, OctagonX, RefreshCw, RotateCcw } from 'lucide-react'
import './TaskDetail.css'

const TERMINAL = ['completed', 'completed_with_blockers', 'timeout_killed', 'failed']

type ResultViewMode = 'result' | 'flow'

export default function TaskDetail() {
  const { id: taskId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { t } = useI18n()
  const [terminating, setTerminating] = useState(false)
  const [showTerminateConfirm, setShowTerminateConfirm] = useState(false)
  const [rePlanning, setRePlanning] = useState(false)
  const [rerunning, setRerunning] = useState(false)
  const [resultViewMode, setResultViewMode] = useState<ResultViewMode>('result')
  const [sandboxDockOpen, setSandboxDockOpen] = useState(true)

  const currentTask = useTaskStore(s => s.currentTask)
  const nodes = useTaskStore(s => s.nodes)
  const isExecuting = useTaskStore(s => s.isExecuting)
  const fetchTask = useTaskStore(s => s.fetchTask)
  const setExecuting = useTaskStore(s => s.setExecuting)
  const currentTaskId = currentTask?.id
  const currentTaskStatus = currentTask?.status

  useEffect(() => {
    if (!taskId) return
    const store = useTaskStore.getState()
    store.resetCurrent()
    store.setExpectedTaskId(taskId)
    fetchTask(taskId)
    return () => {
      const s = useTaskStore.getState()
      if (s.expectedTaskId === taskId) s.setExpectedTaskId(null)
    }
  }, [taskId, fetchTask])

  useEffect(() => {
    if (!currentTaskId || !currentTaskStatus || !taskId) return
    if (currentTaskId !== taskId) return
    if (!isExecuting && (currentTaskStatus === 'planning' || currentTaskStatus === 'executing')) {
      setExecuting(true)
      const ctrl = connectTaskStream(taskId)
      return () => ctrl.abort()
    }
  }, [currentTaskId, currentTaskStatus, taskId, isExecuting, setExecuting])

  if (!currentTask) {
    return (
      <div className="td__loading">
        <Loader size={24} className="animate-pulse" />
        <span>{t('taskDetail.loading')}</span>
      </div>
    )
  }

  if (currentTask.status === 'failed' && !currentTask.question) {
    return (
      <div className="td__loading" style={{ flexDirection: 'column', gap: 12 }}>
        <span style={{ color: '#ef4444', fontWeight: 600 }}>{t('taskDetail.loadFailedTitle')}</span>
        <span style={{ opacity: 0.7, fontSize: 13 }}>
          {t('taskDetail.loadFailedDesc')}
        </span>
        <button
          type="button"
          onClick={() => taskId && fetchTask(taskId)}
          style={{
            padding: '6px 14px',
            border: '1px solid #d1d5db',
            borderRadius: 6,
            background: 'white',
            cursor: 'pointer',
          }}
        >
          {t('common.reload')}
        </button>
      </div>
    )
  }

  const { status, question } = currentTask
  const iterations = currentTask.iterations ?? []
  const currentIterationIndex = Math.max(
    1,
    iterations.findIndex(iter => iter.id === currentTask.current_iteration_id) + 1,
  )

  const handleTerminate = async () => {
    if (!taskId) return
    setTerminating(true)
    try {
      await api.terminateTask(taskId)
      await fetchTask(taskId)
    } catch { /* ignore */ }
    finally {
      setTerminating(false)
      setShowTerminateConfirm(false)
    }
  }

  const handleRePlan = async () => {
    if (!taskId || !currentTask.studio_id) return
    setRePlanning(true)
    try {
      await api.proceedTask(taskId, {
        feedback: t('taskDetail.feedbackReplanAfterTerminate'),
        route_cmd: { studio_id: currentTask.studio_id },
      })
      await fetchTask(taskId)
    } catch { /* ignore */ }
    finally { setRePlanning(false) }
  }

  const isTerminal = TERMINAL.includes(status)
  const showFlowInResult = isTerminal && resultViewMode === 'flow'

  const breadcrumb =
    status === 'terminated' ? t('taskDetail.breadcrumbTerminated')
    : status === 'failed' ? t('taskDetail.breadcrumbFailed')
    : isTerminal ? (showFlowInResult ? t('taskDetail.breadcrumbResultFlow') : t('taskDetail.breadcrumbResult'))
    : status === 'await_leader_plan_approval' ? t('taskDetail.breadcrumbPlanApproval')
    : status === 'need_clarification' ? t('taskDetail.breadcrumbClarification')
    : status === 'executing' ? t('taskDetail.breadcrumbExec')
    : t('taskDetail.breadcrumbPlanning')

  const canTerminate = status === 'executing' || status === 'planning'

  const wrapperModifier = [
    'td--workspace',
    status === 'terminated' ? ''
    : showFlowInResult ? 'td--full'
    : isTerminal ? 'td--result'
    : 'td--full',
  ].filter(Boolean).join(' ')

  const taskSurface = status === 'terminated' ? (
    <TerminatedView
      planSteps={currentTask.plan_steps}
      nodes={nodes}
      onRePlan={handleRePlan}
      rePlanning={rePlanning}
      onRerunOriginal={async () => {
        if (!taskId) return
        setRerunning(true)
        try {
          await api.rerunOriginal(taskId)
          await fetchTask(taskId)
          connectTaskStream(taskId)
        } catch (e: unknown) {
          const msg = e instanceof Error ? e.message : String(e)
          alert(t('taskDetail.rerunFailed') + msg)
        } finally {
          setRerunning(false)
        }
      }}
      rerunning={rerunning}
    />
  ) : status === 'need_clarification' ? (
    <ClarificationForm taskId={currentTask.id} question={question} />
  ) : status === 'await_leader_plan_approval' ? (
    <PlanReview taskId={currentTask.id} question={question} />
  ) : isTerminal ? (
    showFlowInResult ? (
      <ExecMonitor />
    ) : (
      <ResultView
        taskId={currentTask.id}
        question={question}
        status={status}
        nodes={nodes}
        studioId={currentTask.studio_id}
        statusMessage={currentTask.status_message}
      />
    )
  ) : (
    <ExecMonitor />
  )

  const iterationLabel =
    `${t('taskDetail.iterationRound', { current: currentIterationIndex })}` +
    (iterations.length > 1 ? t('taskDetail.iterationTotal', { total: iterations.length }) : '')

  return (
    <div className={`td ${wrapperModifier}`}>
      <div className="td__topbar">
        <button type="button" className="btn btn-icon" onClick={() => navigate('/tasks')}>
          <ArrowLeft size={18} />
        </button>
        <span className="td__breadcrumb">{t('taskDetail.breadcrumbRoot')} / {breadcrumb}</span>
        <span className="td__iteration-pill">
          {iterationLabel}
        </span>
        {isTerminal && (
          <div className="td__view-toggle" role="tablist" aria-label={t('taskDetail.viewToggleAria')}>
            <button
              type="button"
              role="tab"
              aria-selected={resultViewMode === 'result'}
              className={`td__view-toggle-btn ${resultViewMode === 'result' ? 'td__view-toggle-btn--active' : ''}`}
              onClick={() => setResultViewMode('result')}
            >
              <FileText size={14} /> {t('taskDetail.tabResult')}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={resultViewMode === 'flow'}
              className={`td__view-toggle-btn ${resultViewMode === 'flow' ? 'td__view-toggle-btn--active' : ''}`}
              onClick={() => setResultViewMode('flow')}
            >
              <GitBranch size={14} /> {t('taskDetail.tabFlow')}
            </button>
          </div>
        )}
        <div className="td__topbar-actions">
          <button
            type="button"
            className={`td__side-toggle ${sandboxDockOpen ? 'td__side-toggle--active' : ''}`}
            onClick={() => setSandboxDockOpen(open => !open)}
            title={sandboxDockOpen ? t('taskDetail.sandboxCollapse') : t('taskDetail.sandboxExpand')}
          >
            <Box size={14} />
            {t('taskDetail.sandbox')}
          </button>
          {status === 'planning' && (
            <span className="td__hint animate-pulse">{t('taskDetail.planningHint')}</span>
          )}
          {canTerminate && !showTerminateConfirm && (
            <button
              type="button"
              className="btn btn-danger-outline"
              onClick={() => setShowTerminateConfirm(true)}
              disabled={terminating}
            >
              <OctagonX size={14} /> {t('taskDetail.terminateTask')}
            </button>
          )}
          {showTerminateConfirm && (
            <div className="td__terminate-confirm">
              <span>{t('taskDetail.terminateConfirm')}</span>
              <button type="button" className="btn btn-danger" onClick={handleTerminate} disabled={terminating}>
                {terminating ? <Loader size={13} className="animate-pulse" /> : <OctagonX size={13} />}
                {terminating ? t('taskDetail.terminating') : t('taskDetail.confirmTerminate')}
              </button>
              <button type="button" className="btn btn-secondary" onClick={() => setShowTerminateConfirm(false)}>
                {t('common.cancel')}
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="td__workspace">
        <main className="td__workspace-main">
          {taskSurface}
        </main>
        {sandboxDockOpen && (
          <SandboxDock taskId={currentTask.id} onClose={() => setSandboxDockOpen(false)} />
        )}
      </div>
    </div>
  )
}

function TerminatedView({
  planSteps,
  nodes,
  onRePlan,
  rePlanning,
  onRerunOriginal,
  rerunning,
}: {
  planSteps: { id: string; step_label: string; assign_to_role: string; input_context: string }[]
  nodes: ReturnType<typeof useTaskStore.getState>['nodes']
  onRePlan: () => void
  rePlanning: boolean
  onRerunOriginal: () => void
  rerunning: boolean
}) {
  const navigate = useNavigate()
  const { t } = useI18n()

  return (
    <div className="td__terminated">
      <div className="td__terminated-banner">
        <OctagonX size={22} />
        <div>
          <h3>{t('taskDetail.terminatedBannerTitle')}</h3>
          <p>{t('taskDetail.terminatedBannerDesc')}</p>
        </div>
      </div>

      <div className="td__terminated-body">
        {nodes.filter(n => n.status === 'completed').length > 0 && (
          <div className="td__terminated-section">
            <h4>{t('taskDetail.completedSteps')}</h4>
            <div className="td__terminated-nodes">
              {nodes.filter(n => n.status === 'completed').map(n => (
                <div key={n.id} className="td__terminated-node">
                  <span className="td__terminated-node-role">{n.agent_role}</span>
                  <span className="td__terminated-node-label">{n.step_label}</span>
                  <span className="status-badge status--completed">{t('taskDetail.completedBadge')}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {planSteps.length > 0 && (
          <div className="td__terminated-section">
            <h4>{t('taskDetail.originalPlanTitle')}</h4>
            <div className="td__terminated-steps">
              {planSteps.map((s, i) => (
                <div key={s.id} className="td__terminated-step">
                  <span className="td__step-num">{i + 1}</span>
                  <div>
                    <div className="td__step-label">{s.step_label}</div>
                    <div className="td__step-role">{s.assign_to_role}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="td__terminated-actions">
          <button type="button" className="btn btn-primary" onClick={onRePlan} disabled={rePlanning}>
            {rePlanning
              ? <><Loader size={14} className="animate-pulse" /> {t('taskDetail.rePlanning')}</>
              : <><RefreshCw size={14} /> {t('taskDetail.rePlan')}</>}
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={onRerunOriginal}
            disabled={rerunning || !planSteps.length}
          >
            {rerunning
              ? <><Loader size={14} className="animate-pulse" /> {t('taskDetail.starting')}</>
              : <><RotateCcw size={14} /> {t('taskDetail.rerunWithPlan')}</>}
          </button>
          <button type="button" className="btn btn-secondary" onClick={() => navigate('/tasks')}>
            {t('taskDetail.backToBoard')}
          </button>
        </div>
      </div>
    </div>
  )
}
