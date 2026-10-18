import { useMemo, useState } from 'react'
import { revealItemInDir } from '@tauri-apps/plugin-opener'
import { Download, FolderOpen, Info, ScrollText, TerminalSquare } from 'lucide-react'
import { useApp } from '../../app/store'
import { ActionButton, Card, Empty, Input, Stat, formatDateTime } from '../../app/ui'
import { call, coreProcess } from '../../services/core'
import type { LogEntry } from '../../types/ipc'

const LEVELS: LogEntry['level'][] = ['INFO', 'WARNING', 'ERROR']
const RANK: Record<string, number> = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3, CRITICAL: 4 }

export function LogsPage() {
  const { state, run } = useApp()
  const [level, setLevel] = useState<LogEntry['level']>('INFO')
  const [query, setQuery] = useState('')
  const [exported, setExported] = useState<{ path: string; bytes: number } | null>(null)
  const [stderr, setStderr] = useState<string[] | null>(null)

  const logs = useMemo(() => {
    const q = query.toLowerCase()
    return state.logs
      .filter((l) => RANK[l.level] >= RANK[level])
      .filter((l) => !q || l.message.toLowerCase().includes(q) || l.code.toLowerCase().includes(q) || l.correlation_id.includes(q))
      .slice()
      .reverse()
  }, [state.logs, level, query])

  const core = state.status?.core

  return (
    <div className="stack">
      <div className="grid grid-2">
        <Card title="Thông tin hệ thống" icon={<Info size={16} />}>
          <div className="stats-row">
            <Stat label="Agent Core" value={core ? `v${core.version}` : '—'} sub={core ? `PID ${core.pid} · IPC v${core.ipc_protocol}` : undefined} />
            <Stat label="Khởi động lúc" value={formatDateTime(core?.started_at)} sub={`Tự khởi động lại: ${state.core.restarts} lần`} />
          </div>
          <p className="muted small">Dữ liệu cục bộ: <code className="selectable">{core?.data_root ?? '—'}</code></p>
        </Card>
        <Card title="Gói chẩn đoán" icon={<Download size={16} />}>
          <p className="muted small">Tạo file .zip gồm nhật ký, cấu hình (không có mật khẩu/credential) và trạng thái hiện tại để gửi cho bộ phận kỹ thuật.</p>
          <div className="row">
            <ActionButton variant="primary" icon={<Download size={16} />} onAction={async () => {
              const result = await run(() => call('diagnostics.export'), 'Đã tạo gói chẩn đoán')
              if (result) setExported(result)
            }}>Tạo gói chẩn đoán</ActionButton>
            {exported && <ActionButton icon={<FolderOpen size={16} />} onAction={() => run(() => revealItemInDir(exported.path))}>Mở thư mục</ActionButton>}
            <ActionButton variant="ghost" icon={<TerminalSquare size={16} />} onAction={async () => setStderr(await coreProcess.stderr())}>Xem stderr của Core</ActionButton>
          </div>
          {exported && <small className="muted selectable">{exported.path}</small>}
          {stderr && <pre className="stderr">{stderr.length ? stderr.slice(-60).join('\n') : '(trống)'}</pre>}
        </Card>
      </div>

      <Card
        title={`Nhật ký nghiệp vụ (${logs.length})`}
        icon={<ScrollText size={16} />}
        actions={<>
          <div className="segmented">
            {LEVELS.map((l) => <button key={l} className={level === l ? 'is-active' : ''} onClick={() => setLevel(l)}>{l === 'INFO' ? 'Tất cả' : l === 'WARNING' ? 'Cảnh báo+' : 'Lỗi'}</button>)}
          </div>
          <Input value={query} placeholder="Lọc theo nội dung, mã lỗi, request ID" onChange={(e) => setQuery(e.target.value)} />
        </>}
      >
        {logs.length === 0 ? <Empty title="Chưa có nhật ký" /> : (
          <div className="log-list">
            {logs.slice(0, 400).map((l, i) => (
              <div key={`${l.ts}-${i}`} className={`log-row log-${l.level.toLowerCase()}`}>
                <span className="mono log-ts">{new Date(l.ts).toLocaleTimeString('vi-VN')}</span>
                <span className="log-level">{l.level}</span>
                <span className="log-msg">{l.message}</span>
                {l.code !== '-' && <code>{l.code}</code>}
                {l.correlation_id !== '-' && <span className="mono muted log-cid" title="Correlation ID">{l.correlation_id.slice(0, 12)}</span>}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}
