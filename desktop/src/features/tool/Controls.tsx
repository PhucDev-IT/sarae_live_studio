import { useEffect, useState, type ReactNode } from 'react'
import { open } from '@tauri-apps/plugin-dialog'
import { ChevronDown, Clapperboard, Cloud, FolderPlus, Pin, RefreshCw, ScanEye, Video } from 'lucide-react'
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
      <CaptchaRow />
      <AutoPinRow />
      <VideoRow />
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
      ? `Đang phát ${playback.current_filename ?? ''} (${playback.index + 1}/${playback.count})${playback.paused ? ' · tạm dừng' : ''}`
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
      control={<Switch checked={obs.auto_connect} label="OBS" onToggle={(value) =>
        run(() => call(value ? 'obs.connect' : 'obs.disconnect')).then(refresh)} />} />
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

function AutoPinRow() {
  const { run } = useApp()
  const [pin, setPin] = useState<AutoPinStatus | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [products, setProducts] = useState('')
  const [interval, setIntervalSeconds] = useState(30)

  useEffect(() => {
    let alive = true
    const load = () => call('autopin.status').then((s) => {
      if (!alive) return
      setPin(s)
    }).catch(() => undefined)
    void load()
    const timer = window.setInterval(load, 3000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [])

  // Prefill the editor once from what the Core already has.
  const loaded = pin !== null
  useEffect(() => {
    if (!loaded || !pin) return
    setProducts(pin.products.join('\n'))
    setIntervalSeconds(pin.interval_seconds)
  }, [loaded])

  const act = async (fn: () => Promise<AutoPinStatus>, message?: string) => {
    const next = await run(fn, message)
    if (next) setPin(next)
  }
  if (!pin) return null
  const ready = pin.browser_open && pin.products.length > 0
  let status: string
  let tone: Tone = 'muted'
  if (pin.last_error) {
    status = pin.last_error
    tone = 'danger'
  } else if (pin.enabled) {
    status = `Đang ghim ${pin.products.length} sản phẩm mỗi ${pin.interval_seconds} s${pin.last_pin ? ` · vừa ghim: ${pin.last_pin}` : ''}`
    tone = 'ok'
  } else if (!pin.browser_open) {
    status = 'Tắt · cần mở Chrome và đăng nhập LIVE Console'
  } else if (!pin.products.length) {
    status = 'Tắt · chưa có danh sách sản phẩm'
  } else {
    status = `Tắt · ${pin.products.length} sản phẩm, mỗi ${pin.interval_seconds} s`
  }
  return (
    <Row icon={<Pin size={16} />} title="Tự động ghim" status={status} tone={tone}
      control={<>
        <button className={`icon-btn ${expanded ? 'is-open' : ''}`} onClick={() => setExpanded(!expanded)} aria-label="Cấu hình tự động ghim"><ChevronDown size={16} /></button>
        <Switch checked={pin.enabled} disabled={!pin.enabled && !ready} label="Tự động ghim"
          onToggle={(value) => act(() => call(value ? 'autopin.start' : 'autopin.stop'))} />
      </>}>
      {expanded && (
        <div className="row-extra">
          <ActionButton onAction={() => act(() => call('autopin.open', { url: LIVE_CONSOLE_URL }), 'Đã mở Chrome LIVE Console')}>
            {pin.browser_open ? 'Mở lại Chrome LIVE Console' : 'Mở Chrome LIVE Console'}
          </ActionButton>
          <textarea className="input textarea" rows={4} value={products} onChange={(e) => setProducts(e.target.value)}
            placeholder={'Tên hoặc mã sản phẩm, mỗi dòng một sản phẩm'} spellCheck={false} />
          <div className="inline">
            <span className="muted small">Mỗi</span>
            <Input type="number" min={5} max={3600} value={interval} onChange={(e) => setIntervalSeconds(Number(e.target.value))} className="input input-num" />
            <span className="muted small">giây</span>
            <ActionButton variant="primary" disabled={!products.trim()} onAction={() => act(() => call('autopin.configure', {
              products: products.split('\n').map((s) => s.trim()).filter(Boolean), interval_seconds: interval,
            }), 'Đã lưu danh sách ghim')}>Lưu</ActionButton>
          </div>
        </div>
      )}
    </Row>
  )
}

function VideoRow() {
  const { state, run, refresh, refreshFolders } = useApp()
  const media = state.status?.media
  if (!media) return null
  const scanning = state.scan ?? media.scan
  const addFolder = async () => {
    const picked = await open({ directory: true, multiple: true, title: 'Chọn thư mục chứa video MP4' })
    const paths = Array.isArray(picked) ? picked : picked ? [picked] : []
    for (const path of paths) await run(() => call('media.folders.add', { path }), 'Đã thêm thư mục')
    if (paths.length) {
      await refreshFolders()
      await run(() => call('media.scan'))
    }
  }
  return (
    <Row icon={<Video size={16} />} title="Thư mục video"
      status={scanning ? `Đang quét… ${scanning.found} video`
        : media.folder_count ? `${media.folder_count} thư mục · ${media.video_count} video` : 'Chưa chọn thư mục MP4'}
      tone={media.folder_count ? 'ok' : 'warn'}
      control={<>
        <ActionButton variant="ghost" icon={<FolderPlus size={14} />} onAction={addFolder} aria-label="Thêm thư mục" />
        <ActionButton variant="ghost" icon={<RefreshCw size={14} />} disabled={!media.folder_count || !!scanning}
          onAction={() => run(() => call('media.scan')).then(refresh)} aria-label="Quét lại" />
      </>} />
  )
}
