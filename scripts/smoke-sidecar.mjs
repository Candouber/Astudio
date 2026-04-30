import { existsSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawn, spawnSync } from 'node:child_process'
import net from 'node:net'

const binaryName = process.platform === 'win32' ? 'astudio-server.exe' : 'astudio-server'
const sidecarPath = join(process.cwd(), 'build', 'server-bin', binaryName)

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

async function waitForHealth(url, timeoutMs = 45000) {
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

  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid', String(child.pid), '/t', '/f'], { stdio: 'ignore' })
    await waitForExit(child)
    return
  }

  child.kill('SIGTERM')
  await waitForExit(child)
  if (child.exitCode === null && child.signalCode === null) {
    child.kill('SIGKILL')
    await waitForExit(child)
  }
}

async function main() {
  if (!existsSync(sidecarPath)) {
    throw new Error(`Sidecar binary does not exist: ${sidecarPath}`)
  }

  const port = await findFreePort()
  const dataDir = mkdtempSync(join(tmpdir(), 'astudio-sidecar-'))
  const webDistDir = join(process.cwd(), 'web', 'dist')
  let output = ''
  let child = null
  const hardTimeout = setTimeout(() => {
    console.error(output)
    console.error('Sidecar smoke test exceeded the hard timeout.')
    if (child && child.pid) {
      if (process.platform === 'win32') {
        spawnSync('taskkill', ['/pid', String(child.pid), '/t', '/f'], { stdio: 'ignore' })
      } else {
        child.kill('SIGKILL')
      }
    }
    process.exit(1)
  }, 180000)

  console.log(`Starting sidecar smoke test on http://127.0.0.1:${port}/api/health`)
  child = spawn(sidecarPath, [], {
    env: {
      ...process.env,
      ASTUDIO_DATA_DIR: dataDir,
      ASTUDIO_WEB_DIST_DIR: webDistDir,
      ASTUDIO_SERVER_PORT: String(port),
      PYTHONUNBUFFERED: '1',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  })

  child.stdout.on('data', (chunk) => {
    output += chunk.toString()
  })
  child.stderr.on('data', (chunk) => {
    output += chunk.toString()
  })

  try {
    const health = await waitForHealth(`http://127.0.0.1:${port}/api/health`, 120000)
    console.log(`Sidecar health check passed: ${JSON.stringify(health)}`)
  } catch (error) {
    console.error(output)
    throw error
  } finally {
    clearTimeout(hardTimeout)
    await stopChild(child)
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error)
  process.exit(1)
})
