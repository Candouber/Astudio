import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { SkillPoolItem, Studio, SubAgentConfig } from '../types'
import SkillPicker from '../components/common/SkillPicker'
import { useI18n } from '../i18n/useI18n'
import {
  ArrowLeft, Brain, Code2, Loader, Save, ShieldCheck,
  Sparkles, User, Wrench, X,
} from 'lucide-react'
import './AgentDetail.css'

function fmtTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

export default function AgentDetail() {
  const { t } = useI18n()
  const { id: studioId, memberId } = useParams<{ id: string; memberId: string }>()
  const navigate = useNavigate()
  const [studio, setStudio] = useState<Studio | null>(null)
  const [agent, setAgent] = useState<SubAgentConfig | null>(null)
  const [skillPool, setSkillPool] = useState<SkillPoolItem[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState('')

  const [role, setRole] = useState('')
  const [skills, setSkills] = useState<string[]>([])
  const [agentMd, setAgentMd] = useState('')
  const [soul, setSoul] = useState('')

  useEffect(() => {
    if (!studioId || !memberId) return
    setLoading(true)
    Promise.all([api.getStudio(studioId), api.listSkills()])
      .then(([data, skills]) => {
        const found = data.sub_agents.find(sa => sa.id === memberId) || null
        setStudio(data)
        setAgent(found)
        setSkillPool(skills)
        if (found) {
          setRole(found.role)
          setSkills(found.skills ?? [])
          setAgentMd(found.agent_md || '')
          setSoul(found.soul || '')
        }
      })
      .catch(() => setError(t('agentDetail.loadFailed')))
      .finally(() => setLoading(false))
  }, [studioId, memberId])

  const original = useMemo(() => {
    if (!agent) return null
    return {
      role: agent.role,
      skills: agent.skills ?? [],
      agentMd: agent.agent_md || '',
      soul: agent.soul || '',
    }
  }, [agent])

  useEffect(() => {
    if (!original) return
    setDirty(
      role !== original.role ||
      agentMd !== original.agentMd ||
      soul !== original.soul ||
      skills.join('\n') !== original.skills.join('\n'),
    )
  }, [role, skills, agentMd, soul, original])

  const save = async () => {
    if (!studioId || !memberId || !role.trim()) return
    setSaving(true)
    setError('')
    try {
      const updated = await api.updateMember(studioId, memberId, {
        role: role.trim(),
        skills,
        agent_md: agentMd,
        soul,
      })
      const found = updated.sub_agents.find(sa => sa.id === memberId) || null
      setStudio(updated)
      setAgent(found)
      setDirty(false)
    } catch {
      setError(t('agentDetail.saveFailed'))
    } finally {
      setSaving(false)
    }
  }

  const reset = () => {
    if (!original) return
    setRole(original.role)
    setSkills(original.skills)
    setAgentMd(original.agentMd)
    setSoul(original.soul)
    setError('')
  }

  if (loading) {
    return (
      <div className="agent-detail__loading">
        <Loader size={22} className="animate-spin" />
        {t('agentDetail.loading')}
      </div>
    )
  }

  if (!studio || !agent) {
    return (
      <div className="agent-detail__loading">
        <p>{error || t('agentDetail.notFound')}</p>
        <button type="button" className="btn btn-secondary" onClick={() => navigate(studioId ? `/studios/${studioId}` : '/studios')}>
          {t('agentDetail.backToStudio')}
        </button>
      </div>
    )
  }

  return (
    <div className="agent-detail">
      <div className="agent-detail__topbar">
        <button type="button" className="btn btn-icon" onClick={() => navigate(`/studios/${studio.id}`)}>
          <ArrowLeft size={18} />
        </button>
        <div className="agent-detail__identity">
          <div className="agent-detail__avatar">{role.charAt(0).toUpperCase() || 'A'}</div>
          <div>
            <div className="agent-detail__crumb">{studio.scenario} / {t('agentDetail.crumbSuffix')}</div>
            <h1>{role || agent.role}</h1>
          </div>
        </div>
        <div className="agent-detail__actions">
          {dirty && <span className="agent-detail__dirty">{t('agentDetail.dirty')}</span>}
          <button type="button" className="btn btn-secondary" onClick={reset} disabled={!dirty || saving}>
            <X size={14} />
            {t('agentDetail.reset')}
          </button>
          <button type="button" className="btn btn-primary" onClick={save} disabled={!dirty || saving || !role.trim()}>
            {saving ? <Loader size={14} className="animate-spin" /> : <Save size={14} />}
            {saving ? t('agentDetail.saving') : t('agentDetail.save')}
          </button>
        </div>
      </div>

      {error && <div className="agent-detail__error">{error}</div>}

      <div className="agent-detail__grid">
        <section className="agent-detail__panel">
          <div className="agent-detail__section-title">
            <User size={16} />
            {t('agentDetail.basics')}
          </div>
          <label className="agent-detail__label">{t('agentDetail.roleLabel')}</label>
          <input
            className="input-base agent-detail__input"
            value={role}
            onChange={e => setRole(e.target.value)}
            placeholder={t('agentDetail.rolePlaceholder')}
          />

          <div className="agent-detail__stats">
            <div>
              <span>{t('agentDetail.workStatus')}</span>
              <strong>{agent.is_working ? t('agentDetail.working') : t('agentDetail.idle')}</strong>
            </div>
            <div>
              <span>{t('agentDetail.cumulativeCost')}</span>
              <strong>{t('agentDetail.tokensFmt', { value: fmtTokens(agent.total_tokens) })}</strong>
            </div>
          </div>
        </section>

        <section className="agent-detail__panel">
          <div className="agent-detail__section-title">
            <Wrench size={16} />
            Skills
          </div>
          <p className="agent-detail__hint">{t('agentDetail.skillsHint')}</p>
          <SkillPicker skills={skillPool} selected={skills} onChange={setSkills} />
        </section>

        <section className="agent-detail__panel agent-detail__panel--wide">
          <div className="agent-detail__section-title">
            <Code2 size={16} />
            Agent Prompt
          </div>
          <p className="agent-detail__hint">{t('agentDetail.agentMdHint')}</p>
          <textarea
            className="agent-detail__editor agent-detail__editor--agent"
            value={agentMd}
            onChange={e => setAgentMd(e.target.value)}
            spellCheck={false}
          />
        </section>

        <section className="agent-detail__panel agent-detail__panel--wide">
          <div className="agent-detail__section-title">
            <Brain size={16} />
            {t('agentDetail.soulTitle')}
          </div>
          <p className="agent-detail__hint">{t('agentDetail.soulHint')}</p>
          <textarea
            className="agent-detail__editor agent-detail__editor--soul"
            value={soul}
            onChange={e => setSoul(e.target.value)}
            spellCheck={false}
          />
        </section>

        <section className="agent-detail__panel">
          <div className="agent-detail__section-title">
            <Sparkles size={16} />
            {t('agentDetail.summary')}
          </div>
          <div className="agent-detail__memory-preview">
            {soul.trim() || t('agentDetail.noSoul')}
          </div>
        </section>

        <section className="agent-detail__panel">
          <div className="agent-detail__section-title">
            <ShieldCheck size={16} />
            {t('agentDetail.tipsTitle')}
          </div>
          <ul className="agent-detail__tips">
            <li>{t('agentDetail.tip1')}</li>
            <li>{t('agentDetail.tip2')}</li>
            <li>{t('agentDetail.tip3')}</li>
          </ul>
        </section>
      </div>
    </div>
  )
}
