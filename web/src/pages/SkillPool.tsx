import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  AlertTriangle, Check, Code2, Download, Eye, FileCode2, Loader, Package, RefreshCw, RotateCcw, Search, Sparkles, Trash2, Wrench, X,
} from 'lucide-react'
import { api } from '../api/client'
import type { BundleSkillConfig, SkillMdPayload, SkillPoolItem, SkillProbeResult } from '../types'
import { useI18n } from '../i18n/useI18n'
import './SkillPool.css'

interface ImportDraft {
  url: string
  overrideSlug: string
  category: string
}

interface AiDraft {
  slug: string
  name: string
  goal: string
  category: string
}

const EMPTY_IMPORT: ImportDraft = { url: '', overrideSlug: '', category: '' }
const EMPTY_AI: AiDraft = { slug: '', name: '', goal: '', category: '' }

function extractApiError(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: string | unknown } } })?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (err instanceof Error && err.message) return err.message
  return fallback
}

function getBundleConfig(item: SkillPoolItem): BundleSkillConfig | null {
  if (item.kind !== 'bundle') return null
  return item.config as unknown as BundleSkillConfig
}

function kindLabel(
  item: SkillPoolItem,
  t: (path: string) => string,
): { text: string; tone: 'builtin' | 'bundle'; sub?: string } {
  if (item.kind === 'builtin') return { text: t('skillPool.builtin'), tone: 'builtin' }
  const bundle = getBundleConfig(item)
  const provider = bundle?.source.provider ?? 'local'
  const label = provider === 'clawhub' ? 'ClawHub'
    : provider === 'skillhub_cn' ? 'SkillHub.cn'
    : provider === 'github' ? 'GitHub'
    : t('skillPool.aiGenerated')
  return { text: t('skillPool.bundleKind'), tone: 'bundle', sub: label }
}

export default function SkillPool() {
  const { t } = useI18n()
  const [skills, setSkills] = useState<SkillPoolItem[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [importDraft, setImportDraft] = useState<ImportDraft>(EMPTY_IMPORT)
  const [aiDraft, setAiDraft] = useState<AiDraft>(EMPTY_AI)
  const [showAi, setShowAi] = useState(false)
  const [aiResultMsg, setAiResultMsg] = useState('')
  const [editingName, setEditingName] = useState<Record<string, string>>({})

  // URL probe 实时预览（贴上去后 500ms 识别一次）
  const [probe, setProbe] = useState<SkillProbeResult | null>(null)
  const [probeErr, setProbeErr] = useState('')
  const [probing, setProbing] = useState(false)
  const probeTimer = useRef<number | null>(null)

  // SKILL.md 预览抽屉
  const [mdPayload, setMdPayload] = useState<SkillMdPayload | null>(null)
  const [mdLoading, setMdLoading] = useState(false)
  const [mdErr, setMdErr] = useState('')

  // ?highlight=slug 支持：滚动 + 边框动画
  const [params] = useSearchParams()
  const highlightSlug = params.get('highlight')
  const cardRefs = useRef<Record<string, HTMLElement | null>>({})

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      setSkills(await api.listSkills(true))
    } catch (e) {
      setError(extractApiError(e, t('skillPool.opFailed')))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  // URL 变化时 debounce 调 /skills/probe，用于在输入框下方展示识别结果
  useEffect(() => {
    if (probeTimer.current) {
      window.clearTimeout(probeTimer.current)
      probeTimer.current = null
    }
    const url = importDraft.url.trim()
    setProbeErr('')
    if (!url) {
      setProbe(null)
      setProbing(false)
      return
    }
    if (!/^https?:\/\//.test(url)) {
      setProbe(null)
      setProbing(false)
      return
    }
    probeTimer.current = window.setTimeout(async () => {
      setProbing(true)
      try {
        setProbe(await api.probeSkill(url))
      } catch (e) {
        setProbe(null)
        setProbeErr(extractApiError(e, t('skillPool.opFailed')))
      } finally {
        setProbing(false)
      }
    }, 500)
    return () => {
      if (probeTimer.current) window.clearTimeout(probeTimer.current)
    }
  }, [importDraft.url])

  // 加载完成后，如 URL 带 highlight，滚动到对应卡片
  useEffect(() => {
    if (!highlightSlug || loading) return
    const el = cardRefs.current[highlightSlug]
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [highlightSlug, loading, skills])

  const handleRefreshBundle = async (skill: SkillPoolItem) => {
    setBusy(true)
    setError('')
    try {
      await api.refreshSkill(skill.slug)
      await load()
    } catch (e) {
      setError(extractApiError(e, t('skillPool.opFailed')))
    } finally {
      setBusy(false)
    }
  }

  const handlePreviewMd = async (skill: SkillPoolItem) => {
    setMdErr('')
    setMdLoading(true)
    setMdPayload({ slug: skill.slug, local_dir: '', content: '', files: [] }) // 打开抽屉先显示 loading
    try {
      setMdPayload(await api.getSkillMd(skill.slug))
    } catch (e) {
      setMdErr(extractApiError(e, t('skillPool.opFailed')))
    } finally {
      setMdLoading(false)
    }
  }

  const groups = useMemo(() => {
    const acc: Record<string, SkillPoolItem[]> = {}
    for (const s of skills) {
      const cat = s.category || t('skillPool.categoryGeneral')
      acc[cat] = acc[cat] || []
      acc[cat].push(s)
    }
    return acc
  }, [skills, t])

  const handleImport = async () => {
    if (!importDraft.url.trim()) return
    setBusy(true)
    setError('')
    try {
      await api.importSkill({
        url: importDraft.url.trim(),
        override_slug: importDraft.overrideSlug.trim() || null,
        category: importDraft.category.trim() || t('skillPool.categoryImport'),
      })
      setImportDraft(EMPTY_IMPORT)
      await load()
    } catch (e) {
      setError(extractApiError(e, t('skillPool.opFailed')))
    } finally {
      setBusy(false)
    }
  }

  const handleAiCreate = async () => {
    if (!aiDraft.slug.trim() || !aiDraft.name.trim() || !aiDraft.goal.trim()) return
    setBusy(true)
    setError('')
    setAiResultMsg('')
    try {
      const res = await api.aiCreateSkill({
        slug: aiDraft.slug.trim(),
        name: aiDraft.name.trim(),
        goal: aiDraft.goal.trim(),
        category: aiDraft.category.trim() || t('skillPool.categoryCustom'),
      })
      setAiResultMsg(res.message || t('skillPool.generatedOk'))
      setAiDraft(EMPTY_AI)
      await load()
    } catch (e) {
      setError(extractApiError(e, t('skillPool.opFailed')))
    } finally {
      setBusy(false)
    }
  }

  const handleToggle = async (skill: SkillPoolItem) => {
    setBusy(true)
    setError('')
    try {
      await api.updateSkill(skill.slug, { enabled: !skill.enabled })
      await load()
    } catch (e) {
      setError(extractApiError(e, t('skillPool.opFailed')))
    } finally {
      setBusy(false)
    }
  }

  const handleRename = async (skill: SkillPoolItem) => {
    const next = (editingName[skill.slug] ?? skill.name).trim()
    if (!next || next === skill.name) {
      setEditingName(prev => {
        const copy = { ...prev }
        delete copy[skill.slug]
        return copy
      })
      return
    }
    setBusy(true)
    setError('')
    try {
      await api.updateSkill(skill.slug, { name: next })
      setEditingName(prev => {
        const copy = { ...prev }
        delete copy[skill.slug]
        return copy
      })
      await load()
    } catch (e) {
      setError(extractApiError(e, t('skillPool.opFailed')))
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async (skill: SkillPoolItem) => {
    if (!confirm(t('skillPool.deleteConfirm', { slug: skill.slug }))) return
    setBusy(true)
    setError('')
    try {
      await api.deleteSkill(skill.slug)
      await load()
    } catch (e) {
      setError(extractApiError(e, t('skillPool.opFailed')))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="skill-pool-page">
      <div className="skill-pool-page__header">
        <div>
          <h1>{t('skillPool.title')}</h1>
          <p>{t('skillPool.introP1')}</p>
          <p className="skill-pool-page__hint">
            {t('skillPool.introP2')}
          </p>
        </div>
        <button type="button" className="btn btn-secondary" onClick={load} disabled={loading || busy}>
          {loading ? <Loader size={14} className="animate-pulse" /> : <RefreshCw size={14} />}
          {t('skillPool.refresh')}
        </button>
      </div>

      {error && (
        <div className="skill-pool-error">
          <AlertTriangle size={15} />
          <span>{error}</span>
        </div>
      )}

      {/* ── 导入区 ─────────────────────────────────────────────────── */}
      <section className="skill-pool-intake">
        <div className="skill-pool-intake__card">
          <header>
            <Download size={16} />
            <strong>{t('skillPool.importTitle')}</strong>
          </header>
          <p className="skill-pool-intake__hint">
            {t('skillPool.exploreHint')}<br />
            <code>https://clawhub.ai/skills?sort=downloads</code><br />
            <code>https://skillhub.cn/skills</code><br />
          </p>
          <input
            className="input-base"
            placeholder={t('skillPool.urlPlaceholder')}
            value={importDraft.url}
            onChange={e => setImportDraft(prev => ({ ...prev, url: e.target.value }))}
            disabled={busy}
          />
          {/* 实时识别 probe 结果 */}
          {importDraft.url.trim() !== '' && (
            <div className={`skill-pool-probe ${probeErr ? 'skill-pool-probe--err' : ''}`}>
              {probing && (
                <>
                  <Loader size={13} className="animate-pulse" />
                  <span>{t('skillPool.probing')}</span>
                </>
              )}
              {!probing && probe && (
                <>
                  <span className="skill-pool-probe__tag">
                    {probe.provider === 'clawhub' ? 'ClawHub' : 'SkillHub.cn'}
                  </span>
                  <span>
                    <b>{probe.slug}</b>
                    {probe.username ? ` · ${probe.username}` : ''}
                  </span>
                  <span className="skill-pool-probe__slug">{t('skillPool.willBeSlug')} <code>{probe.suggested_slug}</code></span>
                </>
              )}
              {!probing && !probe && probeErr && (
                <>
                  <AlertTriangle size={13} />
                  <span>{probeErr}</span>
                </>
              )}
            </div>
          )}
          <div className="skill-pool-intake__row">
            <input
              className="input-base"
              placeholder={t('skillPool.slugPlaceholder')}
              value={importDraft.overrideSlug}
              onChange={e => setImportDraft(prev => ({ ...prev, overrideSlug: e.target.value }))}
              disabled={busy}
            />
            <input
              className="input-base"
              placeholder={t('skillPool.categoryPlaceholder')}
              value={importDraft.category}
              onChange={e => setImportDraft(prev => ({ ...prev, category: e.target.value }))}
              disabled={busy}
            />
          </div>
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleImport}
            disabled={busy || !importDraft.url.trim()}
          >
            {busy ? <Loader size={14} className="animate-pulse" /> : <Download size={14} />}
            {t('skillPool.importBtn')}
          </button>
        </div>

        <div className="skill-pool-intake__card">
          <header>
            <Sparkles size={16} />
            <strong>{t('skillPool.aiTitle')}</strong>
            <button
              type="button"
              className="skill-pool-intake__toggle"
              onClick={() => { setShowAi(v => !v); setAiResultMsg('') }}
            >
              {showAi ? t('skillPool.toggleHide') : t('skillPool.toggleShow')}
            </button>
          </header>
          <p className="skill-pool-intake__hint">
            {t('skillPool.aiDesc')}
          </p>
          {showAi && (
            <>
              <div className="skill-pool-intake__row">
                <input
                  className="input-base"
                  placeholder={t('skillPool.aiSlugPh')}
                  value={aiDraft.slug}
                  onChange={e => setAiDraft(prev => ({ ...prev, slug: e.target.value }))}
                  disabled={busy}
                />
                <input
                  className="input-base"
                  placeholder={t('skillPool.aiNamePh')}
                  value={aiDraft.name}
                  onChange={e => setAiDraft(prev => ({ ...prev, name: e.target.value }))}
                  disabled={busy}
                />
                <input
                  className="input-base"
                  placeholder={t('skillPool.aiCategoryPh')}
                  value={aiDraft.category}
                  onChange={e => setAiDraft(prev => ({ ...prev, category: e.target.value }))}
                  disabled={busy}
                />
              </div>
              <textarea
                className="input-base"
                rows={4}
                placeholder={t('skillPool.aiGoalPh')}
                value={aiDraft.goal}
                onChange={e => setAiDraft(prev => ({ ...prev, goal: e.target.value }))}
                disabled={busy}
              />
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleAiCreate}
                disabled={busy || !aiDraft.slug.trim() || !aiDraft.name.trim() || !aiDraft.goal.trim()}
              >
                {busy ? <Loader size={14} className="animate-pulse" /> : <Sparkles size={14} />}
                {t('skillPool.generateBtn')}
              </button>
              {aiResultMsg && <pre className="skill-pool-ai-result">{aiResultMsg}</pre>}
            </>
          )}
        </div>
      </section>

      {/* ── 列表区 ─────────────────────────────────────────────────── */}
      {loading ? (
        <div className="skill-pool-empty">
          <Loader size={20} className="animate-pulse" />
          <span>{t('skillPool.loadingPool')}</span>
        </div>
      ) : (
        <div className="skill-pool-groups">
          {Object.entries(groups).map(([category, items]) => (
            <section key={category} className="skill-pool-group">
              <div className="skill-pool-group__title">
                <Wrench size={16} />
                <span>{category}</span>
                <em>{items.length}</em>
              </div>
              <div className="skill-pool-list">
                {items.map(skill => {
                  const bundle = getBundleConfig(skill)
                  const label = kindLabel(skill, t)
                  const editing = skill.slug in editingName
                  const currentName = editing ? editingName[skill.slug] : skill.name
                  return (
                    <article
                      key={skill.slug}
                      ref={el => { cardRefs.current[skill.slug] = el }}
                      className={[
                        'skill-pool-card',
                        !skill.enabled ? 'skill-pool-card--off' : '',
                        highlightSlug === skill.slug ? 'skill-pool-card--highlight' : '',
                      ].filter(Boolean).join(' ')}
                    >
                      <div className="skill-pool-card__top">
                        <div className="skill-pool-card__id">
                          <strong>{skill.slug}</strong>
                          <span className={`skill-pool-badge skill-pool-badge--${label.tone}`}>
                            {label.text}
                            {label.sub ? ` · ${label.sub}` : ''}
                          </span>
                        </div>
                        <button
                          className={`skill-pool-switch ${skill.enabled ? 'skill-pool-switch--on' : ''}`}
                          onClick={() => handleToggle(skill)}
                          disabled={busy}
                        >
                          {skill.enabled ? t('skillPool.toggleOn') : t('skillPool.toggleOff')}
                        </button>
                      </div>

                      <input
                        className="input-base skill-pool-card__input"
                        value={currentName}
                        onChange={e => setEditingName(prev => ({ ...prev, [skill.slug]: e.target.value }))}
                        onBlur={() => editing && handleRename(skill)}
                        onKeyDown={e => { if (e.key === 'Enter') { e.currentTarget.blur() } }}
                        disabled={busy}
                      />
                      <div className="skill-pool-card__desc">{skill.description || t('skillPool.noDesc')}</div>

                      {skill.kind === 'builtin' && (
                        <div className="skill-pool-card__builtin-hint">
                          <Code2 size={13} /> {t('skillPool.pythonImpl')}
                        </div>
                      )}

                      {skill.kind === 'bundle' && bundle && (
                        <div className="skill-pool-card__bundle">
                          <div className="skill-pool-card__bundle-meta">
                            <Package size={13} />
                            <span>{bundle.local_dir}</span>
                          </div>
                          {bundle.summary && (
                            <div className="skill-pool-card__bundle-summary">{bundle.summary}</div>
                          )}
                          {bundle.files && bundle.files.length > 0 && (
                            <details className="skill-pool-card__bundle-files">
                              <summary><FileCode2 size={12} /> {t('skillPool.filesSummary', { n: bundle.files.length })}</summary>
                              <ul>
                                {bundle.files.slice(0, 12).map(f => <li key={f}>{f}</li>)}
                                {bundle.files.length > 12 && <li>{t('skillPool.filesMore', { n: bundle.files.length - 12 })}</li>}
                              </ul>
                            </details>
                          )}
                          {bundle.source.url && (
                            <a className="skill-pool-card__bundle-source" href={bundle.source.url} target="_blank" rel="noreferrer">
                              {t('skillPool.viewSource')}
                            </a>
                          )}
                        </div>
                      )}

                      <div className="skill-pool-card__actions">
                        {skill.kind === 'bundle' && (
                          <>
                            <button type="button" className="btn btn-icon" onClick={() => handlePreviewMd(skill)} title={t('skillPool.viewSkillMd')} disabled={busy}>
                              <Eye size={14} />
                            </button>
                            {bundle?.source.provider && bundle.source.provider !== 'local' && (
                              <button
                                type="button"
                                className="btn btn-icon"
                                onClick={() => handleRefreshBundle(skill)}
                                title={t('skillPool.refreshFromSource')}
                                disabled={busy}
                              >
                                <RotateCcw size={14} />
                              </button>
                            )}
                          </>
                        )}
                        {!skill.builtin && (
                          <button type="button" className="btn btn-icon icon-danger" onClick={() => handleDelete(skill)} title={t('skillPool.deleteTitle')} disabled={busy}>
                            <Trash2 size={14} />
                          </button>
                        )}
                        {skill.enabled && <span className="skill-pool-card__ok"><Check size={13} /> {t('skillPool.assignable')}</span>}
                      </div>
                    </article>
                  )
                })}
              </div>
            </section>
          ))}
        </div>
      )}

      <div className="skill-pool-footer">
        <Search size={14} />
        {t('skillPool.footerHint')}
      </div>

      {/* SKILL.md 预览抽屉 */}
      {mdPayload && (
        <div className="skill-pool-drawer" role="dialog" aria-label={t('skillPool.drawerAria')}>
          <div className="skill-pool-drawer__mask" onClick={() => setMdPayload(null)} />
          <aside className="skill-pool-drawer__panel">
            <header>
              <FileCode2 size={16} />
              <strong>{mdPayload.slug}</strong>
              {mdPayload.local_dir && <span className="skill-pool-drawer__path">{mdPayload.local_dir}</span>}
              <button type="button" className="btn btn-icon" onClick={() => setMdPayload(null)} title={t('skillPool.close')}>
                <X size={15} />
              </button>
            </header>
            {mdLoading && (
              <div className="skill-pool-drawer__loading">
                <Loader size={15} className="animate-pulse" /> {t('skillPool.loadingMd')}
              </div>
            )}
            {mdErr && (
              <div className="skill-pool-error">
                <AlertTriangle size={14} />
                <span>{mdErr}</span>
              </div>
            )}
            {!mdLoading && !mdErr && (
              <pre className="skill-pool-drawer__md">{mdPayload.content}</pre>
            )}
          </aside>
        </div>
      )}
    </div>
  )
}
