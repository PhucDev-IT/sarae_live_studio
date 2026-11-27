import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { AlertTriangle, ArrowDown, ArrowUp, Check, CircleStop, Film, FlaskConical, Layers, ListVideo, MonitorPlay, Pause, Pencil, Pin, PinOff, Play, Plus, Radio, Repeat, Settings2, SkipBack, SkipForward, Trash2, X } from 'lucide-react'
import { studioClient, type LiveSession, type LiveStatus, type Media, type ObsStatus, type Product, type VideoOrder } from '../../services/studio'
import { clock } from './MarkerEditor'
import { ReplyDesk } from './ReplyDesk'
import { EmptyState, ErrorNotice, Modal, errorMessage } from './ui'

type Props = { products: Product[]; media: Media[]; disabled: boolean; visible: boolean; onNotice: (message: string) => void }
const when = new Intl.DateTimeFormat('vi-VN', { dateStyle: 'short', timeStyle: 'short' })
const endReasons: Record<string, string> = { operator: 'Bạn đã dừng', core_restart: 'Gián đoạn do ứng dụng khởi động lại', core_stopped: 'Ứng dụng đã đóng', playback_ended: 'Đã phát hết danh sách' }
const eventLabels: Record<string, string> = { STARTED: 'Bắt đầu phát', STOPPED: 'Đã dừng', PAUSE: 'Tạm dừng', RESUME: 'Phát tiếp', PIN_APPLIED: 'Đã ghim theo mốc', PIN_FAILED: 'Ghim không được', PIN_DRIFT: 'Ghim bị đổi trên TikTok', LIVE_GATE_CHANGED: 'Trạng thái LIVE thay đổi', PIN_MODE: 'Đổi chế độ ghim', PLAYBACK_ENDED: 'Phát kết thúc', ERROR: 'Lỗi phát video' }
const orders: [VideoOrder, string][] = [['sequential', 'Lần lượt'], ['random', 'Ngẫu nhiên'], ['shuffle', 'Xáo trộn']]

function duration(seconds: number) {
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60)
  return h ? `${h} giờ ${m} phút` : `${m} phút`
}

export function LivePanel({ products, media, disabled, visible, onNotice }: Props) {
  const [sessions, setSessions] = useState<LiveSession[]>([])
  const [status, setStatus] = useState<LiveStatus | null>(null)
  const [obs, setObs] = useState<ObsStatus | null>(null)
  const [editing, setEditing] = useState<LiveSession | 'new' | null>(null)
  const [deleting, setDeleting] = useState<LiveSession | null>(null)
  const [obsOpen, setObsOpen] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const mediaById = useMemo(() => new Map(media.map(item => [item.id, item])), [media])
  const loadAll = useCallback(async () => {
    try {
      const [nextSessions, nextStatus, nextObs] = await Promise.all([studioClient.listLiveSessions(), studioClient.liveStatus(), studioClient.obsStatus()])
      setSessions(nextSessions); setStatus(nextStatus); setObs(nextObs)
    } catch (cause) { setError(errorMessage(cause)) }
  }, [])
  useEffect(() => { if (visible && !disabled) void loadAll() }, [visible, disabled, loadAll, products, media])
  // While on air, poll the player: the cursor and the pins change every second without a mutation event.
  useEffect(() => {
    if (!visible || disabled || !status?.active) return
    const timer = window.setInterval(() => { void studioClient.liveStatus().then(setStatus).catch(() => {}) }, 1000)
    return () => window.clearInterval(timer)
  }, [visible, disabled, status?.active])

  const act = async (key: string, action: () => Promise<unknown>, message?: string) => {
    if (busy || disabled) return
    setBusy(key)
    setError('')
    try {
      const result = await action()
      if (result && typeof result === 'object' && 'active' in result) setStatus(result as LiveStatus)
      if (message) onNotice(message)
      await loadAll()
    } catch (cause) { setError(errorMessage(cause)); await loadAll() }
    finally { setBusy('') }
  }
  const live = status?.active ? status : null
  const realPins = status?.pins.real ?? false

  return <>
    <div className="studio-page-heading">
      <div><h1>Phiên LIVE</h1><p>Phát video qua OBS; sản phẩm được ghim đúng lúc video giới thiệu nó.</p></div>
      <div className="studio-actions">
        <button className={`studio-obs-chip ${obs?.connected ? 'ok' : 'off'}`} onClick={() => setObsOpen(true)} title="Cài đặt OBS"><MonitorPlay size={16} />{obs?.connected ? `OBS ${obs.obs_version ?? ''}`.trim() : 'OBS chưa kết nối'}<Settings2 size={14} /></button>
        <button className="studio-button primary" disabled={disabled} onClick={() => setEditing('new')}><Plus size={17} />Tạo phiên</button>
      </div>
    </div>
    {error && <ErrorNotice message={error} />}
    {live && live.session && <ControlRoom status={live} session={live.session} mediaById={mediaById} busy={busy} disabled={disabled}
      onControl={action => act(action, () => studioClient.liveControl(action), action === 'stop' ? 'Đã dừng phiên LIVE.' : undefined)}
      onSeek={ms => act('seek', () => studioClient.liveSeek(ms))}
      onPins={real => act('pins', () => studioClient.livePins(real), real ? 'Đã bật ghim thật trên TikTok.' : 'Đã chuyển sang chạy thử: không bấm ghim trên TikTok.')} />}
    <ReplyDesk products={products} disabled={disabled} visible={visible} onNotice={onNotice} />
    <section className="studio-card">
      <header className="studio-card-heading"><ListVideo size={19} /><div className="studio-grow"><h2>Danh sách phiên</h2><p>Mỗi phiên là các video phát nối tiếp; mốc ghim đặt sẵn trên từng video trong Thư viện video.</p></div>
        {!live && <label className="studio-switch studio-pin-mode" title="Tắt: chạy thử, chỉ ghi lại sẽ ghim gì. Bật: bấm ghim thật trên TikTok LIVE Console."><input type="checkbox" role="switch" checked={realPins} disabled={disabled || !!busy || !status} onChange={event => void act('pins', () => studioClient.livePins(event.target.checked), event.target.checked ? 'Đã bật ghim thật trên TikTok.' : 'Đã chuyển sang chạy thử.')} /><span aria-hidden="true" /><small>{realPins ? 'Ghim thật trên TikTok' : 'Chạy thử (không ghim thật)'}</small></label>}
      </header>
      {!sessions.length ? <EmptyState icon={<Radio size={30} />} title="Chưa có phiên LIVE nào" description="Đặt mốc ghim cho video trong Thư viện video, rồi tạo phiên với các video đó và bấm Phát." action={<button className="studio-button primary" disabled={disabled} onClick={() => setEditing('new')}><Plus size={16} />Tạo phiên đầu tiên</button>} />
        : <ul className="studio-session-list">{sessions.map(session => {
          const videos = session.video_ids.map(id => mediaById.get(id)).filter((item): item is Media => !!item)
          const pins = videos.reduce((total, item) => total + (item.marker_count ?? 0), 0)
          const missing = videos.filter(item => item.status === 'missing').length
          const isLive = session.status === 'live'
          return <li key={session.id} className={isLive ? 'live' : ''}>
            <div className="studio-session-thumbs" aria-hidden="true">{videos.slice(0, 3).map(item => <span key={item.id}>{item.thumbnail_url ? <img src={item.thumbnail_url} alt="" /> : <Film size={14} />}</span>)}</div>
            <div className="studio-grow">
              <strong>{session.title}</strong>
              <small>{videos.length} video · {pins} mốc ghim · {orders.find(o => o[0] === session.video_order)?.[1]}{session.video_loop ? ', lặp lại' : ', phát một lượt'}{session.started_at ? ` · phát gần nhất ${when.format(new Date(session.started_at))}` : ''}{session.status === 'ended' && session.end_reason ? ` · ${endReasons[session.end_reason] ?? session.end_reason}` : ''}</small>
              {missing > 0 && <small className="studio-warn-line"><AlertTriangle size={12} />{missing} video thiếu file</small>}
            </div>
            {isLive ? <span className="studio-live-badge"><i />Đang phát</span> : <span className={`studio-tag ${session.status === 'ended' ? 'neutral' : 'honey'}`}>{session.status === 'ended' ? 'Đã kết thúc' : 'Chưa phát'}</span>}
            <div className="studio-actions">
              {!isLive && <button className="studio-button primary" disabled={disabled || !!busy || !!live || !videos.length || missing > 0} title={live ? 'Đang có phiên khác phát' : 'Bắt đầu phát'} onClick={() => void act('start:' + session.id, () => studioClient.liveStart(session.id), realPins ? 'Đã bắt đầu phát, ghim thật theo mốc.' : 'Đã bắt đầu phát (chạy thử ghim).')}><Play size={15} fill="currentColor" />{busy === 'start:' + session.id ? 'Đang mở…' : 'Phát'}</button>}
              <button className="studio-button ghost icon-only" disabled={disabled} aria-label={`Sửa ${session.title}`} title="Sửa phiên" onClick={() => setEditing(session)}><Pencil size={16} /></button>
              <button className="studio-button ghost icon-only danger" disabled={disabled || isLive} aria-label={`Xóa ${session.title}`} title={isLive ? 'Dừng phiên trước khi xóa' : 'Xóa phiên'} onClick={() => setDeleting(session)}><Trash2 size={16} /></button>
            </div>
          </li>
        })}</ul>}
    </section>
    {editing && <SessionEditor session={editing === 'new' ? null : editing} media={media} onClose={() => setEditing(null)} onSaved={async message => { setEditing(null); onNotice(message); await loadAll() }} />}
    {deleting && <Modal title="Xóa phiên LIVE?" onClose={() => setDeleting(null)}><div className="studio-form"><p>Phiên <strong>{deleting.title}</strong> và nhật ký của nó sẽ bị xóa. Video và mốc ghim vẫn được giữ.</p><div className="studio-form-actions"><button className="studio-button" onClick={() => setDeleting(null)}>Giữ lại</button><button className="studio-button danger-solid" disabled={!!busy} onClick={() => void act('delete', () => studioClient.deleteLiveSession(deleting.id), 'Đã xóa phiên.').then(() => setDeleting(null))}>Xóa phiên</button></div></div></Modal>}
    {obsOpen && obs && <ObsSettings status={obs} live={!!live} onClose={() => setObsOpen(false)} onChanged={next => { setObs(next) }} onNotice={onNotice} />}
  </>
}

function OverlayState({ status }: { status: LiveStatus }) {
  const overlay = status.overlay
  if (!overlay || !overlay.overlay_count || overlay.state === 'disabled' || overlay.state === 'idle') return null
  const source = `nguồn "${overlay.source_name}"`
  if (overlay.state === 'error') return <span className="studio-pin-state warn" title={overlay.error?.message}><AlertTriangle size={13} />Overlay chưa hiện: {overlay.error?.message ?? `kiểm tra ${source} trong OBS`}</span>
  if (overlay.state === 'loading') return <span className="studio-pin-state busy"><Layers size={13} />Đang nạp {overlay.overlay_count} overlay vào OBS…</span>
  const showing = overlay.active_overlays?.length ?? 0
  return <span className={`studio-pin-state${showing ? ' ok' : ''}`} title={`Hiện qua ${source}`}><Layers size={13} />{showing ? `Đang hiện ${showing} overlay` : `${overlay.overlay_count} overlay sẵn sàng`}</span>
}

function PinState({ status }: { status: LiveStatus }) {
  const pins = status.pins
  const name = (id: string | null) => (id && pins.product_names[id]) || 'sản phẩm'
  if (pins.real && !pins.chrome_connected) return <span className="studio-pin-state warn"><AlertTriangle size={13} />Chưa kết nối Chrome TikTok: vào Sản phẩm → Kết nối để ghim được</span>
  if (pins.real && pins.live !== true) return <span className="studio-pin-state warn">Chờ TikTok xác nhận đang LIVE trên Console rồi mới ghim</span>
  if (pins.in_flight) return <span className="studio-pin-state busy"><span className="studio-typing"><i /><i /><i /></span>{pins.in_flight.action === 'PIN_PRODUCT' ? `Đang ghim ${name(pins.in_flight.product_id)}` : 'Đang bỏ ghim'}…</span>
  if (pins.last_error && pins.desired_product_id !== pins.actual_product_id) return <span className="studio-pin-state warn" title={pins.last_error}>Ghim chưa được: {pins.last_error}</span>
  if (pins.actual_product_id) return <span className="studio-pin-state ok"><Check size={13} />{pins.real ? 'Đang ghim' : 'Chạy thử · sẽ ghim'} {name(pins.actual_product_id)}</span>
  if (pins.desired_product_id) return <span className="studio-pin-state busy">Sắp ghim {name(pins.desired_product_id)}</span>
  return <span className="studio-pin-state off"><PinOff size={13} />Chưa tới mốc ghim nào</span>
}

function ControlRoom({ status, session, mediaById, busy, disabled, onControl, onSeek, onPins }: {
  status: LiveStatus; session: LiveSession; mediaById: Map<string, Media>; busy: string; disabled: boolean
  onControl: (action: 'pause' | 'resume' | 'next' | 'previous' | 'stop') => void; onSeek: (ms: number) => void; onPins: (real: boolean) => void
}) {
  const [confirmStop, setConfirmStop] = useState(false)
  const [confirmReal, setConfirmReal] = useState(false)
  const current = status.media_id ? mediaById.get(status.media_id) : undefined
  const length = status.duration_ms || (current?.duration_seconds ?? 0) * 1000 || 1
  const cursor = status.cursor_ms ?? 0
  const locked = disabled || !!busy
  const next = status.markers.find(marker => marker.at_ms > cursor)
  return <section className="studio-control-room" aria-label="Phòng điều khiển LIVE">
    <div className="studio-now-playing">
      <div className="studio-now-cover">{current?.thumbnail_url ? <img src={current.thumbnail_url} alt="" /> : <Film size={34} />}<span className="studio-live-badge"><i />{status.paused ? 'Tạm dừng' : 'Đang phát'}</span></div>
      <div className="studio-grow studio-now-info">
        <small>{session.title} · đã phát {duration(status.session_seconds)}{status.loop_iteration ? ` · lượt ${status.loop_iteration + 1}` : ''}</small>
        <h2 title={current?.original_name}>{current?.original_name ?? 'Video không còn trong thư viện'}</h2>
        <p className="studio-muted"><Film size={13} />Video {status.index + 1}/{status.queue.length}{session.video_loop && <><Repeat size={12} />lặp lại</>}</p>
        <div className="studio-progress studio-progress-markers" role="slider" tabIndex={0} aria-label="Vị trí video" aria-valuemin={0} aria-valuemax={Math.round(length)} aria-valuenow={Math.round(cursor)}
          onClick={event => { if (locked) return; const box = event.currentTarget.getBoundingClientRect(); onSeek((event.clientX - box.left) / box.width * length) }}>
          <span style={{ width: `${Math.min(100, cursor / length * 100)}%` }} />
          {status.markers.map((marker, index) => <i key={index} className={marker.action === 'PIN_PRODUCT' ? 'pin' : 'unpin'} style={{ left: `${Math.min(100, marker.at_ms / length * 100)}%` }} title={`${clock(marker.at_ms)} · ${marker.action === 'PIN_PRODUCT' ? `Ghim ${marker.product_name ?? ''}` : 'Bỏ ghim'}`} />)}
        </div>
        <div className="studio-progress-time"><span>{clock(cursor)}</span><span>{next ? `${next.action === 'PIN_PRODUCT' ? `Ghim ${next.product_name ?? ''}` : 'Bỏ ghim'} sau ${clock(next.at_ms - cursor)}` : status.markers.length ? 'Hết mốc trong video này' : 'Video chưa có mốc ghim'}</span><span>{clock(length)}</span></div>
        {!status.obs_connected && <ErrorNotice message={`OBS không phản hồi${status.obs_error ? `: ${status.obs_error}` : ''}. Engine sẽ tự thử lại.`} />}
      </div>
      <div className="studio-transport">
        <button className="studio-round" disabled={locked} aria-label="Video trước" onClick={() => onControl('previous')}><SkipBack size={18} /></button>
        <button className="studio-round main" disabled={locked} aria-label={status.paused ? 'Phát tiếp' : 'Tạm dừng'} onClick={() => onControl(status.paused ? 'resume' : 'pause')}>{status.paused ? <Play size={22} fill="currentColor" /> : <Pause size={22} fill="currentColor" />}</button>
        <button className="studio-round" disabled={locked} aria-label="Video kế tiếp" onClick={() => onControl('next')}><SkipForward size={18} /></button>
        <button className="studio-button danger-solid studio-stop" disabled={locked} onClick={() => setConfirmStop(true)}><CircleStop size={16} />Dừng phiên</button>
      </div>
    </div>
    <div className="studio-automation">
      <label className="studio-switch dark"><input type="checkbox" role="switch" checked={status.pins.real} disabled={locked} onChange={event => event.target.checked ? setConfirmReal(true) : onPins(false)} /><span aria-hidden="true" /><small>{status.pins.real ? <><Pin size={13} />Ghim thật trên TikTok</> : <><FlaskConical size={13} />Chạy thử ghim</>}</small></label>
      <PinState status={status} />
      <OverlayState status={status} />
    </div>
    <div className="studio-lineup">{status.queue.map((id, index) => {
      const item = mediaById.get(id)
      return <article key={id} className={`studio-lineup-item${index === status.index ? ' current' : ''}`}>
        <span className="studio-lineup-thumb">{item?.thumbnail_url ? <img src={item.thumbnail_url} alt="" /> : <Film size={16} />}</span>
        <div className="studio-grow"><strong title={item?.original_name}>{item?.original_name ?? 'Đã xóa'}</strong><small>{index === status.index ? 'Đang phát' : `${item?.marker_count ?? 0} mốc ghim`}</small></div>
      </article>
    })}</div>
    {status.events.length > 0 && <details className="studio-live-log"><summary>Nhật ký phiên ({status.events.length})</summary><ul>{status.events.map(event => <li key={event.id}><time>{new Date(event.created_at).toLocaleTimeString('vi-VN')}</time><span>{eventLabels[event.kind] ?? event.kind}</span>{event.product_id && <em>{status.pins.product_names[event.product_id] ?? ''}</em>}{event.detail?.dry_run === true && <em>(chạy thử)</em>}</li>)}</ul></details>}
    {confirmStop && <Modal title="Dừng phiên LIVE?" onClose={() => setConfirmStop(false)}><div className="studio-form"><p>Video trong OBS sẽ dừng ngay và Studio ngừng ghim. TikTok LIVE và OBS stream không bị tắt; bạn tự kết thúc LIVE trên TikTok nếu cần.</p><div className="studio-form-actions"><button className="studio-button" onClick={() => setConfirmStop(false)}>Tiếp tục phát</button><button className="studio-button danger-solid" onClick={() => { setConfirmStop(false); onControl('stop') }}>Dừng phiên</button></div></div></Modal>}
    {confirmReal && <Modal title="Bật ghim thật trên TikTok?" onClose={() => setConfirmReal(false)}><div className="studio-form"><p>Studio sẽ bấm ghim sản phẩm trên TikTok LIVE Console theo mốc của video, trên tài khoản đã đồng bộ. Chỉ ghim khi Console xác nhận đang LIVE.</p><p className="studio-muted">Cần Chrome TikTok đang kết nối (trang Sản phẩm → Kết nối).</p><div className="studio-form-actions"><button className="studio-button" onClick={() => setConfirmReal(false)}>Để chạy thử</button><button className="studio-button primary" onClick={() => { setConfirmReal(false); onPins(true) }}>Ghim thật</button></div></div></Modal>}
  </section>
}

function SessionEditor({ session, media, onClose, onSaved }: { session: LiveSession | null; media: Media[]; onClose: () => void; onSaved: (message: string) => Promise<void> }) {
  const [title, setTitle] = useState(session?.title ?? `Phiên ${new Date().toLocaleDateString('vi-VN')}`)
  const [selected, setSelected] = useState<string[]>(session?.video_ids ?? [])
  const [order, setOrder] = useState<VideoOrder>(session?.video_order ?? 'sequential')
  const [loop, setLoop] = useState(session?.video_loop ?? true)
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const byId = useMemo(() => new Map(media.map(item => [item.id, item])), [media])
  const available = useMemo(() => media.filter(item => item.status === 'ready' && !selected.includes(item.id) && item.original_name.toLocaleLowerCase('vi').includes(query.trim().toLocaleLowerCase('vi')))
    .sort((a, b) => (b.marker_count ? 1 : 0) - (a.marker_count ? 1 : 0) || a.original_name.localeCompare(b.original_name, 'vi')), [media, selected, query])
  const move = (index: number, delta: number) => setSelected(current => { const next = [...current]; const [item] = next.splice(index, 1); next.splice(index + delta, 0, item); return next })
  const live = session?.status === 'live'
  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    if (!selected.length) { setError('Chọn ít nhất một video cho phiên.'); return }
    setBusy(true)
    setError('')
    try {
      if (session) await studioClient.updateLiveSession(session.id, live ? { title: title.trim(), video_loop: loop } : { title: title.trim(), video_ids: selected, video_order: order, video_loop: loop })
      else await studioClient.createLiveSession({ title: title.trim(), video_ids: selected, video_order: order, video_loop: loop })
      await onSaved(session ? 'Đã lưu phiên.' : 'Đã tạo phiên LIVE.')
    } catch (cause) { setError(errorMessage(cause)); setBusy(false) }
  }
  return <Modal title={session ? 'Sửa phiên LIVE' : 'Tạo phiên LIVE'} onClose={() => { if (!busy) onClose() }} wide><form className="studio-form" onSubmit={event => void save(event)}>
    <label className="studio-field">Tên phiên<input data-autofocus autoFocus value={title} maxLength={200} disabled={busy} onChange={event => setTitle(event.target.value)} /></label>
    {live && <div className="studio-callout warning"><Radio size={16} /><span>Phiên đang phát: chỉ đổi được tên và lặp lại. Dừng phiên để đổi video hoặc thứ tự.</span></div>}
    <div className="studio-picker">
      <div className="studio-picker-col">
        <div className="studio-picker-head"><strong>Thư viện video</strong><input aria-label="Tìm video" placeholder="Tìm…" value={query} onChange={event => setQuery(event.target.value)} /></div>
        <ul>{available.map(item => <li key={item.id}><button type="button" disabled={busy || live} onClick={() => setSelected(current => [...current, item.id])}><span className="studio-lineup-thumb">{item.thumbnail_url ? <img src={item.thumbnail_url} alt="" /> : <Film size={14} />}</span><span className="studio-grow">{item.original_name}<small className={item.marker_count ? '' : 'warn'}>{item.duration_seconds ? `${clock(item.duration_seconds * 1000)} · ` : ''}{item.marker_count ? `${item.marker_count} mốc ghim` : 'Chưa có mốc ghim'}</small></span><Plus size={15} /></button></li>)}
          {!available.length && <li className="studio-picker-empty">Không còn video phù hợp.</li>}</ul>
      </div>
      <div className="studio-picker-col selected">
        <div className="studio-picker-head"><strong>Thứ tự phát ({selected.length})</strong></div>
        <ol>{selected.map((id, index) => { const item = byId.get(id); return <li key={id}><span className="studio-order">{index + 1}</span><span className="studio-grow">{item?.original_name ?? 'Đã xóa'}<small className={item?.marker_count ? '' : 'warn'}>{item?.marker_count ? `${item.marker_count} mốc ghim` : 'Chưa có mốc ghim: sẽ không ghim gì'}</small></span>
          <button type="button" className="studio-button ghost icon-only" disabled={busy || live || index === 0} aria-label="Lên trên" onClick={() => move(index, -1)}><ArrowUp size={14} /></button>
          <button type="button" className="studio-button ghost icon-only" disabled={busy || live || index === selected.length - 1} aria-label="Xuống dưới" onClick={() => move(index, 1)}><ArrowDown size={14} /></button>
          <button type="button" className="studio-button ghost icon-only danger" disabled={busy || live} aria-label="Bỏ khỏi phiên" onClick={() => setSelected(current => current.filter(value => value !== id))}><X size={14} /></button></li> })}
          {!selected.length && <li className="studio-picker-empty">Bấm vào video bên trái để thêm vào phiên.</li>}</ol>
      </div>
    </div>
    <div className="studio-policy">
      <div className="studio-field">Thứ tự phát<div className="studio-segmented wide" role="radiogroup" aria-label="Thứ tự phát">{orders.map(([value, text]) => <button type="button" role="radio" key={value} aria-checked={order === value} className={order === value ? 'active' : ''} disabled={live} onClick={() => setOrder(value)}>{text}</button>)}</div></div>
      <label className="studio-switch studio-switch-field"><input type="checkbox" role="switch" checked={loop} onChange={event => setLoop(event.target.checked)} /><span aria-hidden="true" /><small>Phát lại từ đầu khi hết danh sách</small></label>
    </div>
    {error && <ErrorNotice message={error} />}
    <div className="studio-form-actions"><button type="button" className="studio-button" disabled={busy} onClick={onClose}>Hủy</button><button className="studio-button primary" disabled={busy || !title.trim()}>{busy ? 'Đang lưu…' : session ? 'Lưu phiên' : 'Tạo phiên'}</button></div>
  </form></Modal>
}

function ObsSettings({ status, live, onClose, onChanged, onNotice }: { status: ObsStatus; live: boolean; onClose: () => void; onChanged: (status: ObsStatus) => void; onNotice: (message: string) => void }) {
  const [host, setHost] = useState(status.host)
  const [port, setPort] = useState(String(status.port))
  const [password, setPassword] = useState('')
  const [mediaInput, setMediaInput] = useState(status.media_input)
  const [dual, setDual] = useState(status.dual_mode)
  const [sourceA, setSourceA] = useState(status.source_a)
  const [sourceB, setSourceB] = useState(status.source_b)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [current, setCurrent] = useState(status)
  const mounted = useRef(true)
  useEffect(() => () => { mounted.current = false }, [])
  const run = async (key: string, action: () => Promise<ObsStatus>, message: string) => {
    setBusy(key)
    setError('')
    try { const next = await action(); setCurrent(next); onChanged(next); onNotice(message) }
    catch (cause) { if (mounted.current) setError(errorMessage(cause)) }
    finally { if (mounted.current) setBusy('') }
  }
  const save = (event: FormEvent) => {
    event.preventDefault()
    void run('save', () => studioClient.obsConfigure({ host: host.trim(), port: Number(port), media_input: mediaInput.trim(), dual_mode: dual, source_a: sourceA.trim(), source_b: sourceB.trim(), ...(password ? { password } : {}) }).then(async next => { setPassword(''); try { return await studioClient.obsConnect() } catch { return next } }), 'Đã lưu cài đặt OBS.')
  }
  return <Modal title="Kết nối OBS" onClose={() => { if (!busy) onClose() }} wide><form className="studio-form" onSubmit={save}>
    <div className={`studio-obs-state ${current.connected ? 'ok' : 'off'}`}><MonitorPlay size={20} /><div className="studio-grow"><strong>{current.connected ? `Đã kết nối OBS ${current.obs_version ?? ''}` : 'Chưa kết nối OBS'}</strong><span>{current.connected ? (current.media_input_ok === false ? `Không thấy nguồn “${current.media_input}” trong OBS. Hãy tạo Media Source đúng tên này.` : 'OBS WebSocket đang trả lời.') : (current.error?.message ?? 'Bật OBS → Tools → WebSocket Server Settings, rồi bấm Kết nối.')}</span></div>
      {current.connected ? <button type="button" className="studio-button" disabled={!!busy || live} onClick={() => void run('disconnect', studioClient.obsDisconnect, 'Đã ngắt OBS.')}>Ngắt</button> : <button type="button" className="studio-button primary" disabled={!!busy} onClick={() => void run('connect', studioClient.obsConnect, 'Đã kết nối OBS.')}>{busy === 'connect' ? 'Đang kết nối…' : 'Kết nối'}</button>}</div>
    {live && <div className="studio-callout warning"><Radio size={16} /><span>Đang có phiên phát. Dừng phiên trước khi đổi cài đặt OBS.</span></div>}
    <div className="studio-form-row">
      <label className="studio-field">Địa chỉ<input value={host} disabled={live || !!busy} onChange={event => setHost(event.target.value)} /></label>
      <label className="studio-field narrow">Cổng<input type="number" min={1} max={65535} value={port} disabled={live || !!busy} onChange={event => setPort(event.target.value)} /></label>
    </div>
    <label className="studio-field">Mật khẩu WebSocket<input type="password" autoComplete="off" value={password} disabled={live || !!busy} placeholder={current.password_set ? 'Đã lưu (để trống nếu không đổi)' : 'Để trống nếu OBS không đặt mật khẩu'} onChange={event => setPassword(event.target.value)} /></label>
    <label className="studio-field">Tên Media Source phát video<input value={mediaInput} maxLength={200} disabled={live || !!busy} onChange={event => setMediaInput(event.target.value)} /></label>
    <label className="studio-switch studio-switch-field"><input type="checkbox" role="switch" checked={dual} disabled={live || !!busy} onChange={event => setDual(event.target.checked)} /><span aria-hidden="true" /><small>Chuyển video mượt bằng hai nguồn A/B (không thấy khung đen giữa hai video)</small></label>
    {dual && <div className="studio-form-row">
      <label className="studio-field">Nguồn A<input value={sourceA} placeholder={`${mediaInput} A`} disabled={live || !!busy} onChange={event => setSourceA(event.target.value)} /></label>
      <label className="studio-field">Nguồn B<input value={sourceB} placeholder={`${mediaInput} B`} disabled={live || !!busy} onChange={event => setSourceB(event.target.value)} /></label>
    </div>}
    <p className="studio-field-hint left">Studio chỉ điều khiển Media Source trong scene; không bật/tắt stream hay ghi hình của OBS.</p>
    {error && <ErrorNotice message={error} />}
    <div className="studio-form-actions"><button type="button" className="studio-button" disabled={!!busy} onClick={onClose}>Đóng</button><button className="studio-button primary" disabled={live || !!busy || !host.trim() || !mediaInput.trim()}>{busy === 'save' ? 'Đang lưu…' : 'Lưu & kết nối'}</button></div>
  </form></Modal>
}
