import { existsSync, mkdtempSync, readdirSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawn, spawnSync } from 'node:child_process'
import net from 'node:net'

const rootDir = process.cwd()
const releaseDir = join(rootDir, 'release')
const productName = 'AStudio.app'

function fail(message) {
  console.error(message)
  process.exit(1)
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: rootDir,
    encoding: 'utf8',
    ...options,
  })
  const output = `${result.stdout || ''}${result.stderr || ''}`
  if (result.status !== 0) {
    console.error(output)
    fail(`Command failed: ${command} ${args.join(' ')}`)
  }
  return output
}

function findFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.unref()
    server.on('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      server.close(() => resolve(address.port))
    })
  })
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function waitForHealth(url, timeoutMs = 60000) {
  const startedAt = Date.now()
  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1500) })
      if (response.ok) {
        const payload = await response.json()
        if (payload?.status === 'ok') return payload
      }
    } catch {
      // Backend is still starting.
    }
    await sleep(500)
  }
  throw new Error(`Timed out waiting for ${url}`)
}

function waitForExit(child, timeoutMs = 5000) {
  return new Promise((resolve) => {
    if (child.exitCode !== null || child.signalCode !== null) {
      resolve()
      return
    }
    const timer = setTimeout(resolve, timeoutMs)
    child.once('exit', () => {
      clearTimeout(timer)
      resolve()
    })
  })
}

async function stopChild(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return
  child.kill('SIGTERM')
  await waitForExit(child)
  if (child.exitCode === null && child.signalCode === null) {
    child.kill('SIGKILL')
    await waitForExit(child)
  }
}

function findApps() {
  if (!existsSync(releaseDir)) return []
  return readdirSync(releaseDir)
    .map((name) => join(releaseDir, name, productName))
    .filter((path) => existsSync(path) && statSync(path).isDirectory())
}

async function verifyApp(appPath) {
  const sidecarPath = join(appPath, 'Contents', 'Resources', 'server-bin', 'astudio-server')
  const webIndexPath = join(appPath, 'Contents', 'Resources', 'web', 'dist', 'index.html')
  const serverSourcePath = join(appPath, 'Contents', 'Resources', 'server')

  if (!existsSync(sidecarPath)) fail(`Missing bundled backend sidecar: ${sidecarPath}`)
  if (!existsSync(webIndexPath)) fail(`Missing bundled frontend: ${webIndexPath}`)
  if (existsSync(serverSourcePath)) fail(`Unexpected server source directory in release app: ${serverSourcePath}`)

  run('codesign', ['--verify', '--deep', '--strict', '--verbose=2', appPath])
  run('codesign', ['--verify', '--strict', '--verbose=2', sidecarPath])

  const signatureInfo = run('codesign', ['--display', '--verbose=4', appPath])
  if (!signatureInfo.includes('Authority=Developer ID Application:')) {
    fail(`Release app is not signed with a Developer ID Application certificate:\n${signatureInfo}`)
  }
  if (signatureInfo.includes('Authority=(unavailable)')) {
    fail(`Release app signature authority is unavailable:\n${signatureInfo}`)
  }
  const expectedTeamId = (process.env.APPLE_TEAM_ID || '').trim()
  const teamId = signatureInfo.match(/^TeamIdentifier=(.+)$/m)?.[1]?.trim()
  if (expectedTeamId && teamId !== expectedTeamId) {
    fail(`Release app was signed by team ${teamId || 'unknown'}, expected ${expectedTeamId}.`)
  }

  run('spctl', ['--assess', '--type', 'execute', '--verbose=4', appPath])
  await verifySidecarHealth(sidecarPath, webIndexPath)
  console.log(`Verified macOS release app: ${appPath}`)
}

async function verifySidecarHealth(sidecarPath, webIndexPath) {
  const port = await findFreePort()
  const dataDir = mkdtempSync(join(tmpdir(), 'astudio-release-verify-'))
  const webDistDir = join(webIndexPath, '..')
  let output = ''
  let child = null

  try {
    child = spawn(sidecarPath, [], {
      env: {
        ...process.env,
        ASTUDIO_DATA_DIR: dataDir,
        ASTUDIO_WEB_DIST_DIR: webDistDir,
        ASTUDIO_SERVER_PORT: String(port),
        ASTUDIO_TASK_EXECUTION: 'inline',
        PYTHONUNBUFFERED: '1',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    child.stdout.on('data', (chunk) => { output += chunk.toString() })
    child.stderr.on('data', (chunk) => { output += chunk.toString() })
    const health = await waitForHealth(`http://127.0.0.1:${port}/api/health`)
    if (health.status !== 'ok') {
      fail(`Unexpected sidecar health payload: ${JSON.stringify(health)}`)
    }
  } catch (error) {
    console.error(output)
    throw error
  } finally {
    await stopChild(child)
  }
}

if (process.platform !== 'darwin') {
  fail('macOS release verification must run on macOS.')
}

const apps = findApps()
if (!apps.length) {
  fail('No macOS .app bundles were found under release/.')
}

for (const appPath of apps) {
  await verifyApp(appPath)
}
