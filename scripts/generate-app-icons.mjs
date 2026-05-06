import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const rootDir = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const sourceIcon = join(rootDir, 'build', 'icon-rounded.png')
const buildIcon = join(rootDir, 'build', 'icon.png')
const iconsetDir = join(rootDir, 'build', 'icon.iconset')
const icnsPath = join(rootDir, 'build', 'icon.icns')
const icoPath = join(rootDir, 'build', 'icon.ico')
const publicIcon = join(rootDir, 'web', 'public', 'astudio-icon.png')
const staticIcon = join(rootDir, 'static', 'astudio-icon.png')
const tempDir = mkdtempSync(join(tmpdir(), 'astudio-icons-'))
const paddedIcon = join(tempDir, 'icon-1024.png')
const innerIcon = join(tempDir, 'icon-inner.png')

const macIconsetSizes = [
  ['icon_16x16.png', 16],
  ['icon_16x16@2x.png', 32],
  ['icon_32x32.png', 32],
  ['icon_32x32@2x.png', 64],
  ['icon_128x128.png', 128],
  ['icon_128x128@2x.png', 256],
  ['icon_256x256.png', 256],
  ['icon_256x256@2x.png', 512],
  ['icon_512x512.png', 512],
  ['icon_512x512@2x.png', 1024],
]
const icnsTypes = [
  ['icp4', 'icon_16x16.png'],
  ['icp5', 'icon_32x32.png'],
  ['icp6', 'icon_32x32@2x.png'],
  ['ic07', 'icon_128x128.png'],
  ['ic08', 'icon_256x256.png'],
  ['ic09', 'icon_512x512.png'],
  ['ic10', 'icon_512x512@2x.png'],
]
const icoSizes = [16, 24, 32, 48, 64, 128, 256]

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: rootDir,
    encoding: 'utf8',
  })
  if (result.status !== 0) {
    const output = `${result.stdout || ''}${result.stderr || ''}`
    throw new Error(`Command failed: ${command} ${args.join(' ')}\n${output}`)
  }
}

function generatePng(input, output, size) {
  run('sips', ['-z', String(size), String(size), input, '--out', output])
}

function writeIco(entries, outputPath) {
  const headerSize = 6
  const entrySize = 16
  let offset = headerSize + entries.length * entrySize
  const dir = Buffer.alloc(offset)

  dir.writeUInt16LE(0, 0)
  dir.writeUInt16LE(1, 2)
  dir.writeUInt16LE(entries.length, 4)

  for (const [index, entry] of entries.entries()) {
    const png = readFileSync(entry.path)
    const pos = headerSize + index * entrySize
    dir.writeUInt8(entry.size === 256 ? 0 : entry.size, pos)
    dir.writeUInt8(entry.size === 256 ? 0 : entry.size, pos + 1)
    dir.writeUInt8(0, pos + 2)
    dir.writeUInt8(0, pos + 3)
    dir.writeUInt16LE(1, pos + 4)
    dir.writeUInt16LE(32, pos + 6)
    dir.writeUInt32LE(png.length, pos + 8)
    dir.writeUInt32LE(offset, pos + 12)
    offset += png.length
  }

  writeFileSync(outputPath, Buffer.concat([dir, ...entries.map((entry) => readFileSync(entry.path))]))
}

function writeIcns(entries, outputPath) {
  const chunks = entries.map(([type, pngPath]) => {
    const png = readFileSync(pngPath)
    const header = Buffer.alloc(8)
    header.write(type, 0, 4, 'ascii')
    header.writeUInt32BE(png.length + 8, 4)
    return Buffer.concat([header, png])
  })
  const totalSize = 8 + chunks.reduce((sum, chunk) => sum + chunk.length, 0)
  const header = Buffer.alloc(8)
  header.write('icns', 0, 4, 'ascii')
  header.writeUInt32BE(totalSize, 4)
  writeFileSync(outputPath, Buffer.concat([header, ...chunks]))
}

if (!existsSync(sourceIcon)) {
  throw new Error(`Missing source icon: ${sourceIcon}`)
}

try {
  generatePng(sourceIcon, innerIcon, 840)
  run('sips', ['--padToHeightWidth', '1024', '1024', innerIcon, '--out', paddedIcon])

  copyFileSync(paddedIcon, buildIcon)
  copyFileSync(paddedIcon, publicIcon)
  copyFileSync(paddedIcon, staticIcon)

  mkdirSync(iconsetDir, { recursive: true })
  for (const [name, size] of macIconsetSizes) {
    generatePng(paddedIcon, join(iconsetDir, name), size)
  }
  writeIcns(
    icnsTypes.map(([type, name]) => [type, join(iconsetDir, name)]),
    icnsPath,
  )

  const icoEntries = icoSizes.map((size) => {
    const pngPath = join(tempDir, `icon-${size}.png`)
    generatePng(paddedIcon, pngPath, size)
    return { size, path: pngPath }
  })
  writeIco(icoEntries, icoPath)

  console.log('Generated app icons with a 1024px canvas and 840px visual safe area.')
} finally {
  rmSync(tempDir, { recursive: true, force: true })
}
