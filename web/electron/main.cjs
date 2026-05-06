const { app, BrowserWindow, dialog } = require('electron')
const { spawn } = require('node:child_process')
const crypto = require('node:crypto')
const fs = require('node:fs')
const http = require('node:http')
const net = require('node:net')
const path = require('node:path')

const SERVER_HOST = '127.0.0.1'
const EXPLICIT_SERVER_PORT = process.env.ASTUDIO_SERVER_PORT || process.env.ANTIT_SERVER_PORT
let SERVER_PORT = Number(EXPLICIT_SERVER_PORT || 8000)
let SERVER_URL = `http://${SERVER_HOST}:${SERVER_PORT}`
let HEALTH_URL = `${SERVER_URL}/api/health`
const RENDERER_URL = process.env.ELECTRON_RENDERER_URL || process.env.VITE_DEV_SERVER_URL || ''
const APP_ICON_PATH = resolveAppIconPath()
const USER_DATA_DIR = app.getPath('userData')
const DATA_DIR = process.env.ASTUDIO_DATA_DIR || path.join(USER_DATA_DIR, 'data')

app.setAppUserModelId(app.getName())

if (!app.requestSingleInstanceLock()) {
  app.quit()
  process.exit(0)
}

let win = null
let backend = null
let startedBackend = false
let backendStartError = null
let browserBridge = null
let browserBridgeUrl = ''
const browserBridgeToken = crypto.randomUUID()
let browserBridgeQueue = Promise.resolve()

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function setServerPort(port) {
  SERVER_PORT = port
  SERVER_URL = `http://${SERVER_HOST}:${SERVER_PORT}`
  HEALTH_URL = `${SERVER_URL}/api/health`
}

function findFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.unref()
    server.on('error', reject)
    server.listen(0, SERVER_HOST, () => {
      const address = server.address()
      server.close(() => resolve(address.port))
    })
  })
}

function readRequestBody(req, maxBytes = 64 * 1024) {
  return new Promise((resolve, reject) => {
    let body = ''
    req.setEncoding('utf8')
    req.on('data', (chunk) => {
      body += chunk
      if (body.length > maxBytes) {
        reject(new Error('Request body is too large'))
        req.destroy()
      }
    })
    req.on('end', () => resolve(body))
    req.on('error', reject)
  })
}

function sendJson(res, statusCode, payload) {
  const data = JSON.stringify(payload)
  res.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(data),
  })
  res.end(data)
}

function withTimeout(promise, timeoutMs, label) {
  let timer = null
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} timed out after ${timeoutMs}ms`)), timeoutMs)
  })
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer))
}

async function runBrowserExtraction({ query, maxResults }) {
  const n = Math.min(Math.max(Number(maxResults || 5), 1), 10)
  const url = `https://www.bing.com/search?q=${encodeURIComponent(query)}&cc=US&setlang=en&ensearch=1`
  const hidden = new BrowserWindow({
    show: false,
    width: 1280,
    height: 900,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      images: false,
    },
  })

  try {
    hidden.webContents.setUserAgent(
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36',
    )
    await withTimeout(hidden.loadURL(url), 60_000, 'Electron browser load')
    const payload = await withTimeout(
      hidden.webContents.executeJavaScript(`
        (() => {
          const norm = (text) => String(text || '').replace(/\\s+/g, ' ').trim()
          const decodeBingUrl = (href) => {
            try {
              const url = new URL(href)
              if (!url.hostname.includes('bing.com') || !url.pathname.startsWith('/ck/')) return href
              const raw = url.searchParams.get('u') || ''
              if (!raw.startsWith('a1')) return href
              let encoded = raw.slice(2)
              encoded += '='.repeat((4 - encoded.length % 4) % 4)
              return decodeURIComponent(escape(atob(encoded.replace(/-/g, '+').replace(/_/g, '/'))))
            } catch (_) {
              return href
            }
          }
          const results = []
          const seen = new Set()
          document.querySelectorAll('li.b_algo').forEach((item) => {
            const anchor = item.querySelector('h2 a')
            if (!anchor) return
            const title = norm(anchor.textContent)
            const href = decodeBingUrl(anchor.href || '')
            const snippet = norm((item.querySelector('p') || {}).textContent)
            if (!title || !href || seen.has(href)) return
            seen.add(href)
            results.push({ title, url: href, content: snippet })
          })
          const links = []
          document.querySelectorAll('a[href]').forEach((anchor) => {
            const href = anchor.href || ''
            const text = norm(anchor.textContent)
            if (!href.startsWith('http') || !text || seen.has(href)) return
            seen.add(href)
            links.push({ title: text.slice(0, 140), url: href })
          })
          return {
            title: norm(document.title),
            url: location.href,
            results,
            links,
            text: norm(document.body ? document.body.innerText : '').slice(0, 8000),
          }
        })()
      `),
      10_000,
      'Electron browser extraction',
    )
    return { ok: true, query, requested_url: url, max_results: n, ...payload }
  } finally {
    if (!hidden.isDestroyed()) hidden.destroy()
  }
}

function enqueueBrowserExtraction(payload) {
  const job = browserBridgeQueue.then(() => runBrowserExtraction(payload))
  browserBridgeQueue = job.catch(() => {})
  return job
}

async function startBrowserBridge() {
  if (browserBridgeUrl) return browserBridgeUrl
  const port = await findFreePort()
  browserBridgeUrl = `http://${SERVER_HOST}:${port}`
  browserBridge = http.createServer(async (req, res) => {
    if (req.method === 'GET' && req.url === '/health') {
      sendJson(res, 200, { status: 'ok' })
      return
    }
    if (req.method !== 'POST' || req.url !== '/browser/search') {
      sendJson(res, 404, { ok: false, error: 'Not found' })
      return
    }
    const auth = req.headers.authorization || ''
    if (auth !== `Bearer ${browserBridgeToken}`) {
      sendJson(res, 401, { ok: false, error: 'Unauthorized' })
      return
    }
    try {
      const body = await readRequestBody(req)
      const payload = JSON.parse(body || '{}')
      const query = String(payload.query || '').trim()
      if (!query) {
        sendJson(res, 400, { ok: false, error: 'Missing query' })
        return
      }
      const result = await enqueueBrowserExtraction({
        query,
        maxResults: payload.max_results,
      })
      sendJson(res, 200, result)
    } catch (error) {
      sendJson(res, 500, {
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      })
    }
  })
  await new Promise((resolve, reject) => {
    browserBridge.once('error', reject)
    browserBridge.listen(port, SERVER_HOST, () => {
      browserBridge.off('error', reject)
      resolve()
    })
  })
  return browserBridgeUrl
}

function resolveAppIconPath() {
  const candidates = [
    path.resolve(__dirname, '../../build/icon.png'),
    path.resolve(__dirname, '../public/astudio-icon.png'),
    path.join(process.resourcesPath || '', 'build/icon.png'),
    path.join(process.resourcesPath || '', 'icon.png'),
  ]
  return candidates.find((candidate) => fs.existsSync(candidate)) || ''
}

function resolveWebDistDir() {
  const candidates = [
    process.env.ASTUDIO_WEB_DIST_DIR,
    path.resolve(__dirname, '../../web/dist'),
    path.join(process.resourcesPath || '', 'web/dist'),
  ].filter(Boolean)
  return candidates.find((candidate) => fs.existsSync(path.join(candidate, 'index.html'))) || ''
}

async function isHealthy() {
  try {
    const response = await fetch(HEALTH_URL, { signal: AbortSignal.timeout(1000) })
    if (!response.ok) return false
    const payload = await response.json()
    return payload && payload.status === 'ok'
  } catch {
    return false
  }
}

async function hasFrontend() {
  try {
    const response = await fetch(SERVER_URL, { signal: AbortSignal.timeout(1000) })
    if (!response.ok) return false
    const html = await response.text()
    return html.includes('<div id="root"')
  } catch {
    return false
  }
}

async function waitForHealth(timeoutMs = 30000) {
  const startedAt = Date.now()
  while (Date.now() - startedAt < timeoutMs) {
    if (await isHealthy()) return true
    await sleep(500)
  }
  return false
}

async function waitForUrl(url, timeoutMs = 30000) {
  const startedAt = Date.now()
  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1000) })
      if (response.ok) return true
    } catch {
      // Server not ready yet.
    }
    await sleep(500)
  }
  return false
}

function resolveServerDir() {
  const candidates = [
    path.resolve(__dirname, '../../server'),
    path.join(process.resourcesPath || '', 'server'),
  ]
  return candidates.find((candidate) => fs.existsSync(path.join(candidate, 'main.py')))
}

function resolveSidecarPath() {
  const binaryName = process.platform === 'win32' ? 'astudio-server.exe' : 'astudio-server'
  const candidates = [
    process.env.ASTUDIO_SERVER_BINARY,
    path.join(process.resourcesPath || '', 'server-bin', binaryName),
  ].filter(Boolean)
  return candidates.find((candidate) => fs.existsSync(candidate) && fs.statSync(candidate).isFile()) || ''
}

function resolveBackendCommand() {
  const sidecarPath = resolveSidecarPath()
  if (sidecarPath) {
    return {
      mode: 'sidecar',
      command: sidecarPath,
      args: [],
      cwd: path.dirname(sidecarPath),
    }
  }

  const serverDir = resolveServerDir()
  if (!serverDir) return null
  return {
    mode: 'uv',
    command: 'uv',
    args: ['run', 'uvicorn', 'main:app', '--host', SERVER_HOST, '--port', String(SERVER_PORT)],
    cwd: serverDir,
  }
}

function openLogStream() {
  const logDir = path.join(USER_DATA_DIR, 'logs')
  fs.mkdirSync(logDir, { recursive: true })
  return fs.createWriteStream(path.join(logDir, 'backend.log'), { flags: 'a' })
}

function startBackend() {
  const backendCommand = resolveBackendCommand()
  if (!backendCommand) {
    throw new Error(
      'Cannot find a bundled backend sidecar or server/main.py. The desktop app cannot start the local backend.',
    )
  }

  const logStream = openLogStream()
  logStream.write(
    `\n[${new Date().toISOString()}] starting backend mode=${backendCommand.mode} on ${SERVER_URL}\n`,
  )

  backend = spawn(
    backendCommand.command,
    backendCommand.args,
    {
      cwd: backendCommand.cwd,
      env: {
        ...process.env,
        ASTUDIO_DESKTOP: '1',
        ANTIT_DESKTOP: '1',
        ASTUDIO_SERVER_HOST: SERVER_HOST,
        ASTUDIO_SERVER_PORT: String(SERVER_PORT),
        ASTUDIO_USER_DATA_DIR: USER_DATA_DIR,
        ASTUDIO_DATA_DIR: DATA_DIR,
        ASTUDIO_WEB_DIST_DIR: resolveWebDistDir(),
        ASTUDIO_ELECTRON_BROWSER_BRIDGE_URL: browserBridgeUrl,
        ASTUDIO_ELECTRON_BROWSER_BRIDGE_TOKEN: browserBridgeToken,
        ASTUDIO_TASK_EXECUTION: process.env.ASTUDIO_TASK_EXECUTION
          || (backendCommand.mode === 'sidecar' ? 'inline' : 'process'),
        PYTHONUNBUFFERED: '1',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    },
  )

  startedBackend = true
  backend.stdout.pipe(logStream)
  backend.stderr.pipe(logStream)
  backend.once('error', (error) => {
    backendStartError = error
    logStream.write(`[${new Date().toISOString()}] backend spawn error: ${error.message}\n`)
  })
  backend.once('exit', (code, signal) => {
    logStream.write(`[${new Date().toISOString()}] backend exited code=${code} signal=${signal}\n`)
    logStream.end()
    backend = null
  })
}

async function ensureBackend() {
  if (await isHealthy()) {
    if (RENDERER_URL || (await hasFrontend())) return
    if (!EXPLICIT_SERVER_PORT && !RENDERER_URL) {
      setServerPort(await findFreePort())
    } else {
      throw new Error(
        `${SERVER_URL} already has an AStudio backend running, but it is not serving the frontend. Stop the old backend and start Electron again.`,
      )
    }
  } else if (!EXPLICIT_SERVER_PORT && !RENDERER_URL) {
    setServerPort(await findFreePort())
  }

  if (await isHealthy()) {
    if (RENDERER_URL || (await hasFrontend())) return
    throw new Error(
      `${SERVER_URL} already has an AStudio backend running, but it is not serving the frontend. Stop the old backend and start Electron again.`,
    )
  }
  await startBrowserBridge()
  startBackend()
  if (!(await waitForHealth())) {
    if (backendStartError) {
      throw new Error(
        `Backend process failed to start: ${backendStartError.message}. Developer preview packages still require uv when no backend sidecar is bundled.`,
      )
    }
    throw new Error(`Backend startup timed out. Check logs: ${path.join(USER_DATA_DIR, 'logs', 'backend.log')}`)
  }
  if (!RENDERER_URL && !(await hasFrontend())) {
    throw new Error('Backend started, but the built frontend was not found. Run pnpm build:web before packaging.')
  }
}

async function createWindow() {
  await ensureBackend()

  if (RENDERER_URL) {
    await waitForUrl(RENDERER_URL)
  }

  win = new BrowserWindow({
    title: 'AStudio',
    width: 1280,
    height: 860,
    minWidth: 1040,
    minHeight: 720,
    ...(APP_ICON_PATH ? { icon: APP_ICON_PATH } : {}),
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    vibrancy: process.platform === 'darwin' ? 'under-window' : undefined,
    visualEffectState: 'active',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  })

  await win.loadURL(RENDERER_URL || SERVER_URL)

  if (RENDERER_URL) {
    win.webContents.openDevTools({ mode: 'right' })
  }
}

function stopBackend() {
  if (!startedBackend || !backend) return
  backend.kill(process.platform === 'win32' ? undefined : 'SIGTERM')
}

function stopBrowserBridge() {
  if (!browserBridge) return
  browserBridge.close()
  browserBridge = null
  browserBridgeUrl = ''
}

app.whenReady().then(() => {
  if (process.platform === 'darwin' && APP_ICON_PATH && app.dock) {
    app.dock.setIcon(APP_ICON_PATH)
  }
  return createWindow()
}).catch((error) => {
  dialog.showErrorBox('AStudio failed to start', error instanceof Error ? error.message : String(error))
  app.quit()
})

app.on('before-quit', () => {
  stopBackend()
  stopBrowserBridge()
})

app.on('window-all-closed', () => {
  win = null
  if (process.platform !== 'darwin') app.quit()
})

app.on('second-instance', () => {
  if (win) {
    if (win.isMinimized()) win.restore()
    win.focus()
  }
})

app.on('activate', () => {
  const allWindows = BrowserWindow.getAllWindows()
  if (allWindows.length) {
    allWindows[0].focus()
  } else {
    createWindow().catch((error) => {
      dialog.showErrorBox('AStudio failed to start', error instanceof Error ? error.message : String(error))
    })
  }
})
