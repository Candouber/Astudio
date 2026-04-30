import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Box, ExternalLink, Loader, Trash2 } from 'lucide-react'
import { api } from '../api/client'
import type { Sandbox, Task } from '../types'
import { useI18n } from '../i18n/useI18n'
import './Sandboxes.css'

function compactText(value: string | null | undefined, limit: number) {
  const text = (value || '').replace(/\s+/g, ' ').trim()
  if (text.length <= limit) return text
  return `${text.slice(0, limit).trim()}…`
}

export default function Sandboxes() {
  const { t } = useI18n()
  const navigate = useNavigate()
  const [sandboxes, setSandboxes] = useState<Sandbox[]>([])
  const [tasks, setTasks] = useState<Record<string, Task>>({})
  const [loading, setLoading] = useState(true)

  const statusLabel = (status: string) => {
    const map: Record<string, string> = {
      ready: 'sandboxes.statusReady',
      running: 'sandboxes.statusRunning',
      stopped: 'sandboxes.statusStopped',
      error: 'sandboxes.statusError',
    }
    const path = map[status]
    return path ? t(path) : status
  }

  const load = async () => {
    setLoading(true)
    try {
      const list = await api.listSandboxes()
      setSandboxes(list)
      const pairs = await Promise.all(
        list.map(sb => api.getTask(sb.task_id).then(task => [sb.task_id, task] as const).catch(() => null)),
      )
      setTasks(Object.fromEntries(pairs.filter(Boolean) as [string, Task][]))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    const timer = window.setInterval(load, 5000)
    return () => window.clearInterval(timer)
  }, [])

  const remove = async (id: string) => {
    if (!window.confirm(t('sandboxes.deleteConfirm'))) return
    await api.deleteSandbox(id, true)
    await load()
  }

  return (
    <div className="sandboxes-page">
      <div className="sandboxes-page__header">
        <div>
          <h1>{t('sandboxes.title')}</h1>
          <p>{t('sandboxes.subtitle')}</p>
        </div>
      </div>

      {loading ? (
        <div className="sandboxes-page__empty">
          <Loader size={20} className="animate-pulse" />
          <span>{t('sandboxes.loading')}</span>
        </div>
      ) : sandboxes.length === 0 ? (
        <div className="sandboxes-page__empty">
          <Box size={28} />
          <h3>{t('sandboxes.emptyTitle')}</h3>
          <p>{t('sandboxes.emptyHint')}</p>
        </div>
      ) : (
        <div className="sandboxes-page__list">
          {sandboxes.map(sb => {
            const task = tasks[sb.task_id]
            const suffix = t('sandboxes.taskSuffix', { id: sb.task_id })
            const title = compactText(sb.title || suffix, 42)
            const summary = compactText(task?.question || sb.description || sb.path, 96)
            return (
              <div key={sb.id} className="sandbox-row">
                <button type="button" className="sandbox-row__main" onClick={() => navigate(`/sandboxes/${sb.id}`)}>
                  <span className={`sandbox-row__status sandbox-row__status--${sb.status}`} />
                  <span>
                    <strong title={sb.title || suffix}>{title}</strong>
                    <small title={task?.question || sb.description || sb.path}>{summary}</small>
                  </span>
                </button>
                <span className={`sandbox-badge sandbox-badge--${sb.status}`}>
                  {statusLabel(sb.status)}
                </span>
                <button type="button" className="btn btn-secondary" onClick={() => navigate(`/tasks/${sb.task_id}`)}>
                  <ExternalLink size={14} /> {t('sandboxes.openTask')}
                </button>
                <button type="button" className="btn btn-icon" onClick={() => remove(sb.id)} title={t('sandboxes.deleteTitle')}>
                  <Trash2 size={15} />
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
