import { copyFileSync, existsSync, mkdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const rootDir = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const serverDir = join(rootDir, 'server')
const outDir = join(rootDir, 'build', 'server-bin')
const binaryName = process.platform === 'win32' ? 'astudio-server.exe' : 'astudio-server'
const sourceBinary = join(serverDir, 'dist', binaryName)
const targetBinary = join(outDir, binaryName)
const dataSeparator = process.platform === 'win32' ? ';' : ':'

mkdirSync(outDir, { recursive: true })

const result = spawnSync(
  'uv',
  [
    'run',
    '--no-dev',
    '--with',
    'pyinstaller',
    'pyinstaller',
    '--clean',
    '--noconfirm',
    '--onefile',
    '--name',
    'astudio-server',
    '--exclude-module',
    'pytest',
    '--exclude-module',
    'py',
    '--exclude-module',
    'pkg_resources',
    '--collect-data',
    'litellm',
    '--add-data',
    `templates${dataSeparator}templates`,
    'desktop_entry.py',
  ],
  {
    cwd: serverDir,
    stdio: 'inherit',
    env: {
      ...process.env,
      PYTHONUNBUFFERED: '1',
    },
  },
)

if (result.status !== 0) {
  process.exit(result.status ?? 1)
}

if (!existsSync(sourceBinary)) {
  console.error(`Expected sidecar binary was not produced: ${sourceBinary}`)
  process.exit(1)
}

copyFileSync(sourceBinary, targetBinary)
console.log(`Copied backend sidecar to ${targetBinary}`)
