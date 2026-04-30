import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTaskStore } from '../stores/taskStore'
import type { Task, TaskStatus } from '../types'
import { getTaskStatusLabel } from '../utils/taskStatus'
import { useI18n } from '../i18n/useI18n'
import { Clock, CheckCircle, AlertTriangle, Play, FileSearch, Loader, Trash2, X } from 'lucide-react'
import './TaskBoard.css'

const STATUS_GROUPS: {
  key: string
  labelKey: string
  icon: typeof Play
  statuses: TaskStatus[]
  color: string
}[] = [
  {
    key: 'action',
    labelKey: 'taskBoard.filterAction',
    icon: AlertTriangle,
    statuses: ['need_clarification', 'await_leader_plan_approval'],
    color: 'var(--accent-warning)',
  },
  {
    key: 'active',
    labelKey: 'taskBoard.filterActive',
    icon: Play,
    statuses: ['planning', 'executing'],
    color: 'var(--accent-brand)',
  },
  {
    key: 'done',
    labelKey: 'taskBoard.filterDone',
    icon: CheckCircle,
    statuses: ['completed', 'completed_with_blockers', 'timeout_killed', 'terminated', 'failed'],
    color: 'var(--accent-success)',
  },
]

export default function TaskBoard() {
  const tasks = useTaskStore((s) => s.tasks)
  const fetchTasks = useTaskStore((s) => s.fetchTasks)
  const deleteTask = useTaskStore((s) => s.deleteTask)
  const [filter, setFilter] = useState<string>('all')
  const navigate = useNavigate()
  const { t } = useI18n()

  useEffect(() => {
    fetchTasks()
    const timer = setInterval(fetchTasks, 5000)
    return () => clearInterval(timer)
  }, [fetchTasks])

  const filtered = filter === 'all'
    ? tasks
    : tasks.filter(taskItem => {
        const group = STATUS_GROUPS.find(g => g.key === filter)
        return group?.statuses.includes(taskItem.status)
      })

  return (
    <div className="task-board">
      <div className="task-board__header">
        <h1>{t('taskBoard.title')}</h1>
        <div className="task-board__filters">
          <button
            className={`task-board__filter ${filter === 'all' ? 'task-board__filter--active' : ''}`}
            onClick={() => setFilter('all')}
          >
            {t('taskBoard.filterAll')} ({tasks.length})
          </button>
          {STATUS_GROUPS.map(g => {
            const count = tasks.filter(taskItem => g.statuses.includes(taskItem.status)).length
            return (
              <button
                key={g.key}
                className={`task-board__filter ${filter === g.key ? 'task-board__filter--active' : ''}`}
                onClick={() => setFilter(g.key)}
              >
                {t(g.labelKey)} ({count})
              </button>
            )
          })}
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="task-board__empty">
          <FileSearch size={40} />
          <p>{t('taskBoard.emptyTitle')}</p>
          <span>{t('taskBoard.emptyHint')}</span>
        </div>
      ) : (
        <div className="task-board__grid">
          {STATUS_GROUPS.map(g => {
            const items = filtered.filter(taskItem => g.statuses.includes(taskItem.status))
            if (items.length === 0) return null
            return (
              <div key={g.key} className="task-board__column">
                <div className="task-board__col-header" style={{ borderColor: g.color }}>
                  <g.icon size={16} style={{ color: g.color }} />
                  <span>{t(g.labelKey)}</span>
                  <span className="task-board__col-count">{items.length}</span>
                </div>
                <div className="task-board__col-body">
                  {items.map(taskItem => (
                    <TaskRow
                      key={taskItem.id}
                      task={taskItem}
                      onNavigate={() => navigate(`/tasks/${taskItem.id}`)}
                      onDelete={() => deleteTask(taskItem.id)}
                    />
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

/** Strip studio prefix from title (zh/en); truncate */
function extractTitle(question: string): string {
  const clean = question
    .replace(/^\[(?:目标工作室：|Target studio:)[^\]]+\]\s*/, '')
    .trim()
  return clean.length > 60 ? `${clean.slice(0, 60)}…` : clean
}

function TaskRow({
  task,
  onNavigate,
  onDelete,
}: {
  task: Task
  onNavigate: () => void
  onDelete: () => void
}) {
  const { t } = useI18n()
  const [confirming, setConfirming] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const needsClarification = task.status === 'need_clarification'

  const handleDeleteClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    setConfirming(true)
  }

  const handleConfirm = async (e: React.MouseEvent) => {
    e.stopPropagation()
    setDeleting(true)
    await onDelete()
    setDeleting(false)
    setConfirming(false)
  }

  const handleCancel = (e: React.MouseEvent) => {
    e.stopPropagation()
    setConfirming(false)
  }

  return (
    <div
      className={`task-row card ${needsClarification ? 'task-row--urgent' : ''}`}
      onClick={confirming ? undefined : onNavigate}
    >
      <div className="task-row__header">
        <div className="task-row__question">{extractTitle(task.question)}</div>
        {!confirming && (
          <button
            type="button"
            className="btn btn-icon task-row__delete"
            onClick={handleDeleteClick}
            title={t('taskBoard.deleteTaskTitle')}
          >
            <Trash2 size={14} />
          </button>
        )}
      </div>

      {confirming && (
        <div className="task-row__confirm" onClick={e => e.stopPropagation()}>
          <span>{t('taskBoard.confirmDelete')}</span>
          <button
            type="button"
            className="btn btn-danger task-row__confirm-yes"
            onClick={handleConfirm}
            disabled={deleting}
          >
            {deleting ? <Loader size={12} className="animate-pulse" /> : <Trash2 size={12} />}
            {deleting ? t('common.deleting') : t('common.delete')}
          </button>
          <button type="button" className="btn btn-icon" onClick={handleCancel}>
            <X size={14} />
          </button>
        </div>
      )}

      <div className="task-row__meta">
        <span className={`status-badge status--${task.status}`}>
          {getTaskStatusLabel(task.status)}
        </span>
        <span className="task-row__time">
          <Clock size={12} />
          {new Date(task.created_at).toLocaleString()}
        </span>
      </div>

      {needsClarification && (
        <div className="task-row__action-hint">
          ⚠️ {t('taskBoard.clarifyHint')}
        </div>
      )}
      {task.status === 'executing' && (
        <div className="task-row__progress">
          <Loader size={12} className="animate-pulse" />
          <span>{t('taskBoard.executingLabel')}</span>
        </div>
      )}
    </div>
  )
}
