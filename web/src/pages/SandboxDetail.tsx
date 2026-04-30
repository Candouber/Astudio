import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, Eye, FileText, Folder, Loader, Maximize2, Play, RefreshCw, Save, Square, Terminal, X } from 'lucide-react'
import { api } from '../api/client'
import type { Sandbox, SandboxFile, SandboxRun, Task } from '../types'
import { useI18n } from '../i18n/useI18n'
import './SandboxDetail.css'

function compactText(value: string | null | undefined, limit: number) {
  const text = (value || '').replace(/\s+/g, ' ').trim()
  if (text.length <= limit) return text
  return `${text.slice(0, limit).trim()}…`
}

export default function SandboxDetail() {
  const { t } = useI18n()
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [sandbox, setSandbox] = useState<Sandbox | null>(null)
  const [task, setTask] = useState<Task | null>(null)
  const [files, setFiles] = useState<SandboxFile[]>([])
  const [runs, setRuns] = useState<SandboxRun[]>([])
  const [directory, setDirectory] = useState('.')
  const [selectedPath, setSelectedPath] = useState('README.md')
  const [content, setContent] = useState('')
  const [logs, setLogs] = useState<{ stdout: string; stderr: string } | null>(null)
  const [command, setCommand] = useState('')
  const [commandSource, setCommandSource] = useState('')
  const [cwd, setCwd] = useState('.')
  const [background, setBackground] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [previewExpanded, setPreviewExpanded] = useState(false)

  const previewUrl = sandbox?.preview_url || ''
  const parentDirectory = useMemo(() => {
    if (directory === '.') return ''
    const parts = directory.split('/').filter(Boolean)
    parts.pop()
    return parts.length ? parts.join('/') : '.'
  }, [directory])

  const load = useCallback(async () => {
    if (!id) return
    setLoading(true)
    try {
      const sb = await api.getSandbox(id)
      setSandbox(sb)
      api.getTask(sb.task_id).then(setTask).catch(() => setTask(null))
      const [nextFiles, nextRuns] = await Promise.all([
        api.listSandboxFiles(id, directory),
        api.listSandboxRuns(id),
      ])
      setFiles(nextFiles)
      setRuns(nextRuns)
      const startCommand = await api.getSandboxStartCommand(id).catch(() => null)
      if (startCommand?.command) {
        const detectedCommand = startCommand.command
        const detectedCwd = startCommand.cwd ?? '.'
        setCommand(current => current.trim() ? current : detectedCommand)
        setCwd(current => current.trim() && current !== '.' ? current : detectedCwd)
        setCommandSource(startCommand.source || '')
      } else {
        setCommandSource(startCommand?.message || t('sandboxDetail.unknownStartCommand'))
      }
    } finally {
      setLoading(false)
    }
  }, [id, directory, t])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (!id || !selectedPath) return
    api.readSandboxFile(id, selectedPath)
      .then(res => setContent(res.content))
      .catch(() => setContent(''))
  }, [id, selectedPath])

  useEffect(() => {
    if (!previewExpanded) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setPreviewExpanded(false)
    }
    document.body.classList.add('sandbox-preview-open')
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      document.body.classList.remove('sandbox-preview-open')
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [previewExpanded])

  const openFile = async (file: SandboxFile) => {
    if (file.kind === 'directory') {
      setDirectory(file.path)
      return
    }
    setSelectedPath(file.path)
    setLogs(null)
  }

  const saveFile = async () => {
    if (!id || !selectedPath) return
    setBusy(true)
    try {
      await api.writeSandboxFile(id, selectedPath, content)
      await load()
    } finally {
      setBusy(false)
    }
  }

  const runCommand = async () => {
    if (!id || !command.trim()) return
    setBusy(true)
    try {
      const run = await api.runSandboxCommand(id, {
        command,
        cwd,
        background,
        timeout_seconds: 120,
      })
      await load()
      if (run?.id) {
        const nextLogs = await api.getSandboxRunLogs(id, run.id)
        setLogs(nextLogs)
      }
    } finally {
      setBusy(false)
    }
  }

  const stop = async () => {
    if (!id) return
    setBusy(true)
    try {
      await api.stopSandbox(id)
      await load()
    } finally {
      setBusy(false)
    }
  }

  const startPreview = async () => {
    if (!id) return
    setBusy(true)
    try {
      const res = await api.startSandboxPreview(id)
      setSandbox(prev => prev ? { ...prev, preview_url: res.preview_url } : prev)
    } finally {
      setBusy(false)
    }
  }

  const loadRunLogs = async (run: SandboxRun) => {
    if (!id) return
    setSelectedPath('')
    setLogs(await api.getSandboxRunLogs(id, run.id))
  }

  if (loading && !sandbox) {
    return (
      <div className="sandbox-detail__loading">
        <Loader size={22} className="animate-pulse" />
        <span>{t('sandboxDetail.loading')}</span>
      </div>
    )
  }

  if (!sandbox) return null
  const displayTitle = compactText(task?.question || sandbox.description || sandbox.path, 120)

  return (
    <div className="sandbox-detail">
      <div className="sandbox-detail__topbar">
        <button className="btn btn-icon" onClick={() => navigate('/sandboxes')}>
          <ArrowLeft size={18} />
        </button>
        <div className="sandbox-detail__title">
          <span>{t('sandboxDetail.titlePrefix')} / {sandbox.id}</span>
          <strong title={task?.question || sandbox.description || sandbox.path}>{displayTitle}</strong>
          {sandbox.dev_port && <small>{t('sandboxDetail.reservedPort')}{sandbox.dev_port}</small>}
        </div>
        <div className="sandbox-detail__actions">
          <button type="button" className="btn btn-secondary" onClick={() => navigate(`/tasks/${sandbox.task_id}`)}>{t('sandboxDetail.openTask')}</button>
          <button type="button" className="btn btn-secondary" onClick={load}><RefreshCw size={14} /> {t('common.refresh')}</button>
          <button type="button" className="btn btn-secondary" onClick={startPreview} disabled={busy}><Eye size={14} /> {t('sandboxDetail.previewPage')}</button>
        </div>
      </div>

      <div className="sandbox-detail__grid">
        <aside className="sandbox-detail__files">
          <div className="sandbox-panel__header">
            <span>{t('common.files')}</span>
            <small>{directory}</small>
          </div>
          {parentDirectory && (
            <button className="sandbox-file" onClick={() => setDirectory(parentDirectory)}>
              <Folder size={15} /> ..
            </button>
          )}
          {files.map(file => (
            <button
              key={file.path}
              className={`sandbox-file ${selectedPath === file.path ? 'sandbox-file--active' : ''}`}
              onClick={() => openFile(file)}
            >
              {file.kind === 'directory' ? <Folder size={15} /> : <FileText size={15} />}
              <span>{file.name}</span>
            </button>
          ))}
        </aside>

        <section className="sandbox-detail__main">
          <div className="sandbox-panel__header">
            <span>{logs ? t('sandboxDetail.runLogs') : selectedPath || t('sandboxDetail.noFileSelected')}</span>
            {selectedPath && !logs && (
              <button type="button" className="btn btn-secondary" onClick={saveFile} disabled={busy}>
                {busy ? <Loader size={14} className="animate-pulse" /> : <Save size={14} />} {t('common.save')}
              </button>
            )}
          </div>
          {logs ? (
            <div className="sandbox-logs">
              <h4>{t('common.stdout')}</h4>
              <pre>{logs.stdout || t('common.noOutput')}</pre>
              <h4>{t('common.stderr')}</h4>
              <pre>{logs.stderr || t('common.noOutput')}</pre>
            </div>
          ) : (
            <textarea
              className="sandbox-editor"
              value={content}
              onChange={e => setContent(e.target.value)}
              spellCheck={false}
            />
          )}
        </section>

        <aside className="sandbox-detail__side">
          <div className="sandbox-runner">
            <div className="sandbox-panel__header">
              <span><Terminal size={15} /> {t('sandboxDock.run')}</span>
            </div>
            <label>
              {t('sandboxDetail.command')}
              <input value={command} onChange={e => setCommand(e.target.value)} />
            </label>
            {commandSource && (
              <p className="sandbox-runner__source">{t('sandboxDetail.commandSource')}{commandSource}</p>
            )}
            <label>
              {t('sandboxDetail.cwd')}
              <input value={cwd} onChange={e => setCwd(e.target.value)} />
            </label>
            <label className="sandbox-runner__check">
              <input type="checkbox" checked={background} onChange={e => setBackground(e.target.checked)} />
              {t('sandboxDetail.backgroundRun')}
            </label>
            <div className="sandbox-runner__buttons">
              <button type="button" className="btn btn-primary" onClick={runCommand} disabled={busy}>
                {busy ? <Loader size={14} className="animate-pulse" /> : <Play size={14} />} {t('sandboxDock.run')}
              </button>
              <button type="button" className="btn btn-secondary" onClick={stop} disabled={busy}>
                <Square size={13} /> {t('common.stop')}
              </button>
            </div>
          </div>

          <div className="sandbox-runs">
            <div className="sandbox-panel__header">
              <span>{t('sandboxDetail.runsTitle')}</span>
            </div>
            {runs.length === 0 ? (
              <p>{t('sandboxDetail.noRuns')}</p>
            ) : runs.map(run => (
              <button key={run.id} className="sandbox-run" onClick={() => loadRunLogs(run)}>
                <span>{run.command}</span>
                <small>{run.status} {run.exit_code !== null && run.exit_code !== undefined ? `(${run.exit_code})` : ''}</small>
              </button>
            ))}
          </div>

          <div className="sandbox-preview">
            <div className="sandbox-panel__header">
              <span>{t('sandboxDetail.pagePreview')}</span>
              {previewUrl && (
                <button type="button" className="btn btn-icon" onClick={() => setPreviewExpanded(true)} title={t('sandboxDetail.fullscreenPreview')}>
                  <Maximize2 size={14} />
                </button>
              )}
            </div>
            {previewUrl ? (
              <iframe src={previewUrl} title="sandbox preview" />
            ) : (
              <p>{t('sandboxDetail.previewHint')}</p>
            )}
          </div>
        </aside>
      </div>

      {previewExpanded && previewUrl && (
        <div className="sandbox-preview-full">
          <div className="sandbox-preview-full__bar">
            <span>{t('sandboxDetail.pagePreview')}</span>
            <button type="button" className="btn btn-secondary" onClick={() => setPreviewExpanded(false)}>
              <X size={14} /> {t('sandboxDetail.exitFullscreen')}
            </button>
          </div>
          <iframe src={previewUrl} title="sandbox preview full page" />
        </div>
      )}
    </div>
  )
}
