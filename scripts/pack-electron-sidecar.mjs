import { existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const rootDir = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const binaryName = process.platform === 'win32' ? 'astudio-server.exe' : 'astudio-server'
const sidecarPath = join(rootDir, 'build', 'server-bin', binaryName)

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: rootDir,
    stdio: 'inherit',
    env: process.env,
    shell: process.platform === 'win32',
  })
  if (result.error) {
    console.error(`Failed to run ${command}: ${result.error.message}`)
  }
  if (result.status !== 0) {
    console.error(`Command failed: ${command} ${args.join(' ')}`)
    process.exit(result.status ?? 1)
  }
}

function detectSidecarArch() {
  if (process.platform !== 'darwin' || !existsSync(sidecarPath)) {
    return process.arch
  }

  const result = spawnSync('file', [sidecarPath], { encoding: 'utf8' })
  const output = `${result.stdout || ''} ${result.stderr || ''}`
  if (output.includes('arm64')) return 'arm64'
  if (output.includes('x86_64')) return 'x64'
  return process.arch
}

const rawArgs = process.argv.slice(2)
const skipBuild = rawArgs.includes('--skip-build')
const skipSidecar = rawArgs.includes('--skip-sidecar')
const passthroughArgs = rawArgs.filter((arg) => arg !== '--skip-build' && arg !== '--skip-sidecar')
const publishArgs = passthroughArgs.includes('--publish') ? [] : ['--publish', 'never']
const targetByPlatform = {
  darwin: '--mac',
  win32: '--win',
  linux: '--linux',
}
const targetFlag = targetByPlatform[process.platform]

if (!targetFlag) {
  console.error(`Unsupported platform for Electron packaging: ${process.platform}`)
  process.exit(1)
}

if (!skipBuild) {
  run('pnpm', ['build:web'])
}

if (!skipSidecar) {
  run('node', ['scripts/build-server-sidecar.mjs'])
}

if (!existsSync(sidecarPath)) {
  console.error(`Backend sidecar is missing: ${sidecarPath}`)
  process.exit(1)
}

const finalArch = detectSidecarArch()
const finalArchFlag = finalArch === 'arm64' ? '--arm64' : '--x64'
if (finalArch !== process.arch) {
  console.warn(`Packaging Electron for ${finalArch} to match backend sidecar architecture.`)
}

run('pnpm', [
  'exec',
  'electron-builder',
  '--config',
  'electron-builder.yml',
  targetFlag,
  finalArchFlag,
  ...publishArgs,
  ...passthroughArgs,
])
