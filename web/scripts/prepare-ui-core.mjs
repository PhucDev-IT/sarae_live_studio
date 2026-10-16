import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'

const root = process.cwd()
const coreDir = resolve(root, '../vendor/react-ui-core')
const distDir = resolve(coreDir, 'packages/ui/dist')
const stampPath = resolve(distDir, '.sarae-build-sha')
const packagePath = resolve(coreDir, 'packages/ui/package.json')
const pnpmVersion = '10.15.0'
const isWindows = process.platform === 'win32'

if (!existsSync(packagePath)) {
  console.error('UI Core submodule is missing.')
  console.error('Run: git submodule update --init --recursive')
  process.exit(1)
}

const quoteCmdArg = (value) => {
  const text = String(value)
  if (!/[\s"&|<>^()]/.test(text)) return text
  return '"' + text.replaceAll('"', '""') + '"'
}

const execute = (command, args, options = {}) => {
  if (isWindows) {
    const shell = process.env.ComSpec || 'cmd.exe'
    const commandLine = [command, ...args].map(quoteCmdArg).join(' ')
    return execFileSync(shell, ['/d', '/s', '/c', commandLine], {
      cwd: root,
      ...options,
    })
  }

  return execFileSync(command, args, {
    cwd: root,
    ...options,
  })
}

const run = (command, args, options = {}) =>
  execute(command, args, {
    stdio: 'inherit',
    ...options,
  })

const capture = (command, args) =>
  execute(command, args, { encoding: 'utf8' }).trim()

const commandExists = (command) => {
  try {
    execute(command, ['--version'], { stdio: 'ignore' })
    return true
  } catch {
    return false
  }
}

const coreSha = capture('git', ['-C', coreDir, 'rev-parse', 'HEAD'])
const builtSha = existsSync(stampPath) ? readFileSync(stampPath, 'utf8').trim() : ''

const required = [
  resolve(distDir, 'index.js'),
  resolve(distDir, 'styles.css'),
  resolve(distDir, 'types/index.d.ts'),
]

if (builtSha === coreSha && required.every(existsSync)) {
  console.log('UI Core is ready at ' + coreSha.slice(0, 8))
  process.exit(0)
}

const pnpmCommand = isWindows ? 'pnpm.cmd' : 'pnpm'
const corepackCommand = isWindows ? 'corepack.cmd' : 'corepack'

if (!commandExists(pnpmCommand)) {
  if (!commandExists(corepackCommand)) {
    console.error('pnpm and Corepack are not available.')
    console.error('Install pnpm ' + pnpmVersion + ' with: npm install -g pnpm@' + pnpmVersion)
    process.exit(1)
  }

  console.log('pnpm not found. Activating pnpm ' + pnpmVersion + ' via Corepack...')
  run(corepackCommand, ['prepare', 'pnpm@' + pnpmVersion, '--activate'])
}

const activeVersion = capture(pnpmCommand, ['--version'])
if (activeVersion !== pnpmVersion) {
  if (!commandExists(corepackCommand)) {
    console.error('pnpm ' + activeVersion + ' is installed, but UI Core requires ' + pnpmVersion + '.')
    console.error('Install the required version with: npm install -g pnpm@' + pnpmVersion)
    process.exit(1)
  }

  console.log('Switching pnpm ' + activeVersion + ' -> ' + pnpmVersion + '...')
  run(corepackCommand, ['prepare', 'pnpm@' + pnpmVersion, '--activate'])
}

console.log('Preparing @sarae/ui from submodule ' + coreSha.slice(0, 8) + '...')
run(pnpmCommand, ['--dir', coreDir, 'install', '--frozen-lockfile'])
run(pnpmCommand, ['--dir', coreDir, '--filter', '@sarae/ui', 'build'])

mkdirSync(distDir, { recursive: true })
writeFileSync(stampPath, coreSha + '\n')
console.log('UI Core build complete.')
