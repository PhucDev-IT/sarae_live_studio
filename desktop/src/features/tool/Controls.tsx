import { useEffect, useState, type ReactNode } from 'react'
import { ChevronDown, Clapperboard, Cloud, Download, Pin, ScanEye } from 'lucide-react'
import { useApp } from '../../app/store'
import { ActionButton, Input, Switch, type Tone } from '../../app/ui'
import { call } from '../../services/core'
import type { AutoPinStatus } from '../../types/ipc'

const MONITOR_LABEL: Record<string, [string, Tone]> = {
  idle: ['Tắt', 'muted'],
  starting: ['Đang khởi động', 'info'],
  watching: ['Đang giám sát', 'ok'],
  captcha_detected: ['Phát hiện CAPTCHA', 'danger'],
  window_unavailable: ['Không thấy cửa sổ TikTok LIVE Studio', 'warn'],
  template_missing_or_invalid: ['Ảnh mẫu không hợp lệ', 'warn'],
  stopped: ['Tắt', 'muted'],
  error: ['Có lỗi', 'danger'],
}

function Row({ icon, title, status, tone = 'muted', control, children }: {
  icon: ReactNode; title: string; status: ReactNode; tone?: Tone; control?: ReactNode; children?: ReactNode
}) {
  return (
    <div className="row-item">
      <div className="row-main">
        <span className={`row-icon tone-${tone}`}>{icon}</span>
        <div className="row-text">
          <strong>{title}</strong>
          <small className={`tone-${tone}`}>{status}</small>
        </div>
        <div className="row-control">{control}</div>
      </div>
      {children}
    </div>
  )
}

/** The on/off switches. Each row shows one status line; details live on the Web or in the log. */
export function Controls() {
  return (
    <section className="panel panel-flush">
      <CloudRow />
      <ObsRow />
      <CacheRow />
      <CaptchaRow />
      <AutoPinRow />
    </section>
  )
}

function CloudRow() {
  const { state, run, refresh } = useApp()
  const agent = state.status?.agent
  const cloud = state.status?.cloud
  if (!agent) return null
  const on = agent.state !== 'stopped' && agent.state !== 'error'
  const last = state.commands[0]
  let status: string
  let tone: Tone = 'muted'
  if (agent.state === 'running') {
    status = 'Đã kết nối' + (last ? ` · lệnh gần nhất: ${last.kind} ${last.status === 'failed' ? '✗' : last.status === 'succeeded' ? '✓' : '…'}` : '')
    tone = 'ok'
  } else if (agent.state === 'disconnected') {
    status = `Mất kết nối${cloud?.next_retry_seconds ? `, thử lại sau ${Math.round(cloud.next_retry_seconds)} s` : ''}`
    tone = 'warn'
  } else if (agent.state === 'error') {
    status = agent.error?.message ?? 'Cloud từ chối Agent'
    tone = 'danger'
  } else if (agent.state === 'starting' || agent.state === 'stopping') {
    status = agent.state === 'starting' ? 'Đang kết nối…' : 'Đang dừng…'
    tone = 'info'
  } else {
    status = 'Tắt: Web không điều khiển được máy này'
  }
  return (
    <Row icon={<Cloud size={16} />} title="Kết nối Cloud" status={status} tone={tone}
      control={<Switch checked={on} label="Kết nối Cloud" onToggle={(value) =>
        run(() => call(value ? 'agent.start' : 'agent.stop')).then(refresh)} />} />
  )
}

function ObsRow() {
  const { state, run, refresh } = useApp()
  const obs = state.status?.obs
  const playback = state.status?.playback
  if (!obs) return null
  let status: string
  let tone: Tone = 'muted'
  if (obs.connected) {
    status = playback?.active
      ? playback.source_mode === 'camera' ? 'Đang dùng camera dự phòng' :
        `Đang phát ${playback.current_filename ?? ''} (${playback.index + 1}/${playback.count})${playback.paused ? ' · tạm dừng' : ''}`
      : `Đã kết nối OBS ${obs.obs_version ?? ''}`
    tone = 'ok'
  } else if (obs.state === 'connecting') {
    status = 'Đang kết nối…'
    tone = 'info'
  } else if (obs.error) {
    status = obs.error.message
    tone = obs.auto_connect ? 'warn' : 'muted'
  } else {
    status = obs.auto_connect ? 'Chưa kết nối, đang thử lại' : 'Tắt'
  }
  return (
    <Row icon={<Clapperboard size={16} />} title="OBS" status={status} tone={tone}
      control={<>
        {playback?.active && <ActionButton disabled={!state.config?.obs.camera_input} onAction={async () => {
          await run(() => call(playback.source_mode === 'camera' ? 'playback.video' : 'playback.camera'))
          await refresh()
        }}>{playback.source_mode === 'camera' ? 'Về video' : 'Dùng camera'}</ActionButton>}
        <Switch checked={obs.auto_connect} label="OBS" onToggle={(value) =>
          run(() => call(value ? 'obs.connect' : 'obs.disconnect')).then(refresh)} />
      </>} />
  )
}

const megabytes = (bytes?: number) => `${((bytes ?? 0) / 1024 ** 2).toFixed(0)} MB`

/** Videos of the LIVE being prepared: the Web starts the download, the Agent plays only from this cache. */
function CacheRow() {
  const { state } = useApp()
  const job = state.status?.media?.cloud_cache
  if (!job || job.state === 'IDLE') return null
  const total = job.bytes_total ?? 0
  const percent = total ? Math.min(100, Math.round((job.bytes_done ?? 0) / total * 100)) : 0
  const ready = job.ready?.length ?? 0
  let status: string
  let tone: Tone
  if (job.state === 'PREPARING') {
    status = `Đang tải video phiên #${job.session_id}: ${ready}/${job.total ?? '?'} video · ${megabytes(job.bytes_done)} / ${megabytes(total)} (${percent}%)`
    tone = 'info'
  } else if (job.state === 'READY') {
    status = `Đủ ${ready} video cho phiên #${job.session_id} · sẵn sàng phát`
    tone = 'ok'
  } else {
    status = `Thiếu ${job.failed?.length ?? 0} video cho phiên #${job.session_id}: ${job.failed?.[0]?.error ?? 'tải lỗi'}`
    tone = 'danger'
  }
  return (
    <Row icon={<Download size={16} />} title="Video LIVE trên máy" status={status} tone={tone}>
      {job.state === 'PREPARING' && <div className="cache-bar"><span style={{ width: `${percent}%` }} /></div>}
    </Row>
  )
}

function CaptchaRow() {
  const { state, run, refresh } = useApp()
  const monitor = state.status?.monitor
  if (!monitor) return null
  const s = monitor.state
  const [label, tone] = MONITOR_LABEL[s.status] ?? [s.status, 'muted' as Tone]
  // Detection data comes from Sarae through the Cloud (nothing to add here); it arrives once Cloud is connected.
  const noTemplate = monitor.templates === 0
  return (
    <Row icon={<ScanEye size={16} />} title="Giám sát CAPTCHA"
      status={noTemplate && !s.running ? 'Đang chờ dữ liệu nhận diện từ Sarae (cần Kết nối Cloud)'
        : s.error && s.status === 'error' ? s.error : label}
      tone={noTemplate && !s.running ? 'warn' : tone}
      control={<Switch checked={s.running} disabled={noTemplate && !s.running} label="Giám sát CAPTCHA" onToggle={(value) =>
        run(() => call(value ? 'monitor.start' : 'monitor.stop')).then(refresh)} />} />
  )
}

const LIVE_CONSOLE_URL = 'https://shop.tiktok.com/streamer/live/product/dashboard'

/** Two steps on one Chrome profile: 1. sign in by hand in plain Chrome, 2. let the Agent pin (Playwright). */
function AutoPinRow() {
  const { run } = useApp()
  const [pin, setPin] = useState<AutoPinStatus | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [syncInfo, setSyncInfo] = useState('')
  const [interval, setIntervalSeconds] = useState(30)

  useEffect(() => {
    let alive = true
    const poll = () => void call('autopin.status').then(next => {
      if (alive) setPin(next)
    }).catch(() => undefined)
    poll()
    const timer = window.setInterval(poll, 3000)
    return () => { alive = false; window.clearInterval(timer) }
  }, [])

  const act = async (fn: () => Promise<AutoPinStatus>, message?: string) => {
    const next = await run(fn, message)
    if (next) setPin(next)
  }

  const sync = async () => {
    if (syncing) return
    setSyncing(true)
    setSyncInfo('')
    try {
      // Core refreshes TikTok Console and observes its own Network response.
      // Only the resulting product snapshot is sent to Sarae Cloud.
      const result = await run(() => call('tiktok.products.sync'))
      if (result) {
        setSyncInfo('Đồng bộ xong · Tạo mới ' + result.imported.created +
          ' · Cập nhật ' + result.imported.updated + ' sản phẩm')
      }
    } finally {
      setSyncing(false)
    }
  }

  if (!pin) return null
  const canPin = !pin.login_open && pin.browser_open
  const status = syncing ? 'Đang đồng bộ sản phẩm…'
    : pin.enabled ? 'Đang tự động ghim'
    : pin.browser_open ? 'TikTok LIVE Console đã mở'
    : 'Sẵn sàng mở TikTok LIVE Console'
  const tone: Tone = pin.enabled ? 'ok' : pin.browser_open ? 'info' : 'muted'

  return (
    <Row icon={<Pin size={16} />} title="TikTok LIVE Console" status={status} tone={tone}
      control={<button className={`icon-btn ${expanded ? 'is-open' : ''}`}
        onClick={() => setExpanded(!expanded)} aria-label="Điều khiển TikTok LIVE Console">
        <ChevronDown size={16} />
      </button>}>
      {expanded && <div className="row-extra">
        <div className="inline">
          <ActionButton variant="primary" onAction={() => act(
            () => call('autopin.open', { url: LIVE_CONSOLE_URL }), 'Đã mở TikTok LIVE Console'
          )}>Mở TikTok LIVE Console</ActionButton>
          <ActionButton disabled={!pin.browser_open || syncing} onAction={sync}>
            {syncing ? 'Đang đồng bộ…' : 'Đồng bộ sản phẩm'}
          </ActionButton>
        </div>
        {syncInfo && <small className="tone-ok">{syncInfo}</small>}
        <small className="muted">
          Đăng nhập và quản lý TikTok trực tiếp trong Chrome. Đồng bộ chạy ngầm, không hiển thị danh sách sản phẩm trên Agent.
        </small>
        <div className="inline">
          <span className="muted small">Auto Pin (để phát triển sau)</span>
          <Switch checked={pin.enabled} disabled={!pin.enabled && !canPin}
            label="Tự động ghim"
            onToggle={value => act(() => call(value ? 'autopin.start' : 'autopin.stop'))} />
          <Input type="number" min={5} max={3600} value={interval}
            onChange={e => setIntervalSeconds(Number(e.target.value))}
            className="input input-num" />
          <span className="muted small">giây</span>
          <ActionButton disabled={!pin.products.length || interval === pin.interval_seconds}
            onAction={() => act(() => call('autopin.configure', {
              products: pin.products, interval_seconds: interval,
            }), 'Đã lưu chu kỳ ghim')}>Lưu</ActionButton>
        </div>
        {pin.enabled
          ? <ActionButton variant="danger" onAction={() => act(() => call('autopin.stop'), 'Đã dừng Auto Pin')}>
              Dừng Auto Pin
            </ActionButton>
          : <ActionButton disabled={!canPin} onAction={() => act(() => call('autopin.start'))}>
              Bắt đầu Auto Pin
            </ActionButton>}
      </div>}
    </Row>
  )
}
