import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  ExternalLink,
  GitBranch,
  Globe,
  Info,
  Power,
  RefreshCw,
} from 'lucide-react'
import { useI18n } from '../i18n/useI18n'
import type { DesktopAppInfo, DesktopUpdateState, DesktopUpdateStatus } from '../types/desktop'
import './About.css'

const DEFAULT_UPDATE_STATE: DesktopUpdateState = {
  status: 'idle',
  updateInfo: null,
  error: '',
  downloaded: false,
  checkingAt: '',
  downloadProgress: null,
}

function statusTone(status: DesktopUpdateStatus): 'neutral' | 'ok' | 'warn' | 'error' {
  if (status === 'available' || status === 'downloaded') return 'warn'
  if (status === 'not-available') return 'ok'
  if (status === 'error' || status === 'unsupported') return 'error'
  return 'neutral'
}

export default function About() {
  const { t } = useI18n()
  const desktop = window.astudioDesktop
  const [appInfo, setAppInfo] = useState<DesktopAppInfo | null>(null)
  const [updateState, setUpdateState] = useState<DesktopUpdateState>(DEFAULT_UPDATE_STATE)
  const [busy, setBusy] = useState<'check' | 'download' | 'install' | ''>('')

  useEffect(() => {
    let cancelled = false
    if (!desktop) {
      setUpdateState({
        ...DEFAULT_UPDATE_STATE,
        status: 'unsupported',
        error: t('aboutPage.unsupported'),
      })
      return undefined
    }

    desktop.getAppInfo().then((info) => {
      if (!cancelled) setAppInfo(info)
    }).catch(() => {})
    desktop.getUpdateState().then((state) => {
      if (!cancelled) setUpdateState(state)
    }).catch(() => {})
    const unsubscribe = desktop.onUpdateState((state) => {
      if (!cancelled) setUpdateState(state)
    })
    return () => {
      cancelled = true
      unsubscribe()
    }
  }, [desktop, t])

  const versionText = appInfo?.version || '0.1.0'
  const repoUrl = appInfo?.repoUrl || 'https://github.com/Candouber/Astudio'
  const homepageUrl = appInfo?.homepageUrl || ''
  const updateMessage = useMemo(() => {
    switch (updateState.status) {
      case 'checking':
        return t('aboutPage.checking')
      case 'available':
        return t('aboutPage.updateAvailable', {
          version: updateState.updateInfo?.version || t('common.none'),
        })
      case 'not-available':
        return t('aboutPage.upToDate')
      case 'downloading':
        return t('aboutPage.downloading')
      case 'downloaded':
        return t('aboutPage.updateDownloaded')
      case 'error':
        return t('aboutPage.error', { message: updateState.error || t('common.requestFailed') })
      case 'unsupported':
        return t('aboutPage.unsupported')
      default:
        return t('aboutPage.idle')
    }
  }, [t, updateState])
  const progress = Math.max(0, Math.min(100, updateState.downloadProgress?.percent || 0))

  async function openExternal(url: string) {
    if (!url) return
    if (desktop) {
      await desktop.openExternal(url)
      return
    }
    window.open(url, '_blank', 'noopener,noreferrer')
  }

  async function checkForUpdates() {
    if (!desktop) return
    setBusy('check')
    try {
      setUpdateState(await desktop.checkForUpdates())
    } finally {
      setBusy('')
    }
  }

  async function downloadUpdate() {
    if (!desktop) return
    setBusy('download')
    try {
      setUpdateState(await desktop.downloadUpdate())
    } finally {
      setBusy('')
    }
  }

  async function installUpdate() {
    if (!desktop) return
    setBusy('install')
    try {
      await desktop.installUpdate()
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="about-page">
      <header className="about-page__header">
        <div className="about-page__title-row">
          <span className="about-page__title-icon"><Info size={20} /></span>
          <div>
            <h1>{t('aboutPage.title')}</h1>
            <p>{t('aboutPage.subtitle')}</p>
          </div>
        </div>
      </header>

      <section className="about-section">
        <div>
          <h2>{t('aboutPage.appInfo')}</h2>
          <p>{t('aboutPage.appInfoDesc')}</p>
        </div>
        <dl className="about-info-grid">
          <div>
            <dt>{t('aboutPage.version')}</dt>
            <dd>{versionText}</dd>
          </div>
          <div>
            <dt>{t('aboutPage.platform')}</dt>
            <dd>{appInfo?.platform || navigator.platform || t('common.none')}</dd>
          </div>
          <div>
            <dt>{t('aboutPage.mode')}</dt>
            <dd>{appInfo?.isPackaged ? t('aboutPage.packaged') : t('aboutPage.development')}</dd>
          </div>
        </dl>
      </section>

      <section className="about-section">
        <div>
          <h2>{t('aboutPage.links')}</h2>
          <p>{t('aboutPage.linksDesc')}</p>
        </div>
        <div className="about-link-list">
          <button type="button" className="about-link" onClick={() => openExternal(repoUrl)}>
            <GitBranch size={20} />
            <span>
              <strong>{t('aboutPage.repository')}</strong>
              <small>{repoUrl}</small>
            </span>
            <ExternalLink size={16} />
          </button>
          <button
            type="button"
            className="about-link"
            onClick={() => openExternal(homepageUrl)}
            disabled={!homepageUrl}
          >
            <Globe size={20} />
            <span>
              <strong>{t('aboutPage.officialSite')}</strong>
              <small>{homepageUrl || t('aboutPage.comingSoon')}</small>
            </span>
            <ExternalLink size={16} />
          </button>
        </div>
      </section>

      <section className="about-section">
        <div>
          <h2>{t('aboutPage.updates')}</h2>
          <p>{t('aboutPage.updateHint')}</p>
        </div>
        <div className={`about-update about-update--${statusTone(updateState.status)}`}>
          <div className="about-update__status">
            {statusTone(updateState.status) === 'ok' ? <CheckCircle2 size={20} /> : <AlertTriangle size={20} />}
            <div>
              <strong>{updateMessage}</strong>
              {updateState.updateInfo?.releaseDate ? (
                <small>{t('aboutPage.releaseDate', { date: updateState.updateInfo.releaseDate })}</small>
              ) : null}
            </div>
          </div>

          {updateState.status === 'downloading' ? (
            <div className="about-update__progress" aria-label={t('aboutPage.downloading')}>
              <span style={{ width: `${progress}%` }} />
              <strong>{t('aboutPage.progress', { percent: progress.toFixed(0) })}</strong>
            </div>
          ) : null}

          <div className="about-update__actions">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={checkForUpdates}
              disabled={!desktop || busy !== '' || updateState.status === 'checking'}
            >
              <RefreshCw size={16} className={updateState.status === 'checking' ? 'about-spin' : ''} />
              {busy === 'check' ? t('aboutPage.checking') : t('aboutPage.check')}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={downloadUpdate}
              disabled={!desktop || busy !== '' || updateState.status !== 'available'}
            >
              <Download size={16} />
              {busy === 'download' ? t('aboutPage.downloading') : t('aboutPage.download')}
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={installUpdate}
              disabled={!desktop || busy !== '' || updateState.status !== 'downloaded'}
            >
              <Power size={16} />
              {t('aboutPage.install')}
            </button>
          </div>
          <p className="about-update__note">{t('aboutPage.versionNote')}</p>
        </div>
      </section>
    </div>
  )
}
