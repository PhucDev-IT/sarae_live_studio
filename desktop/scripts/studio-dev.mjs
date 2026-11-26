// Starts the same local domain service used by Tauri, then the shared React UI.
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { createInterface } from 'node:readline'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const coreRoot = resolve(desktop, '../agent-core')
const venvPython = resolve(coreRoot, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python')
const python = process.env.SARAE_STUDIO_PYTHON || (existsSync(venvPython) ? venvPython : 'python')
const environment = { ...process.env, PYTHONPATH: [resolve(coreRoot, 'src'), process.env.PYTHONPATH].filter(Boolean).join(process.platform === 'win32' ? ';' : ':') }
const core = spawn(python, ['-m', 'sarae_agent.studio', '--http', '--port', '0', '--parent-pid', String(process.pid), ...process.argv.slice(2)], {
  cwd: coreRoot, env: environment, windowsHide: true, stdio: ['ignore', 'pipe', 'inherit'],
})
let vite
let stopping = false
const startup = setTimeout(() => {
  console.error('Bộ máy local chưa sẵn sàng sau 20 giây.')
  stop(1)
}, 20_000)

function stop(code = 0) {
  if (stopping) return
  stopping = true
  clearTimeout(startup)
  vite?.kill()
  core.kill()
  process.exitCode = code
}

core.on('error', (error) => { console.error(`Không mở được Python: ${error.message}`); stop(1) })
core.on('exit', (code) => { if (!stopping) stop(code || 1) })
createInterface({ input: core.stdout }).on('line', (line) => {
  let ready
  try { ready = JSON.parse(line) } catch { console.log(line); return }
  if (ready.event !== 'studio.ready' || vite || stopping) return
  clearTimeout(startup)
  console.log(`Sarae Studio ${ready.version} · dữ liệu: ${ready.data_dir}`)
  vite = spawn(process.execPath, [resolve(desktop, 'node_modules/vite/bin/vite.js')], {
    cwd: desktop, env: { ...process.env, SARAE_STUDIO_URL: ready.base_url }, windowsHide: true, stdio: 'inherit',
  })
  vite.on('error', (error) => { console.error(error.message); stop(1) })
  vite.on('exit', (code) => stop(code || 0))
})
process.on('SIGINT', () => stop())
process.on('SIGTERM', () => stop())
