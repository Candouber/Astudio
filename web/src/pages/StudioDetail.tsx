import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { startChatStream } from '../api/sse'
import { useChatStore } from '../stores/chatStore'
import type { Studio, SubAgentConfig, Task } from '../types'
import { useI18n } from '../i18n/useI18n'
import {
  ArrowLeft, PlayCircle, Brain, Clock, FileText,
  User, ChevronDown, ChevronUp, Plus, Pencil, Trash2, X, Check, Zap,
  Lightbulb, History, TrendingUp, Settings2,
} from 'lucide-react'
import './StudioDetail.css'

/** 格式化 token 数：不足 1k 直接显示，超过 1k 显示 x.xk */
function fmtTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

const TASK_STATUS_PATHS: Record<string, string> = {
  planning: 'taskStatus.labels.planning',
  await_leader_plan_approval: 'taskStatus.labels.await_leader_plan_approval',
  executing: 'taskStatus.labels.executing',
  completed: 'taskStatus.labels.completed',
  completed_with_blockers: 'taskStatus.labels.completed_with_blockers',
  timeout_killed: 'taskStatus.labels.timeout_killed',
  failed: 'taskStatus.labels.failed',
}

function taskStatusLabel(status: string, t: (path: string) => string) {
  const p = TASK_STATUS_PATHS[status]
  return p ? t(p) : status
}

const ALL_SKILLS = ['web_search', 'execute_code', 'read_file', 'write_file', 'list_files']

// ── 内联技能选择器 ─────────────────────────────────────────────────────────
function SkillPicker({ selected, onChange }: { selected: string[]; onChange: (v: string[]) => void }) {
  const toggle = (sk: string) =>
    onChange(selected.includes(sk) ? selected.filter(s => s !== sk) : [...selected, sk])
  return (
    <div className="skill-picker">
      {ALL_SKILLS.map(sk => (
        <button
          key={sk}
          type="button"
          className={`skill-tag ${selected.includes(sk) ? 'skill-tag--on' : ''}`}
          onClick={() => toggle(sk)}
        >
          {sk}
        </button>
      ))}
    </div>
  )
}

// ── 新增成员表单（内嵌在员工卡下方）────────────────────────────────────────
function AddMemberForm({ studioId, onDone }: { studioId: string; onDone: (s: Studio) => void }) {
  const { t } = useI18n()
  const [role, setRole] = useState('')
  const [skills, setSkills] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  const submit = async () => {
    if (!role.trim()) { setErr(t('studioDetail.fillRole')); return }
    setSaving(true); setErr('')
    try {
      const studio = await api.addMember(studioId, { role: role.trim(), skills })
      onDone(studio)
    } catch {
      setErr(t('studioDetail.saveMemberFailed'))
    } finally { setSaving(false) }
  }

  return (
    <div className="add-member-form card">
      <h4 className="amf__title"><Plus size={14} /> {t('studioDetail.addAgentTitle')}</h4>
      <label className="amf__label">{t('studioDetail.roleLabel')}</label>
      <input
        className="input-base amf__input"
        placeholder={t('studioDetail.rolePlaceholder')}
        value={role}
        onChange={e => setRole(e.target.value)}
      />
      <label className="amf__label">{t('studioDetail.skillsLabel')}</label>
      <SkillPicker selected={skills} onChange={setSkills} />
      {err && <p className="amf__err">{err}</p>}
      <div className="amf__actions">
        <button type="button" className="btn btn-primary" onClick={submit} disabled={saving}>
          {saving ? t('studioDetail.saving') : t('studioDetail.confirmAdd')}
        </button>
      </div>
    </div>
  )
}

// ── 成员卡（支持内联编辑）──────────────────────────────────────────────────
function AgentCard({
  sa,
  studioId,
  onUpdated,
  onDeleted,
}: {
  sa: SubAgentConfig
  studioId: string
  onUpdated: (updated: Studio) => void
  onDeleted: (id: string) => void
}) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const [expanded, setExpanded] = useState(false)
  const [editing, setEditing] = useState(false)
  const [role, setRole] = useState(sa.role)
  const [skills, setSkills] = useState<string[]>(sa.skills ?? [])
  const [confirmDel, setConfirmDel] = useState(false)
  const [saving, setSaving] = useState(false)

  const saveEdit = async () => {
    setSaving(true)
    try {
      const updated = await api.updateMember(studioId, sa.id, { role, skills })
      onUpdated(updated)
      setEditing(false)
    } catch { /* ignore */ }
    finally { setSaving(false) }
  }

  const cancelEdit = () => {
    setRole(sa.role); setSkills(sa.skills ?? [])
    setEditing(false)
  }

  const doDelete = async () => {
    try {
      await api.deleteMember(studioId, sa.id)
      onDeleted(sa.id)
    } catch { /* ignore */ }
  }

  return (
    <div className="agent-card card">
      <div className="agent-card__header">
        <div className="agent-card__avatar">{sa.role.charAt(0).toUpperCase()}</div>
        <div className="agent-card__info">
          {editing ? (
            <input
              className="input-base agent-card__role-input"
              value={role}
              onChange={e => setRole(e.target.value)}
            />
          ) : (
            <span className="agent-card__role">{sa.role}</span>
          )}
          {sa.is_working && <span className="agent-card__badge animate-pulse">{t('studioDetail.working')}</span>}
        </div>
        {sa.total_tokens > 0 && !editing && (
          <span className="agent-card__tokens" title={`${sa.total_tokens.toLocaleString()} tokens`}>
            <Zap size={11} /> {fmtTokens(sa.total_tokens)}
          </span>
        )}
        <div className="agent-card__actions">
          {editing ? (
            <>
              <button type="button" className="btn btn-icon icon-success" onClick={saveEdit} disabled={saving} title={t('studioDetail.saveBtn')}>
                <Check size={14} />
              </button>
              <button type="button" className="btn btn-icon" onClick={cancelEdit} title={t('common.cancel')}>
                <X size={14} />
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className="btn btn-icon"
                onClick={() => navigate(`/studios/${studioId}/agents/${sa.id}`)}
                title={t('studioDetail.agentDetail')}
              >
                <Settings2 size={14} />
              </button>
              <button type="button" className="btn btn-icon" onClick={() => setEditing(true)} title={t('studioDetail.editQuick')}>
                <Pencil size={14} />
              </button>
              <button type="button" className="btn btn-icon icon-danger" onClick={() => setConfirmDel(true)} title={t('studioDetail.deleteMember')}>
                <Trash2 size={14} />
              </button>
            </>
          )}
        </div>
      </div>

      {/* 技能 */}
      {editing ? (
        <div className="agent-card__edit-skills">
          <label className="amf__label">{t('studioDetail.skillsLabel')}</label>
          <SkillPicker selected={skills} onChange={setSkills} />
        </div>
      ) : (
        sa.skills?.length > 0 && (
          <div className="agent-card__skills">
            {sa.skills.map((sk, i) => (
              <span key={i} className="agent-card__skill">{sk}</span>
            ))}
          </div>
        )
      )}

      {/* 记忆展开 */}
      {!editing && (
        <>
          <div className="agent-card__soul-toggle">
            <button type="button" className="btn btn-icon" onClick={() => setExpanded(!expanded)}>
              <Brain size={14} />
              <span>{t('studioDetail.experienceMemory')}</span>
              {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
          </div>
          {expanded && <pre className="agent-card__soul">{sa.soul || t('studioDetail.noSoul')}</pre>}
        </>
      )}

      {/* 删除确认 */}
      {confirmDel && (
        <div className="agent-card__confirm">
          <p>{t('studioDetail.confirmDeleteMember', { role: sa.role })}</p>
          <div className="agent-card__confirm-btns">
            <button type="button" className="btn btn-danger" onClick={doDelete}><Trash2 size={12} /> {t('studioDetail.deleteMember')}</button>
            <button type="button" className="btn btn-secondary" onClick={() => setConfirmDel(false)}>{t('common.cancel')}</button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── 主页面 ─────────────────────────────────────────────────────────────────
export default function StudioDetail() {
  const { t } = useI18n()
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [studio, setStudio] = useState<Studio | null>(null)
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const [question, setQuestion] = useState('')
  const [showAddForm, setShowAddForm] = useState(false)

  useEffect(() => {
    if (!id) return
    Promise.all([api.getStudio(id), api.getStudioTasks(id)])
      .then(([s, t]) => { setStudio(s); setTasks(t) })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [id])

  const handleStart = () => {
    if (!question.trim() || !studio) return
    const full = `${t('common.studioTargetPrefix', { name: studio.scenario })}${question.trim()}`
    useChatStore.getState().addMessage({
      id: `user-${Date.now()}`,
      role: 'user',
      content: full,
      thinkingText: '',
      timestamp: Date.now(),
    })
    startChatStream(full)
    navigate('/')
  }

  const handleMemberUpdated = (updated: Studio) => setStudio(updated)

  const handleMemberDeleted = (saId: string) => {
    if (!studio) return
    setStudio({ ...studio, sub_agents: studio.sub_agents.filter(a => a.id !== saId) })
  }

  const handleMemberAdded = (updated: Studio) => {
    setStudio(updated)
    setShowAddForm(false)
  }

  if (loading) return <div className="sd__loading">{t('studioDetail.loading')}</div>
  if (!studio) return <div className="sd__loading">{t('studioDetail.notFound')}</div>

  return (
    <div className="sd">
      <div className="sd__topbar">
        <button className="btn btn-icon" onClick={() => navigate('/studios')}>
          <ArrowLeft size={18} />
        </button>
        <div className="sd__topbar-info">
          <h1 className="sd__title">{studio.scenario}</h1>
          <p className="sd__desc">{studio.card.description}</p>
        </div>
        {studio.total_tokens > 0 && (
          <div className="sd__studio-tokens">
            <Zap size={14} />
            <span>{fmtTokens(studio.total_tokens)}</span>
            <small>{t('studioDetail.cumulativeCost')}</small>
          </div>
        )}
      </div>

      <div className="sd__grid">
        {/* ── Left: Employees ── */}
        <div className="sd__section">
          <div className="sd__section-header">
            <h2 className="sd__section-title">
              <User size={18} /> {t('studioDetail.rosterTitle')}
              <span className="sd__count">{t('studioDetail.peopleCount', { n: studio.sub_agents.length })}</span>
            </h2>
            <button type="button" className="btn btn-primary btn-sm" onClick={() => setShowAddForm(v => !v)}>
              <Plus size={14} /> {showAddForm ? t('studioDetail.collapseAdd') : t('studioDetail.addMember')}
            </button>
          </div>

          {showAddForm && (
            <AddMemberForm studioId={studio.id} onDone={handleMemberAdded} />
          )}

          <div className="sd__agents">
            {studio.sub_agents.length === 0 ? (
              <p className="text-muted text-sm text-center" style={{ padding: '24px 0' }}>
                {t('studioDetail.emptyAgents')}
              </p>
            ) : (
              studio.sub_agents.map(sa => (
                <AgentCard
                  key={sa.id}
                  sa={sa}
                  studioId={studio.id}
                  onUpdated={handleMemberUpdated}
                  onDeleted={handleMemberDeleted}
                />
              ))
            )}
          </div>
        </div>

        {/* ── Right: Task + History ── */}
        <div className="sd__section">
          {/* 工作室成长面板 —— 让用户看到"越用越聪明"的迹象 */}
          {(studio.card.task_count > 0 ||
            (studio.card.recent_topics && studio.card.recent_topics.length > 0) ||
            (studio.card.core_capabilities && studio.card.core_capabilities.length > 0)) && (
            <div className="card p-5 mb-5 sd__growth">
              <h3 style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <Brain size={16} /> {t('studioDetail.memoryTitle')}
                <span className="sd__growth-count" title={t('studioDetail.taskCountTooltip')}>
                  <TrendingUp size={11} /> {t('studioDetail.taskCountTitle', { n: studio.card.task_count || 0 })}
                </span>
              </h3>
              <p className="sd__growth-hint">
                {t('studioDetail.memoryHint')}
              </p>

              {studio.card.core_capabilities && studio.card.core_capabilities.length > 0 && (
                <div className="sd__growth-block">
                  <h4 className="sd__growth-subtitle">
                    <Lightbulb size={13} /> {t('studioDetail.capabilities')}
                  </h4>
                  <div className="sd__growth-chips">
                    {studio.card.core_capabilities.slice(0, 20).map((cap, i) => (
                      <span key={i} className="sd__growth-chip sd__growth-chip--cap">{cap}</span>
                    ))}
                  </div>
                </div>
              )}

              {studio.card.recent_topics && studio.card.recent_topics.length > 0 && (
                <div className="sd__growth-block">
                  <h4 className="sd__growth-subtitle">
                    <History size={13} /> {t('studioDetail.topics')}
                  </h4>
                  <ul className="sd__growth-topics">
                    {studio.card.recent_topics.slice(0, 8).map((topic, i) => (
                      <li key={i} className="sd__growth-topic">{topic}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {studio.card.user_facts && studio.card.user_facts.length > 0 && (
            <div className="card p-5 mb-5">
              <h3 style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <FileText size={16} /> {t('studioDetail.userMemory')}
                <span className="sd__count">{studio.card.user_facts.length}</span>
              </h3>
              <ul className="sd__facts-list">
                {studio.card.user_facts.map((fact, i) => (
                  <li key={i} className="sd__fact-item">{fact}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="card p-5 mb-5">
            <h3>
              <PlayCircle size={16} /> {t('studioDetail.startTask')}
            </h3>
            <textarea
              className="input-base mt-3"
              rows={3}
              placeholder={t('studioDetail.taskPlaceholder')}
              value={question}
              onChange={e => setQuestion(e.target.value)}
            />
            <button
              type="button"
              className="btn btn-primary mt-3"
              onClick={handleStart}
              disabled={!question.trim()}
              style={{ width: '100%' }}
            >
              {t('studioDetail.sendToChat')}
            </button>
          </div>

          <div className="card p-5">
            <div className="flex-between mb-4">
              <h3><FileText size={16} /> {t('studioDetail.historyTitle')}</h3>
              <span className="text-muted text-sm">{t('studioDetail.historyCount', { n: tasks.length })}</span>
            </div>

            {tasks.length === 0 ? (
              <p className="text-muted text-sm text-center">{t('studioDetail.noHistory')}</p>
            ) : (
              <div className="sd__task-list">
                {tasks.map(task => (
                  <div
                    key={task.id}
                    className="sd__task-item"
                    onClick={() => navigate(`/tasks/${task.id}`)}
                  >
                    <span className="sd__task-q">{task.question}</span>
                    <div className="sd__task-meta">
                      <span className={`status-badge status--${task.status}`}>
                        {taskStatusLabel(task.status, t)}
                      </span>
                      <span className="text-muted text-xs">
                        <Clock size={11} /> {new Date(task.created_at).toLocaleString()}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
