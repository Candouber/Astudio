import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useStudioStore } from '../stores/studioStore'
import { Clock, Hash, Zap, Building2, Trash2 } from 'lucide-react'
import type { Studio } from '../types'
import { useI18n } from '../i18n/useI18n'
import './Studios.css'

export default function Studios() {
  const { studios, fetchStudios, deleteStudio } = useStudioStore()
  const navigate = useNavigate()
  const { t } = useI18n()
  const [now] = useState(() => Date.now())
  const [confirmingId, setConfirmingId] = useState('')
  const [deletingId, setDeletingId] = useState('')

  useEffect(() => {
    fetchStudios()
  }, [fetchStudios])

  const businessTeamCount = studios.filter(s => (s.kind ?? 'team') === 'team' && !s.is_hidden).length

  const handleDelete = async (studio: Studio) => {
    if (businessTeamCount <= 1 || studio.is_default || deletingId) return
    if (confirmingId !== studio.id) {
      setConfirmingId(studio.id)
      return
    }
    setDeletingId(studio.id)
    try {
      await deleteStudio(studio.id)
      setConfirmingId('')
    } catch {
      /* store keeps the error */
    } finally {
      setDeletingId('')
    }
  }

  return (
    <div className="studios-page">
      <div className="studios-page__header">
        <h1>{t('studios.title')}</h1>
        <p>{t('studios.subtitle')}</p>
      </div>

      {studios.length === 0 ? (
        <div className="studios-page__empty">
          <Building2 size={40} />
          <p>{t('studios.emptyTitle')}</p>
          <span>{t('studios.emptyHint')}</span>
        </div>
      ) : (
        <div className="studios-page__grid">
          {studios.map(s => (
            <StudioCard
              key={s.id}
              studio={s}
              now={now}
              canDelete={businessTeamCount > 1 && !s.is_default}
              confirming={confirmingId === s.id}
              deleting={deletingId === s.id}
              onClick={() => navigate(`/studios/${s.id}`)}
              onDelete={() => handleDelete(s)}
              onCancelDelete={() => setConfirmingId('')}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function StudioCard({
  studio,
  now,
  canDelete,
  confirming,
  deleting,
  onClick,
  onDelete,
  onCancelDelete,
}: {
  studio: Studio
  now: number
  canDelete: boolean
  confirming: boolean
  deleting: boolean
  onClick: () => void
  onDelete: () => void
  onCancelDelete: () => void
}) {
  const { t } = useI18n()

  const timeAgo = (d?: string) => {
    if (!d) return t('studios.noActivity')
    const mins = Math.floor((now - new Date(d).getTime()) / 60000)
    if (mins < 60) return t('studios.minutesAgo', { n: mins })
    const hrs = Math.floor(mins / 60)
    if (hrs < 24) return t('studios.hoursAgo', { n: hrs })
    return t('studios.daysAgo', { n: Math.floor(hrs / 24) })
  }

  return (
    <div className="s-card card" onClick={onClick}>
      <div className="s-card__top">
        <div className="s-card__icon"><Zap size={18} /></div>
        <div className="s-card__copy">
          <h3 className="s-card__title">{studio.scenario}</h3>
          <p className="s-card__desc">{studio.card.description || t('studios.noDescription')}</p>
        </div>
        <button
          type="button"
          className="btn btn-icon s-card__delete"
          onClick={(e) => {
            e.stopPropagation()
            onDelete()
          }}
          disabled={!canDelete || deleting}
          title={
            studio.is_default
              ? t('studios.defaultCannotDelete')
              : canDelete
                ? t('studios.deleteTitle')
                : t('studios.keepOneTeam')
          }
        >
          <Trash2 size={14} />
        </button>
      </div>

      {confirming && (
        <div className="s-card__confirm" onClick={e => e.stopPropagation()}>
          <span>{t('studios.confirmDelete', { name: studio.scenario })}</span>
          <div className="s-card__confirm-actions">
            <button type="button" className="btn btn-danger btn-sm" onClick={onDelete} disabled={deleting}>
              <Trash2 size={12} />
              {deleting ? t('studios.deleting') : t('studios.deleteTitle')}
            </button>
            <button type="button" className="btn btn-secondary btn-sm" onClick={onCancelDelete} disabled={deleting}>
              {t('common.cancel')}
            </button>
          </div>
        </div>
      )}

      {studio.card.core_capabilities.length > 0 && (
        <div className="s-card__tags">
          {studio.card.core_capabilities.slice(0, 4).map((cap, i) => (
            <span key={i} className="s-card__tag">{cap}</span>
          ))}
        </div>
      )}

      <div className="s-card__agents">
        {studio.sub_agents.map(sa => (
          <span key={sa.id} className="s-card__agent" title={sa.role}>
            {sa.role.charAt(0).toUpperCase()}
          </span>
        ))}
        <span className="s-card__agent-count">{t('studios.agents', { n: studio.sub_agents.length })}</span>
      </div>

      <div className="s-card__footer">
        <span className="s-card__stat"><Hash size={12} />{t('studios.tasksStat', { n: studio.card.task_count })}</span>
        <span className="s-card__stat"><Clock size={12} />{timeAgo(studio.card.last_active || undefined)}</span>
      </div>
    </div>
  )
}
