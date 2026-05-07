import { useState, useCallback, useEffect, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import type { PathNode, PathEdge, TaskStatus, Annotation, SubTask, Sandbox, SandboxFile, TaskIteration } from '../../types'
import { api } from '../../api/client'
import { connectTaskStream } from '../../api/sse'
import { useTaskStore } from '../../stores/taskStore'
import MarkdownRenderer from '../common/MarkdownRenderer'
import SelectionToolbar from './SelectionToolbar'
import ProcessSelectionPopover from './ProcessSelectionPopover'
import AnnotationComposerPopover from './AnnotationComposerPopover'
import AnnotationPopover from './AnnotationPopover'
import ResultChat from './ResultChat'
import { useI18n } from '../../i18n/useI18n'
import { translateStatusMessage } from '../../i18n/translateStatusMessage'
import {
  applyHighlights,
  clearHighlights,
  annotationSignature,
} from '../../utils/annotationHighlight'
import {
  CheckCircle,
  AlertTriangle,
  ArrowLeft,
  ExternalLink,
  MessageSquareText,
  RotateCcw,
  Clock,
  Cpu,
  CircleDollarSign,
  Sparkles,
  FileText,
  FileSpreadsheet,
  Image as ImageIcon,
  Pencil,
  Save,
  X,
  ChevronDown,
  ChevronUp,
  CornerDownRight,
} from 'lucide-react'
import './ResultView.css'

const USD_TO_CNY = 7.2
const OUTPUT_NODE_PREFIX = '__output__:'
const MAX_OUTPUT_SCAN_DEPTH = 4
const MAX_OUTPUT_FILES = 80

interface Props {
  taskId: string
  question: string
  status: TaskStatus
  nodes: PathNode[]
  studioId?: string
  statusMessage?: string
}

interface GraphIndex {
  orderedNodes: PathNode[]
  displayIndex: Record<string, number>
  upstreamOf: Record<string, string[]>
  downstreamOf: Record<string, string[]>
}

interface SynthesisSection {
  id: string
  title: string
  body: string
}

interface SelectionPayload {
  selectedText: string
  nodeId: string
  anchorRect: DOMRect
}

interface ResultIterationTab {
  id: string
  iteration: TaskIteration | null
  label: string
  title: string
  rootNode?: PathNode
  agentNodes: PathNode[]
  subTasks: SubTask[]
}

function outputNodeId(path: string) {
  return `${OUTPUT_NODE_PREFIX}${path}`
}

function isMarkdownFile(path: string) {
  return /\.(md|markdown|txt)$/i.test(path)
}

function isCsvFile(path: string) {
  return /\.csv$/i.test(path)
}

function isJsonFile(path: string) {
  return /\.json$/i.test(path)
}

function isImageFile(path: string) {
  return /\.(png|jpe?g|gif|webp|svg)$/i.test(path)
}

function isPdfFile(path: string) {
  return /\.pdf$/i.test(path)
}

function isHtmlFile(path: string) {
  return /\.html?$/i.test(path)
}

function isReadableOutput(path: string) {
  return isMarkdownFile(path) || isCsvFile(path) || isJsonFile(path)
}

function csvToMarkdown(content: string, maxRows = 80) {
  const rows = content.trim().split(/\r?\n/).slice(0, maxRows).map(row => row.split(',').map(cell => cell.trim()))
  if (rows.length === 0) return ''
  const width = Math.max(...rows.map(row => row.length))
  const normalized = rows.map(row => Array.from({ length: width }, (_, index) => row[index] || ''))
  const escape = (value: string) => value.replace(/\|/g, '\\|')
  const header = normalized[0].map(escape)
  const separator = Array.from({ length: width }, () => '---')
  const body = normalized.slice(1).map(row => `| ${row.map(escape).join(' | ')} |`)
  return [`| ${header.join(' | ')} |`, `| ${separator.join(' | ')} |`, ...body].join('\n')
}

function formatOutputContent(path: string, content: string) {
  if (isCsvFile(path)) return csvToMarkdown(content)
  if (isJsonFile(path)) return `\`\`\`json\n${content}\n\`\`\``
  return content
}

function formatBytes(size: number) {
  if (!size) return '0 B'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function fmtDuration(ms: number): string {
  if (!ms || ms <= 0) return '—'
  const totalSec = Math.round(ms / 1000)
  if (totalSec < 60) return `${totalSec}s`
  const m = Math.floor(totalSec / 60)
  const s = totalSec % 60
  return s === 0 ? `${m}m` : `${m}m${s}s`
}

function fmtTokens(n: number): string {
  if (!n || n <= 0) return '—'
  if (n < 1000) return `${n}`
  return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`
}

function fmtCostCNY(usd: number): string {
  if (!usd || usd <= 0) return '—'
  const cny = usd * USD_TO_CNY
  if (cny < 0.01) return '¥<0.01'
  if (cny < 1) return `¥${cny.toFixed(2)}`
  return `¥${cny.toFixed(cny < 10 ? 2 : 1)}`
}

function summarizeText(content: string | undefined, limit: number, emptyLabel: string) {
  const plain = (content || '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/[#>*_[\]()!|-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  if (!plain) return emptyLabel
  return plain.length > limit ? `${plain.slice(0, limit).trim()}…` : plain
}

function parseSynthesisSections(content: string, defaultTitle: string): SynthesisSection[] {
  const text = content.trim()
  if (!text) return []

  const lines = text.split('\n')
  const sections: SynthesisSection[] = []
  let currentTitle = defaultTitle
  let currentBody: string[] = []

  const flush = () => {
    const body = currentBody.join('\n').trim()
    if (!body) return
    sections.push({
      id: `section-${sections.length + 1}`,
      title: currentTitle,
      body,
    })
  }

  for (const line of lines) {
    const match = line.match(/^#{1,3}\s+(.+)$/)
    if (match) {
      flush()
      currentTitle = match[1].trim()
      currentBody = []
    } else {
      currentBody.push(line)
    }
  }

  flush()

  if (sections.length === 0) {
    return [{ id: 'section-1', title: defaultTitle, body: text }]
  }

  return sections
}

function buildGraphIndex(agentNodes: PathNode[], edges?: PathEdge[]): GraphIndex {
  const agentIds = new Set(agentNodes.map(n => n.id))
  const upstream: Record<string, string[]> = {}
  const downstream: Record<string, string[]> = {}

  const pushUnique = (map: Record<string, string[]>, key: string, value: string) => {
    const arr = map[key] ?? (map[key] = [])
    if (!arr.includes(value)) arr.push(value)
  }

  const mainEdges = (edges ?? []).filter(e => e.type === 'main')
  if (mainEdges.length > 0) {
    for (const edge of mainEdges) {
      if (!agentIds.has(edge.source) || !agentIds.has(edge.target)) continue
      pushUnique(upstream, edge.target, edge.source)
      pushUnique(downstream, edge.source, edge.target)
    }
  } else {
    for (const node of agentNodes) {
      if (node.parent_id && agentIds.has(node.parent_id)) {
        pushUnique(upstream, node.id, node.parent_id)
        pushUnique(downstream, node.parent_id, node.id)
      }
    }
  }

  const levelOf: Record<string, number> = {}
  const visit = (id: string, stack: Set<string>): number => {
    if (levelOf[id] !== undefined) return levelOf[id]
    if (stack.has(id)) return 0
    stack.add(id)
    const parents = upstream[id] ?? []
    const level = parents.length
      ? Math.max(...parents.map(parentId => visit(parentId, stack))) + 1
      : 0
    stack.delete(id)
    levelOf[id] = level
    return level
  }

  for (const node of agentNodes) visit(node.id, new Set())

  const orderedNodes = [...agentNodes].sort((a, b) => {
    const levelDiff = (levelOf[a.id] ?? 0) - (levelOf[b.id] ?? 0)
    if (levelDiff !== 0) return levelDiff
    return agentNodes.indexOf(a) - agentNodes.indexOf(b)
  })

  const displayIndex: Record<string, number> = {}
  orderedNodes.forEach((node, index) => {
    displayIndex[node.id] = index + 1
  })

  return { orderedNodes, displayIndex, upstreamOf: upstream, downstreamOf: downstream }
}

function HighlightedMarkdown({
  content,
  nodeId,
  annotations,
  onHighlightClick,
  className = 'result-view__synthesis-content',
}: {
  content: string
  nodeId: string
  annotations: Annotation[]
  onHighlightClick: (annId: string, anchorRect: DOMRect) => void
  className?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
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
  }, [content, annSignature, annotations])

  const handleClick = useCallback((e: React.MouseEvent) => {
    const mark = (e.target as HTMLElement).closest('mark.ann-hl') as HTMLElement | null
    if (mark?.dataset.annId) onHighlightClick(mark.dataset.annId, mark.getBoundingClientRect())
  }, [onHighlightClick])

  return (
    <div
      ref={ref}
      className={className}
      data-node-id={nodeId}
      onClick={handleClick}
    >
      <MarkdownRenderer content={content} />
    </div>
  )
}

export default function ResultView({ taskId, question, status, nodes, studioId, statusMessage }: Props) {
  const { t, locale } = useI18n()
  const navigate = useNavigate()
  const edges = useTaskStore(s => s.edges)
  const currentTask = useTaskStore(s => s.currentTask)
  const allSubTasks = useMemo(() => currentTask?.sub_tasks ?? [], [currentTask?.sub_tasks])
  const iterations = useMemo(() => currentTask?.iterations ?? [], [currentTask?.iterations])
  const currentIterationId = currentTask?.current_iteration_id
  const [activeResultIterationId, setActiveResultIterationId] = useState('')
  const resultTabs = useMemo<ResultIterationTab[]>(() => {
    const knownIterationIds = new Set(iterations.map(iteration => iteration.id))
    const makeTab = (iteration: TaskIteration, index: number): ResultIterationTab | null => {
      const iterNodes = nodes.filter(node => node.iteration_id === iteration.id)
      const root = [...iterNodes].reverse().find(node =>
        node.type === 'agent_zero' && node.status === 'completed' && Boolean((node.output || '').trim()),
      )
      const iterAgentNodes = iterNodes.filter(node =>
        node.type === 'sub_agent' && (node.status === 'completed' || Boolean((node.output || '').trim())),
      )
      const iterSubTasks = allSubTasks.filter(subTask => subTask.iteration_id === iteration.id)
      if (!root && !iterAgentNodes.some(node => Boolean((node.output || '').trim()))) return null
      return {
        id: iteration.id,
        iteration,
        label: t('resultView.iterationResultTab', { n: index + 1 }),
        title: iteration.title || iteration.instruction || t('resultView.iterationResultTab', { n: index + 1 }),
        rootNode: root,
        agentNodes: iterAgentNodes,
        subTasks: iterSubTasks,
      }
    }

    const tabs = iterations
      .map((iteration, index) => makeTab(iteration, index))
      .filter((tab): tab is ResultIterationTab => Boolean(tab))

    const legacyNodes = nodes.filter(node => !node.iteration_id || !knownIterationIds.has(node.iteration_id))
    const legacyRoot = [...legacyNodes].reverse().find(node =>
      node.type === 'agent_zero' && node.status === 'completed' && Boolean((node.output || '').trim()),
    )
    const legacyAgentNodes = legacyNodes.filter(node =>
      node.type === 'sub_agent' && (node.status === 'completed' || Boolean((node.output || '').trim())),
    )
    if ((legacyRoot || legacyAgentNodes.length > 0) && tabs.length === 0) {
      tabs.push({
        id: '__legacy__',
        iteration: null,
        label: t('resultView.legacyResultTab'),
        title: t('resultView.legacyResultTab'),
        rootNode: legacyRoot,
        agentNodes: legacyAgentNodes,
        subTasks: allSubTasks.filter(subTask => !subTask.iteration_id || !knownIterationIds.has(subTask.iteration_id)),
      })
    }
    return tabs
  }, [allSubTasks, iterations, nodes, t])

  useEffect(() => {
    if (resultTabs.length === 0) {
      if (activeResultIterationId) setActiveResultIterationId('')
      return
    }
    if (resultTabs.some(tab => tab.id === activeResultIterationId)) return
    const currentTab = currentIterationId ? resultTabs.find(tab => tab.id === currentIterationId) : undefined
    setActiveResultIterationId((currentTab ?? resultTabs[resultTabs.length - 1]).id)
  }, [activeResultIterationId, currentIterationId, resultTabs])

  const activeResultTab = resultTabs.find(tab => tab.id === activeResultIterationId) ?? resultTabs[resultTabs.length - 1]
  const rootNode = activeResultTab?.rootNode
  const synthesis = rootNode?.output || ''
  const agentNodes = useMemo(() => activeResultTab?.agentNodes ?? [], [activeResultTab])
  const subTasks = useMemo(() => activeResultTab?.subTasks ?? [], [activeResultTab])
  const graph = useMemo(() => buildGraphIndex(agentNodes, edges), [agentNodes, edges])
  const sections = useMemo(
    () => parseSynthesisSections(synthesis, t('resultView.defaultSectionTitle')),
    [synthesis, t],
  )

  const subTaskByNodeId = useMemo(() => {
    const map: Record<string, SubTask> = {}
    for (const subTask of subTasks) {
      if (subTask.group_id) map[subTask.group_id] = subTask
    }
    return map
  }, [subTasks])

  const totals = useMemo(() => {
    let tokens = 0
    let durationMs = 0
    let costUsd = 0
    let editedCount = 0
    for (const subTask of subTasks) {
      tokens += subTask.tokens || 0
      durationMs += subTask.duration_ms || 0
      costUsd += subTask.cost_usd || 0
      if (subTask.edited_by_user) editedCount += 1
    }
    return {
      tokens,
      durationMs,
      costUsd,
      editedCount,
      stepCount: subTasks.length,
    }
  }, [subTasks])

  const hasBlockedSteps = subTasks.some(subTask => subTask.status === 'blocked')
    || agentNodes.some(node => node.status === 'error')
  const hasBlockers = status === 'completed_with_blockers' || hasBlockedSteps
  const isTimeout = status === 'timeout_killed'
  const isFailed = status === 'failed'
  const isTerminated = status === 'terminated'
  const resultState = isFailed
    ? 'failed'
    : isTimeout
      ? 'timeout'
      : isTerminated
        ? 'terminated'
        : hasBlockers
          ? 'partial'
          : 'done'

  const createdSkillSlugs = useMemo(() => {
    const patterns = [
      /已生成并注册\s*skill=([a-zA-Z0-9_-]+)/g,
      /registered\s+skill=([a-zA-Z0-9_-]+)/gi,
    ]
    const set = new Set<string>()
    const scan = (text?: string) => {
      if (!text) return
      for (const re of patterns) {
        let match: RegExpExecArray | null
        while ((match = re.exec(text)) !== null) set.add(match[1])
      }
    }
    for (const node of [rootNode, ...agentNodes]) scan(node?.output)
    scan(synthesis)
    return Array.from(set)
  }, [agentNodes, rootNode, synthesis])

  const stepNotes = useMemo(() => (
    graph.orderedNodes.map(node => ({
      node,
      index: graph.displayIndex[node.id],
      preview: summarizeText(node.output, 78, t('resultView.emptyPreview')),
      subTask: subTaskByNodeId[node.id],
      upstreamIds: graph.upstreamOf[node.id] ?? [],
      downstreamIds: graph.downstreamOf[node.id] ?? [],
    }))
  ), [graph, subTaskByNodeId, t])

  const [panelOpen, setPanelOpen] = useState(false)
  const [pendingAnnotation, setPendingAnnotation] = useState<SelectionPayload | null>(null)
  const [activeAnnotation, setActiveAnnotation] = useState<{ annotation: Annotation; anchorRect: DOMRect } | null>(null)
  const [selectedPanelAnnotationId, setSelectedPanelAnnotationId] = useState<string | null>(null)
  const [pendingProcess, setPendingProcess] = useState<SelectionPayload | null>(null)
  const [annotations, setAnnotations] = useState<Annotation[]>([])
  const [notebookOpen, setNotebookOpen] = useState(true)
  const [expandedNodeId, setExpandedNodeId] = useState<string | null>(null)
  const [jumpTarget, setJumpTarget] = useState<{ annotationId: string; nodeId: string } | null>(null)
  const [chatOpen, setChatOpen] = useState(false)
  const [sandbox, setSandbox] = useState<Sandbox | null>(null)
  const [outputFiles, setOutputFiles] = useState<SandboxFile[]>([])
  const [selectedOutputPath, setSelectedOutputPath] = useState('')
  const [selectedOutputContent, setSelectedOutputContent] = useState('')
  const [outputLoading, setOutputLoading] = useState(false)
  const [editingOutputPath, setEditingOutputPath] = useState('')
  const [outputEditDraft, setOutputEditDraft] = useState('')
  const [outputSaving, setOutputSaving] = useState(false)
  const [outputEditError, setOutputEditError] = useState('')
  const mainScrollRef = useRef<HTMLDivElement>(null)
  const updateTaskStatus = useTaskStore(s => s.updateTaskStatus)
  const setExecuting = useTaskStore(s => s.setExecuting)

  const loadAnnotations = useCallback(async () => {
    try {
      const list = await api.listAnnotations(taskId)
      setAnnotations(list)
    } catch {
      /* ignore */
    }
  }, [taskId])

  useEffect(() => {
    loadAnnotations()
  }, [loadAnnotations])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const currentSandbox = await api.getTaskSandbox(taskId)
        if (cancelled) return
        setSandbox(currentSandbox)
        const collectOutputFiles = async (directory: string, depth: number): Promise<SandboxFile[]> => {
          if (depth > MAX_OUTPUT_SCAN_DEPTH) return []
          const files = await api.listSandboxFiles(currentSandbox.id, directory).catch(() => [])
          const directFiles = files.filter(file => file.kind === 'file')
          const nestedDirs = files.filter(file => file.kind === 'directory')
          if (!nestedDirs.length) return directFiles
          const nestedFiles = await Promise.all(
            nestedDirs.map(file => collectOutputFiles(file.path, depth + 1)),
          )
          return directFiles.concat(...nestedFiles)
        }
        const files = await collectOutputFiles('output', 0)
        if (cancelled) return
        const nextFiles = files
          .filter(file => file.kind === 'file')
          .sort((a, b) => {
            const aReadable = isReadableOutput(a.path) ? 0 : 1
            const bReadable = isReadableOutput(b.path) ? 0 : 1
            return aReadable - bReadable || a.name.localeCompare(b.name)
          })
          .slice(0, MAX_OUTPUT_FILES)
        setOutputFiles(nextFiles)
        setSelectedOutputPath(current => (
          current && nextFiles.some(file => file.path === current) ? current : ''
        ))
      } catch {
        if (!cancelled) {
          setSandbox(null)
          setOutputFiles([])
          setSelectedOutputPath('')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [taskId])

  useEffect(() => {
    let cancelled = false
    if (!sandbox || !selectedOutputPath || !isReadableOutput(selectedOutputPath)) {
      setSelectedOutputContent('')
      setOutputLoading(false)
      setEditingOutputPath('')
      setOutputEditDraft('')
      return
    }
    setOutputLoading(true)
    setEditingOutputPath('')
    setOutputEditDraft('')
    setOutputEditError('')
    api.readSandboxFile(sandbox.id, selectedOutputPath)
      .then(res => {
        if (!cancelled) setSelectedOutputContent(res.content)
      })
      .catch(() => {
        if (!cancelled) setSelectedOutputContent('')
      })
      .finally(() => {
        if (!cancelled) setOutputLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [sandbox, selectedOutputPath])

  const handleAnnotate = useCallback((payload: SelectionPayload) => {
    setPendingAnnotation(payload)
    setActiveAnnotation(null)
    setPanelOpen(false)
    setPendingProcess(null)
  }, [])

  const handleProcess = useCallback((payload: {
    selectedText: string
    nodeId: string
    anchorRect: DOMRect
  }) => {
    setPendingProcess(payload)
  }, [])

  const handleDeleteAnnotation = useCallback(async (annId: string) => {
    try {
      await api.deleteAnnotation(taskId, annId)
      setAnnotations(prev => prev.filter(annotation => annotation.id !== annId))
    } catch {
      /* ignore */
    }
  }, [taskId])

  const handleHighlightClick = useCallback((annId: string, anchorRect: DOMRect) => {
    const annotation = annotations.find(item => item.id === annId)
    if (!annotation) return
    setActiveAnnotation({ annotation, anchorRect })
    setPendingAnnotation(null)
    setPendingProcess(null)
    setPanelOpen(false)
  }, [annotations])

  const handleJumpToAnnotation = useCallback((annotation: Annotation) => {
    setSelectedPanelAnnotationId(annotation.id)
    setJumpTarget({ annotationId: annotation.id, nodeId: annotation.node_id })
    if (annotation.node_id.startsWith(OUTPUT_NODE_PREFIX)) {
      setSelectedOutputPath(annotation.node_id.slice(OUTPUT_NODE_PREFIX.length))
      return
    }
    if (annotation.node_id !== (rootNode?.id || '__synthesis__')) {
      setExpandedNodeId(annotation.node_id)
    }
  }, [rootNode?.id])

  const handleTogglePanelAnnotation = useCallback((annotation: Annotation) => {
    if (selectedPanelAnnotationId === annotation.id) {
      setSelectedPanelAnnotationId(null)
      return
    }
    handleJumpToAnnotation(annotation)
  }, [handleJumpToAnnotation, selectedPanelAnnotationId])

  const handleJumpToSection = useCallback((sectionId: string) => {
    const container = mainScrollRef.current
    const target = document.getElementById(sectionId)
    if (!target) return
    if (!container) {
      target.scrollIntoView({ behavior: 'smooth', block: 'start' })
      return
    }
    const containerRect = container.getBoundingClientRect()
    const targetRect = target.getBoundingClientRect()
    const top = targetRect.top - containerRect.top + container.scrollTop - 16
    container.scrollTo({ top: Math.max(0, top), behavior: 'smooth' })
  }, [])

  const handleToggleStep = useCallback((nodeId: string, anchorEl: HTMLElement | null) => {
    const scrollContainer = document.getElementById('result-content-area')
    if (!scrollContainer || !anchorEl) {
      setExpandedNodeId(current => current === nodeId ? null : nodeId)
      return
    }

    const previousTop = anchorEl.getBoundingClientRect().top
    const previousScrollTop = scrollContainer.scrollTop

    setExpandedNodeId(current => current === nodeId ? null : nodeId)

    requestAnimationFrame(() => {
      const nextTop = anchorEl.getBoundingClientRect().top
      scrollContainer.scrollTop = previousScrollTop + (nextTop - previousTop)
    })
  }, [])

  const handleToggleOutput = useCallback((path: string, anchorEl: HTMLElement | null) => {
    const scrollContainer = document.getElementById('result-content-area')
    if (!scrollContainer || !anchorEl) {
      setSelectedOutputPath(current => current === path ? '' : path)
      return
    }

    const previousTop = anchorEl.getBoundingClientRect().top
    const previousScrollTop = scrollContainer.scrollTop

    setSelectedOutputPath(current => current === path ? '' : path)

    requestAnimationFrame(() => {
      const nextTop = anchorEl.getBoundingClientRect().top
      scrollContainer.scrollTop = previousScrollTop + (nextTop - previousTop)
    })
  }, [])

  const startEditOutput = useCallback((path: string) => {
    setEditingOutputPath(path)
    setOutputEditDraft(path === selectedOutputPath ? selectedOutputContent : '')
    setOutputEditError('')
  }, [selectedOutputContent, selectedOutputPath])

  const cancelEditOutput = useCallback(() => {
    setEditingOutputPath('')
    setOutputEditDraft('')
    setOutputEditError('')
  }, [])

  const saveOutputEdit = useCallback(async () => {
    if (!sandbox || !editingOutputPath) return
    setOutputSaving(true)
    setOutputEditError('')
    try {
      const res = await api.writeSandboxFile(sandbox.id, editingOutputPath, outputEditDraft)
      setSelectedOutputContent(outputEditDraft)
      setOutputFiles(prev => prev.map(file => (
        file.path === editingOutputPath ? { ...file, size: res.size } : file
      )))
      setEditingOutputPath('')
      setOutputEditDraft('')
    } catch {
      setOutputEditError(t('resultView.saveFailed'))
    } finally {
      setOutputSaving(false)
    }
  }, [editingOutputPath, outputEditDraft, sandbox, t])

  const handleRetryStep = useCallback(async (nodeId: string) => {
    try {
      const res = await fetch(`/api/tasks/${taskId}/retry-step`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ node_id: nodeId, extra_context: '' }),
      })
      if (!res.ok || !res.body) throw new Error(t('common.requestFailed'))
      updateTaskStatus('executing')
      setExecuting(true)
      connectTaskStream(taskId)
    } catch {
      /* ignore */
    }
  }, [setExecuting, taskId, updateTaskStatus, t])

  useEffect(() => {
    if (!expandedNodeId) return
    if (stepNotes.some(item => item.node.id === expandedNodeId)) return
    setExpandedNodeId(null)
  }, [expandedNodeId, stepNotes])

  useEffect(() => {
    if (!selectedOutputPath) return
    if (outputFiles.some(file => file.path === selectedOutputPath)) return
    setSelectedOutputPath('')
  }, [outputFiles, selectedOutputPath])

  useEffect(() => {
    if (!jumpTarget) return

    let timer: number | null = null
    const raf = requestAnimationFrame(() => {
      const mark = document.querySelector(
        `#result-content-area mark.ann-hl[data-ann-id="${jumpTarget.annotationId}"]`,
      ) as HTMLElement | null

      if (!mark) return

      mark.scrollIntoView({ behavior: 'smooth', block: 'center' })
      mark.classList.add('ann-hl--focus')
      timer = window.setTimeout(() => {
        mark.classList.remove('ann-hl--focus')
      }, 1600)
    })

    return () => {
      cancelAnimationFrame(raf)
      if (timer) window.clearTimeout(timer)
    }
  }, [jumpTarget, expandedNodeId, selectedOutputPath, selectedOutputContent, annotations])

  const synthAnnotations = annotations.filter(a => a.node_id === (rootNode?.id || '__synthesis__'))

  return (
    <div className="result-layout">
      <div className="result-layout__main" id="result-content-area" ref={mainScrollRef}>
        <div className={`result-view ${panelOpen ? 'result-view--with-panel' : ''}`}>
          <div className="result-view__header card">
            <div className="result-view__title-row">
              {resultState === 'done' ? (
                <CheckCircle size={22} className="result-view__icon result-view__icon--ok" />
              ) : (
                <AlertTriangle size={22} className="result-view__icon result-view__icon--warn" />
              )}
              <div className="result-view__title-copy">
                <h2>
                  {resultState === 'failed'
                    ? t('resultView.titleFailed')
                    : resultState === 'timeout'
                      ? t('resultView.titleTimeout')
                      : resultState === 'terminated'
                        ? t('resultView.titleTerminated')
                        : resultState === 'partial'
                        ? t('resultView.titlePartial')
                        : t('resultView.titleDone')}
                </h2>
                <p className="result-view__question">{question}</p>
                <p className="result-view__summary">
                  {resultState === 'terminated'
                    ? t('resultView.summaryTerminated')
                    : resultState === 'partial'
                      ? t('resultView.summaryPartial')
                      : t('resultView.summaryLine')}
                </p>
              </div>
              <button
                type="button"
                className={`btn btn-icon result-view__ann-toggle ${panelOpen ? 'result-view__ann-toggle--active' : ''}`}
                onClick={() => {
                  setPanelOpen(v => !v)
                  setPendingAnnotation(null)
                  setActiveAnnotation(null)
                }}
                title={t('resultView.annotationsTitle')}
              >
                <MessageSquareText size={18} />
                {annotations.length > 0 && (
                  <span className="result-view__ann-badge">{annotations.length}</span>
                )}
              </button>
            </div>

            {totals.stepCount > 0 && (
              <div className="result-view__metrics" title={t('resultView.metricsTitle')}>
                <span className="result-view__metric">
                  <Clock size={14} />
                  <span className="result-view__metric-label">{t('resultView.totalDuration')}</span>
                  <span className="result-view__metric-value">{fmtDuration(totals.durationMs)}</span>
                </span>
                <span className="result-view__metric">
                  <Cpu size={14} />
                  <span className="result-view__metric-label">{t('resultView.totalTokens')}</span>
                  <span className="result-view__metric-value">{fmtTokens(totals.tokens)}</span>
                </span>
                <span className="result-view__metric result-view__metric--cost">
                  <CircleDollarSign size={14} />
                  <span className="result-view__metric-label">{t('resultView.estCost')}</span>
                  <span className="result-view__metric-value">{fmtCostCNY(totals.costUsd)}</span>
                </span>
                <span className="result-view__metric result-view__metric--count">
                  <span className="result-view__metric-label">{t('resultView.stepCount')}</span>
                  <span className="result-view__metric-value">{totals.stepCount}</span>
                  {totals.editedCount > 0 && (
                    <span className="result-view__metric-edited">
                      {t('resultView.editedSteps', { count: totals.editedCount })}
                    </span>
                  )}
                </span>
              </div>
            )}
          </div>

          {(isFailed || isTerminated) && statusMessage && (
            <div className="result-view__warning card">
              <AlertTriangle size={16} />
              <span>{translateStatusMessage(locale, statusMessage)}</span>
            </div>
          )}

          {(hasBlockers || isTimeout || isTerminated) && (
            <div className="result-view__warning card">
              <AlertTriangle size={16} />
              <span>
                {isTimeout
                  ? t('resultView.warnTimeout')
                  : isTerminated
                    ? t('resultView.warnTerminated')
                    : t('resultView.warnBlockers')}
              </span>
            </div>
          )}

          {createdSkillSlugs.length > 0 && (
            <div className="result-view__created-skills card">
              <div className="result-view__created-skills-head">
                <Sparkles size={16} />
                <h3>{t('resultView.newSkillsTitle', { count: createdSkillSlugs.length })}</h3>
              </div>
              <ul className="result-view__created-skills-list">
                {createdSkillSlugs.map(slug => (
                  <li key={slug}>
                    <code>{slug}</code>
                    <button
                      type="button"
                      className="btn btn-link"
                      onClick={() => navigate(`/skills?highlight=${encodeURIComponent(slug)}`)}
                    >
                      {t('resultView.goSkillPool')} <ExternalLink size={12} />
                    </button>
                  </li>
                ))}
              </ul>
              <p className="result-view__created-skills-hint">
                {t('resultView.newSkillsHint')}
              </p>
            </div>
          )}

          <section className="result-notebook card">
            <div className="result-notebook__head">
              <div>
                <span className="result-notebook__eyebrow">{t('resultView.notebookEyebrow')}</span>
                <h3>{t('resultView.notebookTitle')}</h3>
                <p>{t('resultView.notebookDesc')}</p>
              </div>
              <div className="result-notebook__head-actions">
                <FileText size={18} />
                <button
                  type="button"
                  className="btn btn-icon"
                  onClick={() => setNotebookOpen(open => !open)}
                  title={notebookOpen ? t('resultView.collapseNotebook') : t('resultView.expandNotebook')}
                  aria-expanded={notebookOpen}
                >
                  {notebookOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </button>
              </div>
            </div>

            {resultTabs.length > 1 && (
              <div className="result-notebook__iteration-tabs" role="tablist" aria-label={t('resultView.iterationTabsAria')}>
                {resultTabs.map(tab => (
                  <button
                    key={tab.id}
                    type="button"
                    role="tab"
                    aria-selected={tab.id === activeResultIterationId}
                    className={`result-notebook__iteration-tab ${tab.id === activeResultIterationId ? 'result-notebook__iteration-tab--active' : ''}`}
                    onClick={() => {
                      setActiveResultIterationId(tab.id)
                      setExpandedNodeId(null)
                    }}
                    title={tab.title}
                  >
                    <span>{tab.label}</span>
                    {tab.title && tab.title !== tab.label && <small>{tab.title}</small>}
                  </button>
                ))}
              </div>
            )}

            {notebookOpen && sections.length > 1 && (
              <nav className="result-notebook__toc" aria-label={t('resultView.notebookTocAria')}>
                {sections.map((section, index) => (
                  <button
                    key={section.id}
                    type="button"
                    className="result-notebook__toc-link"
                    onClick={() => handleJumpToSection(section.id)}
                  >
                    <span>{String(index + 1).padStart(2, '0')}</span>
                    {section.title}
                  </button>
                ))}
              </nav>
            )}

            {notebookOpen && (sections.length > 0 ? (
              <div className="result-notebook__pages">
                {sections.map((section, index) => (
                  <section key={section.id} id={section.id} className="result-note-page">
                    <div className="result-note-page__index">{String(index + 1).padStart(2, '0')}</div>
                    <div className="result-note-page__body">
                      <h4>{section.title}</h4>
                      <HighlightedMarkdown
                        content={section.body}
                        nodeId={rootNode?.id || '__synthesis__'}
                        annotations={synthAnnotations}
                        onHighlightClick={handleHighlightClick}
                      />
                    </div>
                  </section>
                ))}
              </div>
            ) : (
              <div className="result-note-page result-note-page--empty">
                <div className="result-note-page__index">01</div>
                <div className="result-note-page__body">
                  <h4>{t('resultView.noSynthesisTitle')}</h4>
                  <p>{t('resultView.noSynthesisDesc')}</p>
                </div>
              </div>
            ))}
          </section>

          {outputFiles.length > 0 && (
            <section className="result-step-notes result-output-directory card">
              <div className="result-step-notes__head result-output-directory__head">
                <div>
                  <h3>{t('resultView.outputSectionTitle')}</h3>
                  <p>{t('resultView.outputSectionDesc')}</p>
                </div>
                {sandbox && (
                  <button type="button" className="btn btn-secondary" onClick={() => navigate(`/sandboxes/${sandbox.id}`)}>
                    <ExternalLink size={13} />
                    {t('resultView.openSandbox')}
                  </button>
                )}
              </div>
              <div className="result-step-notes__list">
                {outputFiles.map((file, index) => {
                  const expanded = file.path === selectedOutputPath
                  const outputId = outputNodeId(file.path)
                  const previewUrl = sandbox
                    ? `/api/sandboxes/${sandbox.id}/preview/${file.path.split('/').map(encodeURIComponent).join('/')}`
                    : ''
                  const outputAnnotations = annotations.filter(annotation => annotation.node_id === outputId)
                  const Icon = isImageFile(file.path) ? ImageIcon : isCsvFile(file.path) ? FileSpreadsheet : FileText

                  return (
                    <article
                      key={file.path}
                      id={`output-${outputId}`}
                      className={`result-step-note result-output-note ${expanded ? 'result-step-note--expanded' : ''}`}
                    >
                      <button
                        type="button"
                        className="result-step-note__trigger"
                        onClick={(e) => handleToggleOutput(file.path, e.currentTarget)}
                      >
                        <span className="result-step-note__index result-output-note__icon">
                          <Icon size={15} />
                        </span>
                        <span className="result-step-note__body">
                          <strong>{file.name}</strong>
                          <span>
                            {String(index + 1).padStart(2, '0')} · {file.path} · {formatBytes(file.size)}
                          </span>
                        </span>
                        {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                      </button>

                      {expanded && (
                        <div className="result-step-note__panel result-output-note__panel">
                          <div className="result-step-note__meta">
                            <span><FileText size={13} /> {file.path}</span>
                            <span>{formatBytes(file.size)}</span>
                            {isReadableOutput(file.path) && editingOutputPath !== file.path && (
                              <button
                                type="button"
                                className="btn btn-sm btn-secondary"
                                onClick={() => startEditOutput(file.path)}
                                disabled={outputLoading}
                              >
                                <Pencil size={12} />
                                {t('resultView.edit')}
                              </button>
                            )}
                            {editingOutputPath === file.path && (
                              <>
                                <button
                                  type="button"
                                  className="btn btn-sm btn-primary"
                                  onClick={saveOutputEdit}
                                  disabled={outputSaving}
                                >
                                  <Save size={12} />
                                  {outputSaving ? t('resultView.saving') : t('common.save')}
                                </button>
                                <button
                                  type="button"
                                  className="btn btn-sm btn-secondary"
                                  onClick={cancelEditOutput}
                                  disabled={outputSaving}
                                >
                                  <X size={12} />
                                  {t('common.cancel')}
                                </button>
                              </>
                            )}
                            {previewUrl && (
                              <a
                                className="btn btn-sm btn-secondary result-output-preview__open"
                                href={previewUrl}
                                target="_blank"
                                rel="noreferrer"
                              >
                                <ExternalLink size={12} />
                                {t('resultView.openFile')}
                              </a>
                            )}
                          </div>

                          <div className="result-step-note__appendix result-output-note__appendix">
                            <div className="result-step-note__appendix-head">
                              <CornerDownRight size={14} />
                              <span>{t('resultView.outputContent')}</span>
                            </div>
                            {outputLoading ? (
                              <div className="result-output-preview__empty">{t('resultView.readingFile')}</div>
                            ) : editingOutputPath === file.path ? (
                              <div className="result-output-editor">
                                <textarea
                                  className="input-base result-output-editor__textarea"
                                  value={outputEditDraft}
                                  onChange={(e) => setOutputEditDraft(e.target.value)}
                                  spellCheck={false}
                                />
                                {outputEditError && (
                                  <p className="result-output-editor__error">{outputEditError}</p>
                                )}
                              </div>
                            ) : isImageFile(file.path) && previewUrl ? (
                              <div className="result-output-preview__media">
                                <img src={previewUrl} alt={file.name} />
                              </div>
                            ) : isPdfFile(file.path) && previewUrl ? (
                              <iframe
                                className="result-output-preview__pdf"
                                src={previewUrl}
                                title={file.name}
                              />
                            ) : isHtmlFile(file.path) && previewUrl ? (
                              <iframe
                                className="result-output-preview__pdf"
                                src={previewUrl}
                                title={file.name}
                              />
                            ) : isReadableOutput(file.path) ? (
                              selectedOutputContent ? (
                                <HighlightedMarkdown
                                  content={formatOutputContent(file.path, selectedOutputContent)}
                                  nodeId={outputId}
                                  annotations={outputAnnotations}
                                  onHighlightClick={handleHighlightClick}
                                  className="result-output-preview__markdown"
                                />
                              ) : (
                                <div className="result-output-preview__empty">{t('resultView.fileEmpty')}</div>
                              )
                            ) : (
                              <div className="result-output-preview__empty">
                                {t('resultView.previewUnsupported')}
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </article>
                  )
                })}
              </div>
            </section>
          )}

          {stepNotes.length > 0 && (
            <section className="result-step-notes card">
              <div className="result-step-notes__head">
                <div>
                  <h3>{t('resultView.stepsSectionTitle')}</h3>
                  <p>{t('resultView.stepsSectionDesc')}</p>
                </div>
              </div>
              <div className="result-step-notes__list">
                {stepNotes.map(item => (
                  <article
                    key={item.node.id}
                    id={`deliverable-${item.node.id}`}
                    className={`result-step-note ${expandedNodeId === item.node.id ? 'result-step-note--expanded' : ''}`}
                  >
                    <button
                      type="button"
                      className="result-step-note__trigger"
                      onClick={(e) => handleToggleStep(item.node.id, e.currentTarget)}
                    >
                      <span className="result-step-note__index">#{item.index}</span>
                      <span className="result-step-note__body">
                        <strong>{item.node.step_label}</strong>
                        <span>
                          {item.node.agent_role}
                          {item.subTask?.duration_ms ? ` · ${fmtDuration(item.subTask.duration_ms)}` : ''}
                          {item.preview ? ` · ${item.preview}` : ''}
                        </span>
                      </span>
                      {expandedNodeId === item.node.id ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                    </button>

                    {expandedNodeId === item.node.id && (
                      <div className="result-step-note__panel">
                        <div className="result-step-note__meta">
                          <span className={`status-badge status--${item.node.status}`}>
                            {item.node.status === 'completed'
                              ? t('resultView.completed')
                              : item.node.status}
                          </span>
                          <button
                            type="button"
                            className="btn btn-sm btn-secondary"
                            onClick={() => handleRetryStep(item.node.id)}
                          >
                            <RotateCcw size={13} />
                            {t('resultView.rerunStep')}
                          </button>
                          {item.subTask?.duration_ms ? (
                            <span><Clock size={13} /> {fmtDuration(item.subTask.duration_ms)}</span>
                          ) : null}
                          {item.subTask?.tokens ? (
                            <span><Cpu size={13} /> {fmtTokens(item.subTask.tokens)}</span>
                          ) : null}
                          {item.subTask?.cost_usd ? (
                            <span><CircleDollarSign size={13} /> {fmtCostCNY(item.subTask.cost_usd)}</span>
                          ) : null}
                        </div>

                        {(item.upstreamIds.length > 0 || item.downstreamIds.length > 0) && (
                          <div className="result-step-note__links">
                            {item.upstreamIds.length > 0 && (
                              <div className="result-step-note__link-group">
                                <span>{t('resultView.upstreamInputs')}</span>
                                <div className="result-step-note__chips">
                                  {item.upstreamIds.map(nodeId => {
                                    const node = graph.orderedNodes.find(entry => entry.id === nodeId)
                                    return (
                                      <button
                                        key={nodeId}
                                        type="button"
                                        onClick={() => setExpandedNodeId(nodeId)}
                                      >
                                        #{graph.displayIndex[nodeId]} {node?.step_label || t('common.upstreamStep')}
                                      </button>
                                    )
                                  })}
                                </div>
                              </div>
                            )}

                            {item.downstreamIds.length > 0 && (
                              <div className="result-step-note__link-group">
                                <span>{t('resultView.downstreamRefs')}</span>
                                <div className="result-step-note__chips">
                                  {item.downstreamIds.map(nodeId => {
                                    const node = graph.orderedNodes.find(entry => entry.id === nodeId)
                                    return (
                                      <button
                                        key={nodeId}
                                        type="button"
                                        onClick={() => setExpandedNodeId(nodeId)}
                                      >
                                        #{graph.displayIndex[nodeId]} {node?.step_label || t('common.downstreamStep')}
                                      </button>
                                    )
                                  })}
                                </div>
                              </div>
                            )}
                          </div>
                        )}

                        <div className="result-step-note__appendix">
                          <div className="result-step-note__appendix-head">
                            <CornerDownRight size={14} />
                            <span>{t('resultView.stepMaterials')}</span>
                          </div>
                          {item.node.output ? (
                            <HighlightedMarkdown
                              content={item.node.output}
                              nodeId={item.node.id}
                              annotations={annotations.filter(annotation => annotation.node_id === item.node.id)}
                              onHighlightClick={handleHighlightClick}
                              className="result-step-note__content"
                            />
                          ) : (
                            <p className="result-step-note__empty">{t('resultView.stepNoOutput')}</p>
                          )}
                        </div>
                      </div>
                    )}
                  </article>
                ))}
              </div>
            </section>
          )}

          <div className="result-view__actions">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setChatOpen(true)}
              title={t('resultView.resultChatTitle')}
            >
              <MessageSquareText size={14} />
              {t('resultView.resultChat')}
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => navigate('/tasks')}>
              <ArrowLeft size={14} />
              {t('resultView.backToBoard')}
            </button>
            {studioId && (
              <button type="button" className="btn btn-secondary" onClick={() => navigate(`/studios/${studioId}`)}>
                <ExternalLink size={14} />
                {t('resultView.viewStudio')}
              </button>
            )}
          </div>
        </div>

          <SelectionToolbar
            containerSelector="#result-content-area"
            onAnnotate={handleAnnotate}
            onProcess={handleProcess}
          />
        {pendingAnnotation && (
          <AnnotationComposerPopover
            taskId={taskId}
            pending={pendingAnnotation}
            onClose={() => setPendingAnnotation(null)}
            onCreated={loadAnnotations}
          />
        )}
        {activeAnnotation && (
          <AnnotationPopover
            annotation={activeAnnotation.annotation}
            anchorRect={activeAnnotation.anchorRect}
            onClose={() => setActiveAnnotation(null)}
            onDelete={handleDeleteAnnotation}
          />
        )}
        {pendingProcess && (
          <ProcessSelectionPopover
            taskId={taskId}
            pending={pendingProcess}
            onClose={() => setPendingProcess(null)}
          />
        )}
        <ResultChat
          taskId={taskId}
          open={chatOpen}
          onOpenChange={setChatOpen}
          hideLauncher
        />
      </div>

      <div className={`result-layout__panel ${panelOpen ? 'result-layout__panel--open' : ''}`}>
        {panelOpen && (
          <div className="result-annotations-popout">
            <div className="result-annotations-popout__head">
              <span>{t('resultView.panelAnnotations')}</span>
              <button type="button" className="btn btn-icon" onClick={() => setPanelOpen(false)}>×</button>
            </div>
            <div className="result-annotations-popout__list">
              {annotations.length === 0 ? (
                <p>{t('resultView.panelHint')}</p>
              ) : annotations.map(annotation => (
                <div
                  key={annotation.id}
                  className={`result-annotations-popout__item ${
                    selectedPanelAnnotationId === annotation.id ? 'result-annotations-popout__item--active' : ''
                  }`}
                >
                  <button
                    type="button"
                    className="result-annotations-popout__item-trigger"
                    onClick={() => handleTogglePanelAnnotation(annotation)}
                    aria-expanded={selectedPanelAnnotationId === annotation.id}
                  >
                    <strong>{annotation.question}</strong>
                    <span>{annotation.selected_text.length > 72 ? `${annotation.selected_text.slice(0, 72)}…` : annotation.selected_text}</span>
                  </button>
                  {selectedPanelAnnotationId === annotation.id && (
                    <div className="result-annotations-popout__inline-detail">
                      <div className="result-annotations-popout__quote">
                        {annotation.selected_text}
                      </div>
                      <div className="result-annotations-popout__qa">
                        <MarkdownRenderer content={annotation.answer || t('resultView.answerPending')} />
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
