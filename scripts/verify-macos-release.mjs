import { existsSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'

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

function findApps() {
  if (!existsSync(releaseDir)) return []
  return readdirSync(releaseDir)
    .map((name) => join(releaseDir, name, productName))
    .filter((path) => existsSync(path) && statSync(path).isDirectory())
}

function verifyApp(appPath) {
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
  console.log(`Verified macOS release app: ${appPath}`)
}

if (process.platform !== 'darwin') {
  fail('macOS release verification must run on macOS.')
}

const apps = findApps()
if (!apps.length) {
  fail('No macOS .app bundles were found under release/.')
}

for (const appPath of apps) {
  verifyApp(appPath)
}
