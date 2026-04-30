import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AlertTriangle,
  CalendarDays,
  CheckCircle2,
  Clock,
  ExternalLink,
  ListFilter,
  Loader,
  RefreshCw,
  Search,
} from 'lucide-react'
import { api } from '../api/client'
import type { ScheduledRunResult, ScheduleKind } from '../types'
import { getTaskStatusLabel } from '../utils/taskStatus'
import { useI18n } from '../i18n/useI18n'
import './ScheduleResults.css'

function compactText(value: string | null | undefined, limit: number) {
  const text = (value || '').replace(/\s+/g, ' ').trim()
  if (text.length <= limit) return text
  return `${text.slice(0, limit).trim()}…`
}

function formatSchedule(item: ScheduledRunResult, t: (k: string, v?: Record<string, string | number>) => string) {
  if (item.schedule_kind === 'every') {
    return t('scheduleResults.scheduleEvery', { seconds: item.every_seconds || 0 })
  }
  if (item.schedule_kind === 'cron') {
    return `${item.cron_expr || '-'}${item.timezone ? ` (${item.timezone})` : ''}`
  }
  return scheduleKindLabel(item.schedule_kind, t)
}

function scheduleKindLabel(kind: ScheduleKind, t: (k: string) => string) {
  if (kind === 'every') return t('scheduleResults.kindEvery')
  if (kind === 'at') return t('scheduleResults.kindAt')
  return t('scheduleResults.kindCron')
}

function runStatusLabel(
  status: ScheduledRunResult['run_status'],
  t: (k: string) => string,
) {
  const paths: Record<ScheduledRunResult['run_status'], string> = {
    running: 'scheduleResults.statusRunning',
    ok: 'scheduleResults.statusOk',
    error: 'scheduleResults.statusError',
    skipped: 'scheduleResults.statusSkipped',
  }
  return t(paths[status])
}

function getRunStatusClass(status: ScheduledRunResult['run_status']) {
  if (status === 'ok') return 'schedule-results-status--ok'
  if (status === 'error') return 'schedule-results-status--error'
  if (status === 'skipped') return 'schedule-results-status--skipped'
  return 'schedule-results-status--running'
}

export default function ScheduleResults() {
  const { t } = useI18n()
  const navigate = useNavigate()
  const [results, setResults] = useState<ScheduledRunResult[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<'all' | ScheduledRunResult['run_status']>('all')
  const [jobId, setJobId] = useState('all')

  const formatTimeDisplay = useCallback(
    (value?: string | null) => {
      if (!value) return t('scheduleResults.none')
      const date = new Date(value)
      if (Number.isNaN(date.getTime())) return t('scheduleResults.none')
      return date.toLocaleString()
    },
    [t],
  )

  const formatDay = useCallback(
    (value: string) => {
      const date = new Date(value)
      if (Number.isNaN(date.getTime())) return t('scheduleResults.unknownDate')
      return date.toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
        weekday: 'short',
      })
    },
    [t],
  )

  const fetchResults = async () => {
    setLoading(true)
    setError('')
    try {
      setResults(await api.listScheduleRunResults({ limit: 200 }))
    } catch (e) {
      setError(e instanceof Error ? e.message : t('scheduleResults.loadFailed'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchResults()
    const timer = window.setInterval(fetchResults, 10000)
    return () => window.clearInterval(timer)
  }, [])

  const jobOptions = useMemo(() => {
    const map = new Map<string, string>()
    results.forEach(item => map.set(item.job_id, item.job_name))
    return Array.from(map.entries()).map(([id, name]) => ({ id, name }))
  }, [results])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return results.filter(item => {
      if (status !== 'all' && item.run_status !== status) return false
      if (jobId !== 'all' && item.job_id !== jobId) return false
      if (!q) return true
      return [
        item.job_name,
        item.job_message,
        item.task_question || '',
        item.result_excerpt || '',
        item.run_error || '',
      ].some(text => text.toLowerCase().includes(q))
    })
  }, [jobId, query, results, status])

  const grouped = useMemo(() => {
    return filtered.reduce<Record<string, ScheduledRunResult[]>>((acc, item) => {
      const day = formatDay(item.started_at)
      acc[day] = acc[day] || []
      acc[day].push(item)
      return acc
    }, {})
  }, [filtered, formatDay])

  const stats = useMemo(() => ({
    total: results.length,
    ok: results.filter(item => item.run_status === 'ok').length,
    running: results.filter(item => item.run_status === 'running').length,
    problem: results.filter(item => item.run_status === 'error' || item.run_status === 'skipped').length,
    linked: results.filter(item => item.task_id).length,
  }), [results])

  return (
    <div className="schedule-results-page">
      <div className="schedule-results-page__header">
        <div>
          <h1>{t('scheduleResults.title')}</h1>
          <p>{t('scheduleResults.subtitle')}</p>
        </div>
        <button type="button" className="btn btn-secondary" onClick={fetchResults} disabled={loading}>
          {loading ? <Loader size={14} className="animate-pulse" /> : <RefreshCw size={14} />}
          {t('scheduleResults.refresh')}
        </button>
      </div>

      <div className="schedule-results-stats">
        <div className="schedule-results-stat">
          <span>{t('scheduleResults.runsColumn')}</span>
          <strong>{stats.total}</strong>
        </div>
        <div className="schedule-results-stat">
          <span>{t('scheduleResults.ok')}</span>
          <strong>{stats.ok}</strong>
        </div>
        <div className="schedule-results-stat">
          <span>{t('scheduleResults.running')}</span>
          <strong>{stats.running}</strong>
        </div>
        <div className="schedule-results-stat">
          <span>{t('scheduleResults.errorOrSkipped')}</span>
          <strong>{stats.problem}</strong>
        </div>
        <div className="schedule-results-stat">
          <span>{t('scheduleResults.linkedTask')}</span>
          <strong>{stats.linked}</strong>
        </div>
      </div>

      <div className="schedule-results-toolbar">
        <label className="schedule-results-search">
          <Search size={16} />
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder={t('scheduleResults.searchPlaceholder')}
          />
        </label>
        <label className="schedule-results-filter">
          <ListFilter size={16} />
          <select value={status} onChange={e => setStatus(e.target.value as 'all' | ScheduledRunResult['run_status'])}>
            <option value="all">{t('scheduleResults.filterAll')}</option>
            <option value="ok">{t('scheduleResults.filterOk')}</option>
            <option value="running">{t('scheduleResults.filterRunning')}</option>
            <option value="error">{t('scheduleResults.filterError')}</option>
            <option value="skipped">{t('scheduleResults.filterSkipped')}</option>
          </select>
        </label>
        <label className="schedule-results-filter">
          <CalendarDays size={16} />
          <select value={jobId} onChange={e => setJobId(e.target.value)}>
            <option value="all">{t('scheduleResults.filterAllJobs')}</option>
            {jobOptions.map(job => (
              <option key={job.id} value={job.id}>{job.name}</option>
            ))}
          </select>
        </label>
      </div>

      {error && (
        <div className="schedule-results-error">
          <AlertTriangle size={16} />
          <span>{error}</span>
        </div>
      )}

      {filtered.length === 0 ? (
        <div className="schedule-results-empty">
          <CalendarDays size={42} />
          <p>{results.length === 0 ? t('scheduleResults.emptyNone') : t('scheduleResults.emptyFiltered')}</p>
          <span>{results.length === 0 ? t('scheduleResults.emptyNoneHint') : t('scheduleResults.emptyFilteredHint')}</span>
        </div>
      ) : (
        <div className="schedule-results-days">
          {Object.entries(grouped).map(([day, items]) => (
            <section key={day} className="schedule-result-day">
              <div className="schedule-result-day__title">
                <CalendarDays size={16} />
                <span>{day}</span>
                <em>{t('scheduleResults.runsCount', { n: items.length })}</em>
              </div>

              <div className="schedule-result-list">
                {items.map(item => {
                  const canOpenTask = Boolean(item.task_id)
                  const briefMessage = compactText(item.job_message, 56)
                  const briefResult = compactText(item.run_error || item.result_excerpt, 96)
                  return (
                    <article key={item.run_id} className="schedule-result-card">
                      <div className="schedule-result-card__top">
                        <div>
                          <h2>{item.job_name}</h2>
                          <p title={item.job_message}>{briefMessage}</p>
                        </div>
                        <span className={`schedule-results-status ${getRunStatusClass(item.run_status)}`}>
                          {item.run_status === 'running' ? (
                            <Loader size={13} className="animate-pulse" />
                          ) : item.run_status === 'ok' ? (
                            <CheckCircle2 size={13} />
                          ) : (
                            <AlertTriangle size={13} />
                          )}
                          {runStatusLabel(item.run_status, t)}
                        </span>
                      </div>

                      <div className="schedule-result-card__meta">
                        <span><Clock size={12} /> {formatTimeDisplay(item.started_at)}</span>
                        <span>{scheduleKindLabel(item.schedule_kind, t)}：{formatSchedule(item, t)}</span>
                        <span>{t('scheduleResults.taskStatusLabel')}{getTaskStatusLabel(item.task_status || undefined)}</span>
                        {item.finished_at && (
                          <span>{t('scheduleResults.finishedAt')}{formatTimeDisplay(item.finished_at)}</span>
                        )}
                      </div>

                      {item.run_error ? (
                        <div className="schedule-result-card__message schedule-result-card__message--error">
                          {briefResult}
                        </div>
                      ) : (
                        <div className="schedule-result-card__message">
                          {briefResult || t('scheduleResults.briefFallback')}
                        </div>
                      )}

                      <div className="schedule-result-card__actions">
                        <button
                          type="button"
                          className="btn btn-secondary"
                          onClick={() => canOpenTask && navigate(`/tasks/${item.task_id}`)}
                          disabled={!canOpenTask}
                        >
                          <ExternalLink size={14} />
                          {canOpenTask ? t('scheduleResults.openTask') : t('scheduleResults.noTaskYet')}
                        </button>
                      </div>
                    </article>
                  )
                })}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  )
}
