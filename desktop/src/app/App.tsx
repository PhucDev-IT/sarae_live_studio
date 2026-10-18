import { AutoPinPage } from '../features/auto-pin/AutoPinPage'
import { useState } from 'react'
import {
  Activity, AlertTriangle, CheckCircle2, Clapperboard, Cloud, FolderOpen, Info, KeyRound, LayoutDashboard,
  RefreshCw, ScanEye, ScrollText, Pin, X, XCircle,
} from 'lucide-react'
import { useApp } from './store'
import { Badge, Button, type Tone } from './ui'
import { OverviewPage } from '../features/overview/OverviewPage'
import { ConnectionPage } from '../features/activation/ConnectionPage'
import { MediaPage } from '../features/media/MediaPage'
import { ObsPage } from '../features/playback/ObsPage'
import { MonitorPage } from '../features/monitor/MonitorPage'
import { LogsPage } from '../features/logs/LogsPage'
import { coreProcess } from '../services/core'
import type { AgentState } from '../types/ipc'

type Page = 'overview' | 'connection' | 'media' | 'obs' | 'auto-pin' | 'monitor' | 'logs'

const NAV: { id: Page; label: string; icon: typeof Activity }[] = [
  { id: 'overview', label: 'Tổng quan', icon: LayoutDashboard },
  { id: 'connection', label: 'Kích hoạt & Cloud', icon: KeyRound },
  { id: 'media', label: 'Thư viện video', icon: FolderOpen },
  { id: 'obs', label: 'OBS & Phát', icon: Clapperboard },
  { id: 'auto-pin', label: 'Tự động ghim', icon: Pin },
  { id: 'monitor', label: 'CAPTCHA Monitor', icon: ScanEye },
  { id: 'logs', label: 'Nhật ký & Chẩn đoán', icon: ScrollText },
]

export const AGENT_LABEL: Record<AgentState, [string, Tone]> = {
  stopped: ['Đã dừng', 'muted'],
  starting: ['Đang khởi động', 'info'],
  running: ['Đang chạy', 'ok'],
  disconnected: ['Mất kết nối', 'warn'],
  error: ['Lỗi', 'danger'],
  stopping: ['Đang dừng', 'info'],
}

export function App() {
  const [page, setPage] = useState<Page>('overview')
  const { state, ready } = useApp()
  const agent = state.status?.agent

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">S</div>
          <div>
            <strong>Sarae Agent</strong>
            <small>Windows · {state.status?.core.version ? `v${state.status.core.version}` : '—'}</small>
          </div>
        </div>
        <nav>
          {NAV.map(({ id, label, icon: Icon }) => (
            <button key={id} className={`nav-item ${page === id ? 'is-active' : ''}`} onClick={() => setPage(id)}>
              <Icon size={18} />
              <span>{label}</span>
              {id === 'monitor' && state.captcha && <i className="nav-alert" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="host-line"><Cloud size={14} />{agent?.agent_id ? agent.agent_id.slice(0, 16) + '…' : 'Chưa kích hoạt'}</div>
          <div className="host-line"><Activity size={14} />{agent?.hostname ?? '—'}</div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <h1>{NAV.find((n) => n.id === page)?.label}</h1>
          <div className="topbar-status">
            <CoreBadge />
            {agent && <Badge tone={AGENT_LABEL[agent.state][1]}>Agent: {AGENT_LABEL[agent.state][0]}</Badge>}
            {state.status && (
              <Badge tone={state.status.obs.connected ? 'ok' : 'muted'}>OBS {state.status.obs.connected ? 'đã kết nối' : 'chưa kết nối'}</Badge>
            )}
          </div>
        </header>

        {state.captcha && <CaptchaBanner onOpen={() => setPage('monitor')} />}
        <CoreBanner />

        <div className="content">
          {!ready && !state.status ? <Booting /> : (
            <>
              {page === 'overview' && <OverviewPage navigate={(p) => setPage(p as Page)} />}
              {page === 'connection' && <ConnectionPage />}
              {page === 'media' && <MediaPage />}
              {page === 'obs' && <ObsPage />}
              {page === 'monitor' && <MonitorPage />}
              {page === 'auto-pin' && <AutoPinPage />}
              {page === 'logs' && <LogsPage />}
            </>
          )}
        </div>
      </main>
      <Toasts />
    </div>
  )
}

function CoreBadge() {
  const { state } = useApp()
  const map: Record<string, [string, Tone]> = {
    ready: ['Core sẵn sàng', 'ok'], starting: ['Core đang khởi động', 'info'], stopping: ['Core đang dừng', 'info'],
    stopped: ['Core đã dừng', 'muted'], crashed: ['Core gặp sự cố', 'danger'],
  }
  const [label, tone] = map[state.core.phase] ?? ['Core', 'muted']
  return <Badge tone={tone}>{label}</Badge>
}

function CoreBanner() {
  const { state, run } = useApp()
  const { phase, last_error, restarts } = state.core
  if (phase === 'ready' || phase === 'starting') return null
  return (
    <div className={`banner ${phase === 'crashed' ? 'banner-danger' : 'banner-info'}`}>
      <AlertTriangle size={18} />
      <div>
        <strong>{phase === 'crashed' ? 'Agent Core đã dừng bất thường' : phase === 'stopping' ? 'Agent Core đang dừng…' : 'Agent Core chưa chạy'}</strong>
        {last_error && <p>{last_error.message} <code>{last_error.code}</code></p>}
        {restarts > 0 && <p>Đã tự khởi động lại {restarts} lần.</p>}
      </div>
      {phase !== 'stopping' && (
        <Button variant="primary" icon={<RefreshCw size={16} />} onClick={() => void run(() => coreProcess.restart(), 'Đã khởi động lại Agent Core')}>
          Khởi động lại Core
        </Button>
      )}
    </div>
  )
}

function CaptchaBanner({ onOpen }: { onOpen: () => void }) {
  const { state, clearCaptcha } = useApp()
  if (!state.captcha) return null
  return (
    <div className="banner banner-danger pulse">
      <ScanEye size={18} />
      <div>
        <strong>Phát hiện CAPTCHA trên TikTok LIVE Studio</strong>
        <p>Lúc {new Date(state.captcha.at).toLocaleTimeString('vi-VN')} · điểm khớp {state.captcha.score}. Hãy xử lý trực tiếp trên máy — Agent không tự vượt CAPTCHA.</p>
      </div>
      <Button variant="secondary" onClick={onOpen}>Xem Monitor</Button>
      <Button variant="ghost" icon={<X size={16} />} onClick={clearCaptcha} aria-label="Ẩn" />
    </div>
  )
}

function Booting() {
  const { state } = useApp()
  return (
    <div className="booting">
      <div className="booting-ring" />
      <strong>{state.core.phase === 'crashed' ? 'Agent Core không khởi động được' : 'Đang khởi động Agent Core…'}</strong>
      <p>Lần chạy đầu tiên có thể mất vài giây để giải nén thành phần xử lý video.</p>
    </div>
  )
}

function Toasts() {
  const { state, dismiss } = useApp()
  return (
    <div className="toasts">
      {state.toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.kind}`}>
          {t.kind === 'success' ? <CheckCircle2 size={18} /> : t.kind === 'error' ? <XCircle size={18} /> : <Info size={18} />}
          <div>
            <strong>{t.title}</strong>
            {t.detail && <code>{t.detail}</code>}
          </div>
          <button className="toast-close" onClick={() => dismiss(t.id)} aria-label="Đóng"><X size={14} /></button>
        </div>
      ))}
    </div>
  )
}
