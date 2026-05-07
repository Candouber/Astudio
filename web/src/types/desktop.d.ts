export type DesktopUpdateStatus =
  | 'idle'
  | 'checking'
  | 'available'
  | 'not-available'
  | 'downloading'
  | 'downloaded'
  | 'error'
  | 'unsupported'

export interface DesktopUpdateInfo {
  version: string
  releaseName: string
  releaseDate: string
}

export interface DesktopDownloadProgress {
  percent: number
  transferred: number
  total: number
}

export interface DesktopUpdateState {
  status: DesktopUpdateStatus
  updateInfo: DesktopUpdateInfo | null
  error: string
  downloaded: boolean
  checkingAt: string
  downloadProgress: DesktopDownloadProgress | null
}

export interface DesktopAppInfo {
  name: string
  version: string
  isPackaged: boolean
  platform: string
  repoUrl: string
  homepageUrl: string
}

export interface DesktopBridge {
  getAppInfo: () => Promise<DesktopAppInfo>
  openExternal: (url: string) => Promise<{ ok: boolean; error?: string }>
  getUpdateState: () => Promise<DesktopUpdateState>
  checkForUpdates: () => Promise<DesktopUpdateState>
  downloadUpdate: () => Promise<DesktopUpdateState>
  installUpdate: () => Promise<{ ok: boolean; error?: string }>
  onUpdateState: (callback: (state: DesktopUpdateState) => void) => () => void
}

declare global {
  interface Window {
    astudioDesktop?: DesktopBridge
  }
}

export {}
