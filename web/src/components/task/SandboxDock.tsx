import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Box,
  ExternalLink,
  FileCode2,
  Folder,
  Loader,
  Maximize2,
  Play,
  RefreshCw,
  RotateCcw,
  Square,
  Wand2,
  X,
} from 'lucide-react'
import { api } from '../../api/client'
import { connectTaskStream } from '../../api/sse'
import { useI18n } from '../../i18n/useI18n'
import { useTaskStore } from '../../stores/taskStore'
import type { Sandbox, SandboxFile, SandboxRun } from '../../types'
import './SandboxDock.css'

interface Props {
  taskId: string
  onClose?: () => void
}

function compactPath(path: string, max = 34) {
  if (path.length <= max) return path
  return `…${path.slice(path.length - max)}`
}

export default function SandboxDock({ taskId, onClose }: Props) {
  const { t } = useI18n()
  const navigate = useNavigate()

  function statusLabel(status?: string) {
    if (status === 'running') return t('sandboxDock.statusRunning')
    if (status === 'error') return t('sandboxDock.statusError')
    if (status === 'stopped') return t('sandboxDock.statusStopped')
    return t('sandboxDock.statusReady')
  }
  const selectedNodeId = useTaskStore(s => s.selectedNodeId)
  const nodes = useTaskStore(s => s.nodes)
  const updateTaskStatus = useTaskStore(s => s.updateTaskStatus)
  const setExecuting = useTaskStore(s => s.setExecuting)
  const setStatus = useTaskStore(s => s.setStatus)
  const clearPlan = useTaskStore(s => s.clearPlan)
  const clearClarification = useTaskStore(s => s.clearClarification)
  const selectedNode = useMemo(() => nodes.find(n => n.id === selectedNodeId), [nodes, selectedNodeId])

  const [sandbox, setSandbox] = useState<Sandbox | null>(null)
  const [files, setFiles] = useState<SandboxFile[]>([])
  const [runs, setRuns] = useState<SandboxRun[]>([])
  const [command, setCommand] = useState('')
  const [commandSource, setCommandSource] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [iterateText, setIterateText] = useState('')
  const [iterating, setIterating] = useState(false)

  const loadSandbox = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const current = await api.getTaskSandbox(taskId)
      setSandbox(current)
      const [rootFiles, recentRuns, startCommand] = await Promise.all([
        api.listSandboxFiles(current.id, '.').catch(() => []),
        api.listSandboxRuns(current.id).catch(() => []),
        api.getSandboxStartCommand(current.id).catch(() => null),
      ])
      setFiles(rootFiles)
      setRuns(recentRuns)
      setCommand(startCommand?.command || '')
      setCommandSource(startCommand?.source || '')
    } catch {
      setSandbox(null)
      setFiles([])
      setRuns([])
      setCommand('')
      setCommandSource('')
    } finally {
      setLoading(false)
    }
  }, [taskId])

  useEffect(() => {
    loadSandbox()
  }, [loadSandbox])

  const ensureSandbox = async () => {
    setBusy(true)
    setError('')
    try {
      const res = await api.ensureTaskSandbox(taskId)
      setSandbox(res.sandbox)
      await loadSandbox()
    } catch {
      setError(t('sandboxDock.errCreate'))
    } finally {
      setBusy(false)
    }
  }

  const runCommand = async () => {
    if (!sandbox || !command.trim()) return
    setBusy(true)
    setError('')
    try {
      await api.runSandboxCommand(sandbox.id, {
        command: command.trim(),
        cwd: '.',
        background: true,
        timeout_seconds: 120,
      })
      await loadSandbox()
    } catch {
      setError(t('sandboxDock.errCommand'))
    } finally {
      setBusy(false)
    }
  }

  const stopSandbox = async () => {
    if (!sandbox) return
    setBusy(true)
    setError('')
    try {
      await api.stopSandbox(sandbox.id)
      await loadSandbox()
    } catch {
      setError(t('sandboxDock.errStop'))
    } finally {
      setBusy(false)
    }
  }

  const startPreview = async () => {
    if (!sandbox) return
    setBusy(true)
    setError('')
    try {
      const res = await api.startSandboxPreview(sandbox.id)
      setSandbox({ ...sandbox, preview_url: res.preview_url })
    } catch {
      setError(t('sandboxDock.errNoPreview'))
    } finally {
      setBusy(false)
    }
  }

  const iterateFromSandbox = async () => {
    if (!sandbox || !iterateText.trim() || iterating) return
    setIterating(true)
    setError('')
    const fileList = files.slice(0, 8).map(file => `${file.kind === 'directory' ? 'dir' : 'file'}:${file.path}`).join(', ')
    const recentRun = runs[0]
    const selectedContext = selectedNode
      ? t('sandboxDock.iterPromptSuffix', {
          role: selectedNode.agent_role,
          step: selectedNode.step_label,
          status: selectedNode.status,
        })
      : ''
    const instruction = [
      iterateText.trim(),
      '',
      t('sandboxDock.iterInstruction'),
      `${t('sandboxDock.sandboxId')}${sandbox.id}`,
      `${t('sandboxDock.sandboxPath')}${sandbox.path}`,
      `${t('sandboxDock.previewUrl')}${sandbox.preview_url || t('sandboxDock.previewNone')}`,
      `${t('sandboxDock.rootFiles')}${fileList || t('sandboxDock.filesNone')}`,
      recentRun
        ? `${t('sandboxDock.recentCmd')}${recentRun.command} (${recentRun.status})`
        : t('sandboxDock.recentCmdNone'),
      selectedContext,
    ].join('\n')

    try {
      const res = await fetch(`/api/tasks/${taskId}/iterate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instruction, messages: [] }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data?.detail || data?.message || t('common.requestFailed'))
      clearPlan()
      clearClarification()
      updateTaskStatus('planning')
      setStatus(t('sandboxDock.iterStatus'))
      setExecuting(true)
      connectTaskStream(taskId)
      setIterateText('')
    } catch {
      setError(t('sandboxDock.errIterate'))
    } finally {
      setIterating(false)
    }
  }

  return (
    <aside className="sandbox-dock">
      <div className="sandbox-dock__head">
        <div className="sandbox-dock__title">
          <Box size={15} />
          <span>{t('sandboxDock.title')}</span>
        </div>
        <div className="sandbox-dock__head-actions">
          <button className="sandbox-dock__icon-btn" onClick={loadSandbox} aria-label={t('sandboxDock.refreshAria')} disabled={loading}>
            <RefreshCw size={14} />
          </button>
          {onClose && (
            <button className="sandbox-dock__icon-btn" onClick={onClose} aria-label={t('sandboxDock.closeAria')}>
              <X size={14} />
            </button>
          )}
        </div>
      </div>

      {loading ? (
        <div className="sandbox-dock__empty">
          <Loader size={16} className="animate-spin" />
          <span>{t('sandboxDock.reading')}</span>
        </div>
      ) : !sandbox ? (
        <div className="sandbox-dock__empty">
          <Box size={22} />
          <span>{t('sandboxDock.noSandbox')}</span>
          <button className="btn btn-primary btn-sm" onClick={ensureSandbox} disabled={busy}>
            {busy ? <Loader size={13} className="animate-spin" /> : <Wand2 size={13} />}
            {t('sandboxDock.createSandbox')}
          </button>
        </div>
      ) : (
        <>
          <div className="sandbox-dock__status-row">
            <span className={`sandbox-dock__dot sandbox-dock__dot--${sandbox.status}`} />
            <span>{statusLabel(sandbox.status)}</span>
            {sandbox.dev_port && <code>{sandbox.dev_port}</code>}
          </div>

          <div className="sandbox-dock__section">
            <div className="sandbox-dock__section-head">
              <span>{t('sandboxDock.preview')}</span>
              {sandbox.preview_url && (
                <button
                  className="sandbox-dock__icon-btn"
                  onClick={() => window.open(sandbox.preview_url || '', '_blank')}
                  aria-label={t('sandboxDock.openPreviewAria')}
                >
                  <ExternalLink size={13} />
                </button>
              )}
            </div>
            {sandbox.preview_url ? (
              <button className="sandbox-dock__preview" onClick={() => navigate(`/sandboxes/${sandbox.id}`)}>
                <Maximize2 size={14} />
                <span>{compactPath(sandbox.preview_url, 42)}</span>
              </button>
            ) : (
              <button className="sandbox-dock__soft-action" onClick={startPreview} disabled={busy}>
                {t('sandboxDock.genPreview')}
              </button>
            )}
          </div>

          <div className="sandbox-dock__section">
            <div className="sandbox-dock__section-head">
              <span>{t('sandboxDock.files')}</span>
              <button className="sandbox-dock__link-btn" onClick={() => navigate(`/sandboxes/${sandbox.id}`)}>
                {t('sandboxDock.open')}
              </button>
            </div>
            <div className="sandbox-dock__files">
              {files.slice(0, 8).map(file => (
                <button
                  key={file.path}
                  className="sandbox-dock__file"
                  onClick={() => navigate(`/sandboxes/${sandbox.id}`)}
                  title={file.path}
                >
                  {file.kind === 'directory' ? <Folder size={13} /> : <FileCode2 size={13} />}
                  <span>{compactPath(file.path)}</span>
                </button>
              ))}
              {files.length === 0 && <span className="sandbox-dock__muted">{t('sandboxDock.noFiles')}</span>}
            </div>
          </div>

          <div className="sandbox-dock__section">
            <div className="sandbox-dock__section-head">
              <span>{t('sandboxDock.command')}</span>
              {commandSource && <small>{commandSource}</small>}
            </div>
            <input
              className="sandbox-dock__command"
              value={command}
              onChange={e => setCommand(e.target.value)}
              placeholder="npm run dev"
              disabled={busy}
            />
            <div className="sandbox-dock__actions">
              <button className="btn btn-primary btn-sm" onClick={runCommand} disabled={busy || !command.trim()}>
                {busy ? <Loader size={13} className="animate-spin" /> : <Play size={13} />}
                {t('sandboxDock.run')}
              </button>
              <button className="btn btn-secondary btn-sm" onClick={stopSandbox} disabled={busy}>
                <Square size={12} />
                {t('sandboxDock.stop')}
              </button>
            </div>
          </div>

          <div className="sandbox-dock__section">
            <div className="sandbox-dock__section-head">
              <span>{t('sandboxDock.recentRuns')}</span>
            </div>
            <div className="sandbox-dock__runs">
              {runs.slice(0, 4).map(run => (
                <button key={run.id} className="sandbox-dock__run" onClick={() => navigate(`/sandboxes/${sandbox.id}`)}>
                  <span>{compactPath(run.command, 38)}</span>
                  <small>{run.status}</small>
                </button>
              ))}
              {runs.length === 0 && <span className="sandbox-dock__muted">{t('sandboxDock.noRuns')}</span>}
            </div>
          </div>

          <div className="sandbox-dock__iterate">
            <div className="sandbox-dock__section-head">
              <span>{t('sandboxDock.iterateTitle')}</span>
              {selectedNode && <small>{t('sandboxDock.selectedNode')}</small>}
            </div>
            <textarea
              value={iterateText}
              onChange={e => setIterateText(e.target.value)}
              placeholder={t('sandboxDock.iteratePlaceholder')}
              disabled={iterating}
              rows={3}
            />
            <button className="btn btn-primary btn-sm" onClick={iterateFromSandbox} disabled={iterating || !iterateText.trim()}>
              {iterating ? <Loader size={13} className="animate-spin" /> : <RotateCcw size={13} />}
              {t('sandboxDock.iterateCta')}
            </button>
          </div>
        </>
      )}

      {error && <div className="sandbox-dock__error">{error}</div>}
    </aside>
  )
}
