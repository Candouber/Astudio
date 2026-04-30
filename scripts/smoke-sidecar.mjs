import { existsSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawn } from 'node:child_process'
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

async function main() {
  if (!existsSync(sidecarPath)) {
    throw new Error(`Sidecar binary does not exist: ${sidecarPath}`)
  }

  const port = await findFreePort()
  const dataDir = mkdtempSync(join(tmpdir(), 'astudio-sidecar-'))
  const webDistDir = join(process.cwd(), 'web', 'dist')
  const child = spawn(sidecarPath, [], {
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

  let output = ''
  child.stdout.on('data', (chunk) => {
    output += chunk.toString()
  })
  child.stderr.on('data', (chunk) => {
    output += chunk.toString()
  })

  try {
    const health = await waitForHealth(`http://127.0.0.1:${port}/api/health`)
    console.log(`Sidecar health check passed: ${JSON.stringify(health)}`)
  } catch (error) {
    console.error(output)
    throw error
  } finally {
    if (!child.killed) {
      child.kill(process.platform === 'win32' ? undefined : 'SIGTERM')
    }
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error)
  process.exit(1)
})
