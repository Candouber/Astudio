import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import type { PathNode, PathEdge, Annotation, SubTask } from '../../types'
import {
  ChevronDown,
  ChevronRight,
  AlertTriangle,
  CheckCircle,
  RotateCcw,
  Loader,
  Send,
  ChevronsDown,
  ChevronsUp,
  MessageSquareText,
  Quote,
  ArrowUpToLine,
  ArrowDownToLine,
  Layers,
  CornerDownRight,
  Pencil,
  Clock,
  Cpu,
  CircleDollarSign,
  CheckCheck,
  X,
} from 'lucide-react'
import { parseSSEStream } from '../../api/sse'
import { useTaskStore } from '../../stores/taskStore'
import MarkdownRenderer from '../common/MarkdownRenderer'
import { useI18n } from '../../i18n/useI18n'
import { translateStatusMessage } from '../../i18n/translateStatusMessage'
import { useLocaleStore } from '../../stores/localeStore'
import {
  applyHighlights,
  clearHighlights,
  annotationSignature,
} from '../../utils/annotationHighlight'
import './DeliverableList.css'

interface Props {
  nodes: PathNode[]
  /** 主任务图的 edges（优先用）。缺省时退化成 parent_id 单向链 */
  edges?: PathEdge[]
  /** 与 PathNode 对应的 SubTask 元数据，用来展示 tokens / 耗时 / 成本 / 人类编辑痕迹 */
  subTasks?: SubTask[]
  taskId: string
  annotations?: Annotation[]
  onDeleteAnnotation?: (id: string) => void
}

/** 格式化毫秒时长为 xs / xm y s */
function formatDuration(ms?: number): string {
  if (!ms || ms <= 0) return ''
  const totalSec = Math.round(ms / 1000)
  if (totalSec < 60) return `${totalSec}s`
  const m = Math.floor(totalSec / 60)
  const s = totalSec % 60
  return s === 0 ? `${m}m` : `${m}m${s}s`
}

/** token 数量缩写：1234 -> 1.2k */
function formatTokens(n?: number): string {
  if (!n || n <= 0) return ''
  if (n < 1000) return `${n}`
  return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`
}

/** USD → CNY 粗略换算（对齐后端 FX_USD_CNY=7.2） */
const USD_TO_CNY = 7.2

function formatCostCNY(usd?: number): string {
  if (!usd || usd <= 0) return ''
  const cny = usd * USD_TO_CNY
  if (cny < 0.01) return '¥<0.01'
  if (cny < 1) return `¥${cny.toFixed(2)}`
  return `¥${cny.toFixed(cny < 10 ? 2 : 1)}`
}

// ─────────────────────────────────────────────────────────────────
//  DAG 层级计算
// ─────────────────────────────────────────────────────────────────

interface GraphIndex {
  /** 本层级排好序的所有子节点 */
  orderedNodes: PathNode[]
  /** level -> 属于该层级的节点 */
  stages: { level: number; nodes: PathNode[] }[]
  /** nodeId -> [上游 nodeId, ...]（直接依赖） */
  upstreamOf: Record<string, string[]>
  /** nodeId -> [下游 nodeId, ...] */
  downstreamOf: Record<string, string[]>
  /** nodeId -> 全局展示序号（1 based，按层级 + 层内顺序） */
  displayIndex: Record<string, number>
  /** nodeId -> level（0 based） */
  levelOf: Record<string, number>
}

function buildGraphIndex(
  agentNodes: PathNode[],
  edges?: PathEdge[],
): GraphIndex {
  const agentIds = new Set(agentNodes.map(n => n.id))
  const upstream: Record<string, string[]> = {}
  const downstream: Record<string, string[]> = {}

  const pushUnique = (map: Record<string, string[]>, k: string, v: string) => {
    const arr = map[k] ?? (map[k] = [])
    if (!arr.includes(v)) arr.push(v)
  }

  // 优先用 main 类型边；其次退化到 parent_id
  const mainEdges = (edges ?? []).filter(e => e.type === 'main')
  if (mainEdges.length > 0) {
    for (const e of mainEdges) {
      if (!agentIds.has(e.source) || !agentIds.has(e.target)) continue
      pushUnique(upstream, e.target, e.source)
      pushUnique(downstream, e.source, e.target)
    }
  } else {
    for (const n of agentNodes) {
      if (n.parent_id && agentIds.has(n.parent_id)) {
        pushUnique(upstream, n.id, n.parent_id)
        pushUnique(downstream, n.parent_id, n.id)
      }
    }
  }

  // 递归算 level，带防环
  const levelOf: Record<string, number> = {}
  const visit = (id: string, stack: Set<string>): number => {
    if (levelOf[id] !== undefined) return levelOf[id]
    if (stack.has(id)) return 0
    stack.add(id)
    const parents = upstream[id] ?? []
    const lvl = parents.length
      ? Math.max(...parents.map(p => visit(p, stack))) + 1
      : 0
    stack.delete(id)
    levelOf[id] = lvl
    return lvl
  }
  for (const n of agentNodes) visit(n.id, new Set())

  // 按 level 分组，组内保持 nodes 原始顺序（通常是创建顺序）
  const stageMap: Record<number, PathNode[]> = {}
  for (const n of agentNodes) {
    const l = levelOf[n.id] ?? 0
    ;(stageMap[l] ??= []).push(n)
  }
  const stages = Object.keys(stageMap)
    .map(k => Number(k))
    .sort((a, b) => a - b)
    .map(level => ({ level, nodes: stageMap[level] }))

  // 全局展示序号：按 (level, 层内顺序) 扁平化
  const displayIndex: Record<string, number> = {}
  const orderedNodes: PathNode[] = []
  let counter = 1
  for (const s of stages) {
    for (const n of s.nodes) {
      displayIndex[n.id] = counter++
      orderedNodes.push(n)
    }
  }

  return {
    orderedNodes,
    stages,
    upstreamOf: upstream,
    downstreamOf: downstream,
    displayIndex,
    levelOf,
  }
}

// ─────────────────────────────────────────────────────────────────
//  共用小组件
// ─────────────────────────────────────────────────────────────────

function RetryInline({
  node,
  taskId,
  isCompleted = false,
}: {
  node: PathNode
  taskId: string
  isCompleted?: boolean
}) {
  const { t, locale } = useI18n()
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [statusMsg, setStatusMsg] = useState('')
  const updateTaskStatus = useTaskStore(s => s.updateTaskStatus)

  const handleRetry = async () => {
    if (!isCompleted && !text.trim()) return
    setLoading(true)
    setStatusMsg(t('deliverableList.submitting'))
    try {
      const res = await fetch(`/api/tasks/${taskId}/retry-step`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ node_id: node.id, extra_context: text }),
      })
      if (!res.ok || !res.body) throw new Error(t('common.requestFailed'))
      updateTaskStatus('executing')
      parseSSEStream(
        res.body.getReader(),
        (ev, raw) => {
          try {
            const data = JSON.parse(raw)
            if (ev === 'status') setStatusMsg(data.message || '')
            if (ev === 'done') {
              setLoading(false)
              setOpen(false)
              setText('')
            }
          } catch { /* ignore */ }
        },
        () => setLoading(false),
      )
    } catch {
      setStatusMsg(t('deliverableList.requestFailedRetry'))
      setLoading(false)
    }
  }

  const btnLabel = isCompleted ? t('deliverableList.btnRerun') : t('deliverableList.btnRetry')
  const btnClass = isCompleted ? 'btn btn-sm btn-secondary' : 'btn btn-sm btn-warning'
  const placeholder = isCompleted
    ? t('deliverableList.phRerun')
    : t('deliverableList.phRetry')

  return (
    <div className={`retry-inline ${isCompleted ? 'retry-inline--action' : ''} ${open ? 'retry-inline--open' : ''}`}>
      {!open ? (
        <button type="button" className={btnClass} onClick={() => setOpen(true)}>
          <RotateCcw size={13} />
          {btnLabel}
        </button>
      ) : (
        <div className="retry-inline__form">
          <textarea
            className="input-base retry-inline__textarea"
            rows={3}
            placeholder={placeholder}
            value={text}
            onChange={e => setText(e.target.value)}
            disabled={loading}
            autoFocus
          />
          <div className="retry-inline__actions">
            {statusMsg && (
              <span className="retry-inline__status">
                {loading && <Loader size={12} className="animate-spin" />}
                {translateStatusMessage(locale, statusMsg)}
              </span>
            )}
            <button
              type="button"
              className="btn btn-sm btn-primary"
              onClick={handleRetry}
              disabled={loading || (!isCompleted && !text.trim())}
            >
              <Send size={13} />
              {loading ? t('deliverableList.executing') : t('deliverableList.confirm')}
            </button>
            <button
              type="button"
              className="btn btn-sm btn-secondary"
              onClick={() => { setOpen(false); setText('') }}
              disabled={loading}
            >
              {t('common.cancel')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────
//  编辑输出（手动改写 + 自动级联下游）
// ─────────────────────────────────────────────────────────────────

function EditInline({
  node,
  taskId,
  initialContent,
}: {
  node: PathNode
  taskId: string
  initialContent: string
}) {
  const { t, locale } = useI18n()
  const [open, setOpen] = useState(false)
  const [text, setText] = useState(initialContent)
  const [loading, setLoading] = useState(false)
  const [statusMsg, setStatusMsg] = useState('')
  const [cascade, setCascade] = useState(true)
  const updateTaskStatus = useTaskStore(s => s.updateTaskStatus)

  // 每次打开时，从最新 output 重新初始化，避免滞留旧草稿
  useEffect(() => {
    if (open) setText(initialContent)
  }, [open, initialContent])

  const handleSubmit = async () => {
    if (!text.trim()) return
    setLoading(true)
    setStatusMsg(t('deliverableList.writing'))
    try {
      const res = await fetch(`/api/tasks/${taskId}/edit-step`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          node_id: node.id,
          new_output: text,
          cascade,
        }),
      })
      if (!res.ok || !res.body) throw new Error(t('common.requestFailed'))
      if (cascade) updateTaskStatus('executing')
      parseSSEStream(
        res.body.getReader(),
        (ev, raw) => {
          try {
            const data = JSON.parse(raw)
            if (ev === 'status') setStatusMsg(data.message || '')
            if (ev === 'edit_committed') setStatusMsg(t('deliverableList.savedRefreshing'))
            if (ev === 'done') {
              setLoading(false)
              setOpen(false)
            }
          } catch { /* ignore */ }
        },
        () => setLoading(false),
      )
    } catch {
      setStatusMsg(t('deliverableList.saveFailed'))
      setLoading(false)
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        className="btn btn-sm btn-secondary deliverable-item__edit-btn"
        onClick={(e) => { e.stopPropagation(); setOpen(true) }}
        title={t('deliverableList.editTitle')}
      >
        <Pencil size={12} />
        {t('deliverableList.editBtn')}
      </button>
    )
  }

  return (
    <div className="edit-inline" onClick={(e) => e.stopPropagation()}>
      <textarea
        className="input-base edit-inline__textarea"
        rows={10}
        value={text}
        onChange={e => setText(e.target.value)}
        disabled={loading}
        autoFocus
      />
      <label className="edit-inline__cascade">
        <input
          type="checkbox"
          checked={cascade}
          onChange={e => setCascade(e.target.checked)}
          disabled={loading}
        />
        {t('deliverableList.editHint')}
      </label>
      <div className="edit-inline__actions">
        {statusMsg && (
          <span className="edit-inline__status">
            {loading && <Loader size={12} className="animate-spin" />}
            {translateStatusMessage(locale, statusMsg)}
          </span>
        )}
        <button
          type="button"
          className="btn btn-sm btn-primary"
          onClick={handleSubmit}
          disabled={loading || !text.trim()}
        >
          <CheckCheck size={13} />
          {loading ? t('deliverableList.saving') : t('deliverableList.save')}
        </button>
        <button
          type="button"
          className="btn btn-sm btn-secondary"
          onClick={() => { setOpen(false); setStatusMsg('') }}
          disabled={loading}
        >
          <X size={13} />
          {t('common.cancel')}
        </button>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────
//  单步 metrics 行（tokens / 耗时 / 成本 / 人工编辑痕迹）
// ─────────────────────────────────────────────────────────────────

function MetricsRow({ st }: { st?: SubTask }) {
  const { t } = useI18n()
  if (!st) return null
  const tok = formatTokens(st.tokens)
  const dur = formatDuration(st.duration_ms)
  const cost = formatCostCNY(st.cost_usd)
  if (!tok && !dur && !cost && !st.edited_by_user) return null

  return (
    <div className="deliverable-item__metrics">
      {dur && (
        <span className="metric-chip" title={st.started_at ? t('deliverableList.metricStarted', { time: st.started_at }) : undefined}>
          <Clock size={11} />
          {dur}
        </span>
      )}
      {tok && (
        <span
          className="metric-chip"
          title={st.model_name ? t('deliverableList.metricModel', { name: st.model_name }) : undefined}
        >
          <Cpu size={11} />
          {tok} tok
        </span>
      )}
      {cost && (
        <span className="metric-chip metric-chip--cost" title={t('deliverableList.costTooltip')}>
          <CircleDollarSign size={11} />
          {cost}
        </span>
      )}
      {st.edited_by_user && (
        <span className="metric-chip metric-chip--edited" title={t('deliverableList.editedTooltip')}>
          <Pencil size={11} />
          {t('deliverableList.editedBadge')}
        </span>
      )}
    </div>
  )
}

const COLLAPSE_THRESHOLD = 400

function ExpandableContent({
  nodeId,
  content,
  annotations,
  onHighlightClick,
}: {
  nodeId: string
  content: string
  annotations: Annotation[]
  onHighlightClick: (annId: string) => void
}) {
  const { t } = useI18n()
  const ref = useRef<HTMLDivElement>(null)
  const [needsCollapse, setNeedsCollapse] = useState(false)
  const [collapsed, setCollapsed] = useState(true)

  useEffect(() => {
    if (ref.current && ref.current.scrollHeight > COLLAPSE_THRESHOLD + 60) {
      setNeedsCollapse(true)
    }
  }, [content])

  const annSignature = useMemo(() => annotationSignature(annotations), [annotations])

  useEffect(() => {
    const container = ref.current
    if (!container) return
    if (annotations.length === 0) {
      clearHighlights(container)
      return
    }
    const raf = requestAnimationFrame(() => {
      if (ref.current) applyHighlights(ref.current, annotations)
    })
    return () => {
      cancelAnimationFrame(raf)
      if (container.isConnected) clearHighlights(container)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [content, annSignature])

  const handleClick = useCallback((e: React.MouseEvent) => {
    const mark = (e.target as HTMLElement).closest('mark.ann-hl') as HTMLElement | null
    if (mark?.dataset.annId) {
      onHighlightClick(mark.dataset.annId)
    }
  }, [onHighlightClick])

  return (
    <div className="deliverable-item__content" data-node-id={nodeId} onClick={handleClick}>
      <div
        ref={ref}
        className={`deliverable-item__content-wrap${needsCollapse && collapsed ? ' deliverable-item__content-wrap--collapsed' : ''}`}
        style={needsCollapse && collapsed ? { maxHeight: COLLAPSE_THRESHOLD } : undefined}
      >
        <MarkdownRenderer content={content} />
      </div>
      {needsCollapse && (
        <button
          className="deliverable-item__expand-btn"
          onClick={() => setCollapsed(v => !v)}
        >
          {collapsed ? <><ChevronsDown size={14} /> {t('deliverableList.expandFull')}</> : <><ChevronsUp size={14} /> {t('deliverableList.collapseFull')}</>}
        </button>
      )}
    </div>
  )
}

function CollapsibleAnnotationCard({
  ann,
  isExpanded,
  onToggle,
  onDelete,
}: {
  ann: Annotation
  isExpanded: boolean
  onToggle: () => void
  onDelete?: (id: string) => void
}) {
  const { t } = useI18n()
  const uiLocale = useLocaleStore(s => s.locale)
  const localeTag = uiLocale === 'zh' ? 'zh-CN' : 'en-US'
  return (
    <div
      id={`ann-inline-${ann.id}`}
      className={`ann-inline ${isExpanded ? 'ann-inline--open' : ''}`}
    >
      <div className="ann-inline__header" onClick={onToggle}>
        <span className="ann-inline__chevron">
          {isExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </span>
        <Quote size={11} className="ann-inline__quote-icon" />
        <span className="ann-inline__q">
          {ann.question.length > 50 ? ann.question.slice(0, 50) + '…' : ann.question}
        </span>
      </div>
      {isExpanded && (
        <div className="ann-inline__body">
          <div className="ann-inline__selected">
            <Quote size={10} />
            <span>{ann.selected_text.length > 120 ? ann.selected_text.slice(0, 120) + '…' : ann.selected_text}</span>
          </div>
          <div className="ann-inline__question">{ann.question}</div>
          {ann.answer && (
            <div className="ann-inline__answer">
              <MarkdownRenderer content={ann.answer} />
            </div>
          )}
          {onDelete && (
            <div className="ann-inline__footer">
              <span className="ann-inline__time">
                {new Date(ann.created_at).toLocaleString(localeTag, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
              </span>
              <button type="button" className="ann-inline__delete" onClick={(e) => { e.stopPropagation(); onDelete(ann.id) }}>
                {t('deliverableList.delete')}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────
//  依赖 chip（点击可跳到对应卡片）
// ─────────────────────────────────────────────────────────────────

function DepChips({
  direction,
  ids,
  nodes,
  displayIndex,
  onJumpTo,
}: {
  direction: 'up' | 'down'
  ids: string[]
  nodes: PathNode[]
  displayIndex: Record<string, number>
  onJumpTo: (nodeId: string) => void
}) {
  const { t } = useI18n()
  if (ids.length === 0) return null
  const isUp = direction === 'up'
  const nodeById = new Map(nodes.map(n => [n.id, n]))

  return (
    <div className={`dep-chips dep-chips--${direction}`}>
      <span className="dep-chips__label">
        {isUp ? <ArrowUpToLine size={11} /> : <ArrowDownToLine size={11} />}
        {isUp ? t('deliverableList.depUp') : t('deliverableList.depDown')}
      </span>
      {ids.map(id => {
        const n = nodeById.get(id)
        if (!n) return null
        const num = displayIndex[id]
        return (
          <button
            key={id}
            className={`dep-chip dep-chip--${n.status}`}
            onClick={(e) => { e.stopPropagation(); onJumpTo(id) }}
            title={`${n.agent_role} · ${n.step_label}`}
          >
            <span className="dep-chip__num">#{num}</span>
            <span className="dep-chip__label">{n.step_label}</span>
          </button>
        )
      })}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────
//  单张交付卡片
// ─────────────────────────────────────────────────────────────────

function DeliverableCard({
  node,
  taskId,
  displayNumber,
  upstreamIds,
  downstreamIds,
  allNodes,
  displayIndex,
  annotations,
  expanded,
  onToggleExpand,
  expandedAnnId,
  onToggleAnn,
  onHighlightClick,
  onDeleteAnnotation,
  onJumpTo,
  subTask,
}: {
  node: PathNode
  taskId: string
  displayNumber: number
  upstreamIds: string[]
  downstreamIds: string[]
  allNodes: PathNode[]
  displayIndex: Record<string, number>
  annotations: Annotation[]
  expanded: boolean
  onToggleExpand: () => void
  expandedAnnId: string | null
  onToggleAnn: (annId: string) => void
  onHighlightClick: (annId: string) => void
  onDeleteAnnotation?: (id: string) => void
  onJumpTo: (nodeId: string) => void
  subTask?: SubTask
}) {
  const { t } = useI18n()
  const isCompleted = node.status === 'completed'
  const isError = node.status === 'error'
  const isSkipped = isError && node.output?.startsWith('[SKIPPED]')
  const isCrash = isError && node.output?.startsWith('[CRASH]')
  const isBlocked = isError && !isSkipped && !isCrash

  const blockerReason = node.output
    ?.replace(/^\[BLOCKED\]\s*/, '')
    ?.replace(/^\[SKIPPED\]\s*/, '')
    ?.replace(/^\[CRASH\]:\s*/, '')
    || ''

  const cardClass = [
    'deliverable-item',
    'card',
    isError ? 'deliverable-item--error' : '',
    isSkipped ? 'deliverable-item--skipped' : '',
    expanded ? 'deliverable-item--expanded' : '',
  ].filter(Boolean).join(' ')

  return (
    <div
      id={`deliverable-${node.id}`}
      className={cardClass}
      data-node-id={node.id}
    >
      {/* ── 头部：序号 + 角色 + 状态 ── */}
      <div
        className="deliverable-item__header"
        onClick={() => !isError && onToggleExpand()}
        style={{ cursor: isError ? 'default' : 'pointer' }}
      >
        <span className="deliverable-item__num" title={t('deliverableList.stepNumTitle', { n: displayNumber })}>
          #{displayNumber}
        </span>
        <span className="deliverable-item__toggle">
          {isError ? null : (expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />)}
        </span>
        <span className="deliverable-item__icon">
          {isError ? <AlertTriangle size={14} /> : <CheckCircle size={14} />}
        </span>
        <span className="deliverable-item__role">{node.agent_role}</span>
        <span className="deliverable-item__label">{node.step_label}</span>
        {annotations.length > 0 && (
          <span className="deliverable-item__ann-count" title={t('deliverableList.annCountTitle', { count: annotations.length })}>
            <MessageSquareText size={12} />
            {annotations.length}
          </span>
        )}
        <span className={`status-badge status--${node.status} ${isSkipped ? 'status--skipped' : ''}`}>
          {isSkipped ? t('deliverableList.statusSkipped') : isBlocked ? t('deliverableList.statusBlocked') : isCrash ? t('deliverableList.statusCrash') : t('deliverableList.statusCompleted')}
        </span>
      </div>

      {/* ── 本步观测：耗时 / token / 成本 / 人工编辑痕迹 ── */}
      <MetricsRow st={subTask} />

      {/* ── 依赖关系区（始终显示，方便一眼看懂数据流向） ── */}
      {(upstreamIds.length > 0 || downstreamIds.length > 0) && (
        <div className="deliverable-item__deps">
          <DepChips
            direction="up"
            ids={upstreamIds}
            nodes={allNodes}
            displayIndex={displayIndex}
            onJumpTo={onJumpTo}
          />
          <DepChips
            direction="down"
            ids={downstreamIds}
            nodes={allNodes}
            displayIndex={displayIndex}
            onJumpTo={onJumpTo}
          />
        </div>
      )}

      {/* ── 阻塞/跳过原因 ── */}
      {isError && blockerReason && (
        <div className="deliverable-item__blocker">
          <span className="deliverable-item__blocker-label">
            {isSkipped ? t('subTaskPanel.skipReason') : t('subTaskPanel.blockReason')}
          </span>
          <p className="deliverable-item__blocker-text">{blockerReason}</p>
        </div>
      )}

      {isBlocked && <RetryInline node={node} taskId={taskId} />}
      {isCompleted && (
        <div className="deliverable-item__action-row">
          <RetryInline node={node} taskId={taskId} isCompleted />
          <EditInline node={node} taskId={taskId} initialContent={node.output || ''} />
        </div>
      )}

      {/* ── 展开的产出内容 ── */}
      {isCompleted && expanded && node.output && (
        <>
          <ExpandableContent
            nodeId={node.id}
            content={node.output}
            annotations={annotations}
            onHighlightClick={onHighlightClick}
          />
          {annotations.length > 0 && (
            <div className="deliverable-item__annotations">
              <span className="deliverable-item__annotations-label">
                <MessageSquareText size={12} /> {t('deliverableList.annotateLine', { count: annotations.length })}
              </span>
              {annotations.map(ann => (
                <CollapsibleAnnotationCard
                  key={ann.id}
                  ann={ann}
                  isExpanded={expandedAnnId === ann.id}
                  onToggle={() => onToggleAnn(ann.id)}
                  onDelete={onDeleteAnnotation}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────
//  主组件：分阶段流水线
// ─────────────────────────────────────────────────────────────────

export default function DeliverableList({
  nodes,
  edges,
  subTasks,
  taskId,
  annotations = [],
  onDeleteAnnotation,
}: Props) {
  const { t } = useI18n()
  const agentNodes = useMemo(
    () => nodes.filter(n => n.type === 'sub_agent'),
    [nodes],
  )

  const graph = useMemo(
    () => buildGraphIndex(agentNodes, edges),
    [agentNodes, edges],
  )

  // node.id → 对应的 SubTask；后端以 group_id 关联 path_node.id
  const subTaskByNodeId = useMemo(() => {
    const m = new Map<string, SubTask>()
    for (const st of subTasks ?? []) {
      if (st.group_id) m.set(st.group_id, st)
    }
    return m
  }, [subTasks])

  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [expandedAnnId, setExpandedAnnId] = useState<string | null>(null)
  const [flashId, setFlashId] = useState<string | null>(null)

  const handleHighlightClick = useCallback((annId: string) => {
    setExpandedAnnId(annId)
    requestAnimationFrame(() => {
      const el = document.getElementById(`ann-inline-${annId}`)
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    })
  }, [])

  // 点击上下游 chip 时，滚动并高亮目标卡片；同时自动展开（方便阅读）
  const handleJumpTo = useCallback((nodeId: string) => {
    setExpandedId(nodeId)
    setFlashId(nodeId)
    requestAnimationFrame(() => {
      const el = document.getElementById(`deliverable-${nodeId}`)
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
    // flash 1.2s 后自动清除
    setTimeout(() => setFlashId(prev => (prev === nodeId ? null : prev)), 1200)
  }, [])

  if (agentNodes.length === 0) return null

  const hasMultiStage = graph.stages.length > 1
  const maxParallel = graph.stages.reduce((m, s) => Math.max(m, s.nodes.length), 0)

  return (
    <div className="deliverable-list">
      {/* ── 顶部概览（仅多阶段时显示，否则显得啰嗦） ── */}
      {hasMultiStage && (
        <div className="pipeline-overview">
          <span className="pipeline-overview__chip">
            <Layers size={12} />
            {t('deliverableList.stagesTotal', { n: graph.stages.length })}
          </span>
          <span className="pipeline-overview__chip">
            {t('deliverableList.stepsTotal', { n: agentNodes.length })}
          </span>
          {maxParallel > 1 && (
            <span className="pipeline-overview__chip pipeline-overview__chip--hl">
              {t('deliverableList.maxParallel', { n: maxParallel })}
            </span>
          )}
        </div>
      )}

      {graph.stages.map((stage, idx) => {
        const isLast = idx === graph.stages.length - 1
        const parallelCount = stage.nodes.length
        const stageNumber = idx + 1

        return (
          <div key={stage.level} className="pipeline-stage">
            {/* 阶段头：[阶段 N · K 项并行] */}
            {hasMultiStage && (
              <div className="pipeline-stage__header">
                <span className="pipeline-stage__badge">
                  <Layers size={11} />
                  {t('deliverableList.stageN', { n: stageNumber })}
                </span>
                <span className="pipeline-stage__meta">
                  {parallelCount > 1
                    ? t('deliverableList.parallelN', { n: parallelCount })
                    : t('deliverableList.singleItem')}
                </span>
                <span className="pipeline-stage__line" />
              </div>
            )}

            <div
              className={`pipeline-stage__body ${
                parallelCount > 1 ? 'pipeline-stage__body--grid' : ''
              }`}
            >
              {stage.nodes.map(node => {
                const nodeAnnotations = annotations.filter(a => a.node_id === node.id)
                return (
                  <div
                    key={node.id}
                    className={`pipeline-stage__slot ${flashId === node.id ? 'pipeline-stage__slot--flash' : ''}`}
                  >
                    <DeliverableCard
                      node={node}
                      taskId={taskId}
                      displayNumber={graph.displayIndex[node.id]}
                      upstreamIds={graph.upstreamOf[node.id] ?? []}
                      downstreamIds={graph.downstreamOf[node.id] ?? []}
                      allNodes={agentNodes}
                      displayIndex={graph.displayIndex}
                      annotations={nodeAnnotations}
                      expanded={expandedId === node.id}
                      onToggleExpand={() =>
                        setExpandedId(prev => (prev === node.id ? null : node.id))
                      }
                      expandedAnnId={expandedAnnId}
                      onToggleAnn={(annId) =>
                        setExpandedAnnId(prev => (prev === annId ? null : annId))
                      }
                      onHighlightClick={handleHighlightClick}
                      onDeleteAnnotation={onDeleteAnnotation}
                      onJumpTo={handleJumpTo}
                      subTask={subTaskByNodeId.get(node.id)}
                    />
                  </div>
                )
              })}
            </div>

            {/* 阶段间连接线（最后一个阶段不画） */}
            {hasMultiStage && !isLast && (
              <div className="pipeline-stage__connector" aria-hidden>
                <CornerDownRight size={14} />
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
