import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CalendarClock, CalendarDays, Clock, PauseCircle, Play, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { api } from '../api/client'
import type { ScheduleKind, ScheduledJob } from '../types'
import { useI18n } from '../i18n/useI18n'
import './Schedules.css'

function formatTime(value: string | undefined | null, t: (k: string) => string) {
  if (!value) return t('schedules.scheduleUnknown')
  return new Date(value).toLocaleString()
}

function formatSchedule(job: ScheduledJob, t: (k: string, v?: Record<string, string | number>) => string) {
  if (job.schedule_kind === 'every') {
    return t('schedules.scheduleEvery', { seconds: job.every_seconds || 0 })
  }
  if (job.schedule_kind === 'cron') {
    return `${job.cron_expr || '-'}${job.timezone ? ` (${job.timezone})` : ''}`
  }
  return formatTime(job.at_time, t)
}

function scheduleKindLabel(kind: ScheduleKind, t: (k: string) => string) {
  if (kind === 'every') return t('schedules.kindEvery')
  if (kind === 'at') return t('schedules.kindAt')
  return t('schedules.kindCron')
}

export default function Schedules() {
  const { t } = useI18n()
  const navigate = useNavigate()
  const [jobs, setJobs] = useState<ScheduledJob[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [name, setName] = useState('')
  const [message, setMessage] = useState('')
  const [kind, setKind] = useState<ScheduleKind>('every')
  const [everySeconds, setEverySeconds] = useState('3600')
  const [cronExpr, setCronExpr] = useState('0 9 * * *')
  const [timezone, setTimezone] = useState('Asia/Shanghai')
  const [atTime, setAtTime] = useState('')

  const activeCount = useMemo(() => jobs.filter(j => j.enabled).length, [jobs])

  const fetchJobs = async () => {
    setLoading(true)
    try {
      setJobs(await api.listSchedules())
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchJobs()
    const timer = window.setInterval(fetchJobs, 5000)
    return () => window.clearInterval(timer)
  }, [])

  const createJob = async () => {
    if (!message.trim()) return
    setSaving(true)
    try {
      await api.createSchedule({
        name: name.trim() || message.trim().slice(0, 30),
        message: message.trim(),
        schedule_kind: kind,
        every_seconds: kind === 'every' ? Number(everySeconds) : null,
        cron_expr: kind === 'cron' ? cronExpr.trim() : null,
        timezone: kind === 'cron' ? timezone.trim() : null,
        at_time: kind === 'at' ? atTime : null,
        approval_policy: 'auto_execute',
        overlap_policy: 'skip',
        delete_after_run: kind === 'at',
        created_by: 'user',
      })
      setName('')
      setMessage('')
      await fetchJobs()
    } finally {
      setSaving(false)
    }
  }

  const toggleJob = async (job: ScheduledJob) => {
    await api.updateSchedule(job.id, { enabled: !job.enabled })
    await fetchJobs()
  }

  const deleteJob = async (job: ScheduledJob) => {
    if (!window.confirm(t('schedules.confirmDelete', { name: job.name }))) return
    await api.deleteSchedule(job.id)
    await fetchJobs()
  }

  const runNow = async (job: ScheduledJob) => {
    await api.runScheduleNow(job.id)
    await fetchJobs()
  }

  return (
    <div className="schedules-page">
      <div className="schedules-page__header">
        <div>
          <h1>{t('schedules.title')}</h1>
          <p>{t('schedules.subtitle', { count: activeCount })}</p>
        </div>
        <div className="schedules-page__header-actions">
          <button type="button" className="btn btn-secondary" onClick={() => navigate('/schedule-results')}>
            <CalendarDays size={14} /> {t('schedules.resultsLink')}
          </button>
          <button type="button" className="btn btn-secondary" onClick={fetchJobs} disabled={loading}>
            <RefreshCw size={14} /> {t('schedules.refresh')}
          </button>
        </div>
      </div>

      <div className="schedule-create card">
        <h2><Plus size={16} /> {t('schedules.newTitle')}</h2>
        <input className="input-base" placeholder={t('schedules.namePlaceholder')} value={name} onChange={e => setName(e.target.value)} />
        <textarea
          className="input-base"
          rows={3}
          placeholder={t('schedules.promptPlaceholder')}
          value={message}
          onChange={e => setMessage(e.target.value)}
        />
        <div className="schedule-create__row">
          <select className="input-base" value={kind} onChange={e => setKind(e.target.value as ScheduleKind)}>
            <option value="every">{t('schedules.kindEvery')}</option>
            <option value="cron">{t('schedules.kindCron')}</option>
            <option value="at">{t('schedules.kindAt')}</option>
          </select>
          {kind === 'every' && (
            <input className="input-base" type="number" min="1" value={everySeconds} onChange={e => setEverySeconds(e.target.value)} />
          )}
          {kind === 'cron' && (
            <>
              <input className="input-base" value={cronExpr} onChange={e => setCronExpr(e.target.value)} />
              <input className="input-base" value={timezone} onChange={e => setTimezone(e.target.value)} />
            </>
          )}
          {kind === 'at' && (
            <input className="input-base" type="datetime-local" value={atTime} onChange={e => setAtTime(e.target.value)} />
          )}
          <button type="button" className="btn btn-primary" onClick={createJob} disabled={saving || !message.trim()}>
            {t('schedules.create')}
          </button>
        </div>
      </div>

      {jobs.length === 0 ? (
        <div className="schedules-page__empty">
          <CalendarClock size={40} />
          <p>{t('schedules.empty')}</p>
        </div>
      ) : (
        <div className="schedule-list">
          {jobs.map(job => (
            <div key={job.id} className="schedule-card card">
              <div className="schedule-card__top">
                <div>
                  <h3>{job.name}</h3>
                  <p>{job.message}</p>
                </div>
                <span className={`schedule-card__status ${job.enabled ? 'schedule-card__status--on' : ''}`}>
                  {job.enabled ? t('schedules.toggleOn') : t('schedules.toggleOff')}
                </span>
              </div>
              <div className="schedule-card__meta">
                <span><Clock size={12} /> {scheduleKindLabel(job.schedule_kind, t)}：{formatSchedule(job, t)}</span>
                <span>{t('schedules.nextRun')}{formatTime(job.next_run_at, t)}</span>
                <span>{t('schedules.lastRun')}{job.last_status || t('schedules.scheduleUnknown')}</span>
              </div>
              {job.last_error && <p className="schedule-card__error">{job.last_error}</p>}
              <div className="schedule-card__actions">
                <button type="button" className="btn btn-secondary" onClick={() => toggleJob(job)}>
                  {job.enabled ? <PauseCircle size={14} /> : <Play size={14} />}
                  {job.enabled ? t('schedules.toggleOff') : t('schedules.toggleOn')}
                </button>
                <button type="button" className="btn btn-secondary" onClick={() => runNow(job)}>
                  <Play size={14} /> {t('schedules.runNow')}
                </button>
                {job.last_status && job.last_run_at && (
                  <button type="button" className="btn btn-secondary" onClick={() => navigate('/tasks')}>
                    {t('schedules.openBoard')}
                  </button>
                )}
                <button type="button" className="btn btn-icon icon-danger" onClick={() => deleteJob(job)} title={t('schedules.deleteTitle')}>
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
