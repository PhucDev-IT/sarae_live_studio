import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { ArrowDown, ArrowUp, CircleStop, Clock3, Film, ListVideo, MonitorPlay, Package, Pause, Pencil, Play, Plus, Radio, Settings2, SkipBack, SkipForward, Trash2, X } from 'lucide-react'
import { studioClient, type LiveSession, type LiveStatus, type Media, type ObsStatus, type PlaybackPolicy, type Product } from '../../services/studio'
import { ReplyDesk } from './ReplyDesk'
import { EmptyState, ErrorNotice, Modal, errorMessage } from './ui'

type Props = { products: Product[]; media: Media[]; disabled: boolean; visible: boolean; onNotice: (message: string) => void }
const defaultPolicy: PlaybackPolicy = { video_order: 'sequential', product_rotation: 'off', rotate_after_videos: 3, rotate_after_minutes: null, switch_mode: 'after_current', auto: true }
const when = new Intl.DateTimeFormat('vi-VN', { dateStyle: 'short', timeStyle: 'short' })
const endReasons: Record<string, string> = { operator: 'Bạn đã dừng', core_restart: 'Gián đoạn do ứng dụng khởi động lại', core_stopped: 'Ứng dụng đã đóng', playback_ended: 'Phát lỗi nhiều lần nên đã dừng' }
const eventLabels: Record<string, string> = { STARTED: 'Bắt đầu phát', STOPPED: 'Đã dừng', PAUSE: 'Tạm dừng', RESUME: 'Phát tiếp', SWITCH_REQUESTED: 'Yêu cầu chuyển sản phẩm', PRODUCT_SWITCHED: 'Đã chuyển sản phẩm', PLAYBACK_ENDED: 'Phát kết thúc', ERROR: 'Lỗi phát video' }

function clock(ms: number | null | undefined) {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return '--:--'
  const total = Math.max(0, Math.floor(ms / 1000))
  return `${Math.floor(total / 60).toString().padStart(2, '0')}:${(total % 60).toString().padStart(2, '0')}`
}
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
  const productById = useMemo(() => new Map(products.map(product => [product.id, product])), [products])
  const videoCount = useMemo(() => {
    const result = new Map<string, number>()
    for (const item of media) if (item.product_id && item.status === 'ready') result.set(item.product_id, (result.get(item.product_id) ?? 0) + 1)
    return result
  }, [media])
  const mediaNames = useMemo(() => new Map(media.map(item => [item.id, item.original_name])), [media])
  const loadAll = useCallback(async () => {
    try {
      const [nextSessions, nextStatus, nextObs] = await Promise.all([studioClient.listLiveSessions(), studioClient.liveStatus(), studioClient.obsStatus()])
      setSessions(nextSessions); setStatus(nextStatus); setObs(nextObs)
    } catch (cause) { setError(errorMessage(cause)) }
  }, [])
  useEffect(() => { if (visible && !disabled) void loadAll() }, [visible, disabled, loadAll, products, media])
  // While on air, poll the player (cursor/state change every second without a mutation event).
  useEffect(() => {
    if (!visible || disabled || !status?.active) return
    const timer = window.setInterval(() => { void studioClient.liveStatus().then(setStatus).catch(() => {}) }, 1500)
    return () => window.clearInterval(timer)
  }, [visible, disabled, status?.active])

  const act = async (key: string, action: () => Promise<LiveStatus | unknown>, message?: string) => {
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
  const removeSession = async () => {
    if (!deleting) return
    await act('delete', () => studioClient.deleteLiveSession(deleting.id), 'Đã xóa phiên.')
    setDeleting(null)
  }

  return <>
    <div className="studio-page-heading">
      <div><h1>Phiên LIVE</h1><p>Phát lại video sản phẩm qua OBS; chuyển sản phẩm ngay hoặc sau video đang phát.</p></div>
      <div className="studio-actions">
        <button className={`studio-obs-chip ${obs?.connected ? 'ok' : 'off'}`} onClick={() => setObsOpen(true)} title="Cài đặt OBS"><MonitorPlay size={16} />{obs?.connected ? `OBS ${obs.obs_version ?? ''}`.trim() : 'OBS chưa kết nối'}<Settings2 size={14} /></button>
        <button className="studio-button primary" disabled={disabled} onClick={() => setEditing('new')}><Plus size={17} />Tạo phiên</button>
      </div>
    </div>
    {error && <ErrorNotice message={error} />}
    {live && live.session && <ControlRoom status={live} session={live.session} productById={productById} videoCount={videoCount} mediaNames={mediaNames} busy={busy} disabled={disabled}
      onSwitch={(productId, mode) => act('switch:' + productId + mode, () => studioClient.liveSwitch(productId, mode), mode === 'immediate' ? 'Đã chuyển sản phẩm.' : 'Sẽ chuyển sau video hiện tại.')}
      onControl={action => act(action, () => studioClient.liveControl(action), action === 'stop' ? 'Đã dừng phiên LIVE.' : undefined)} />}
    <ReplyDesk products={products} disabled={disabled} visible={visible} onNotice={onNotice} />
    <section className="studio-card">
      <header className="studio-card-heading"><ListVideo size={19} /><div><h2>Danh sách phiên</h2><p>Mỗi phiên là một danh sách sản phẩm theo thứ tự cùng cách phát video.</p></div></header>
      {!sessions.length ? <EmptyState icon={<Radio size={30} />} title="Chưa có phiên LIVE nào" description="Tạo phiên, chọn các sản phẩm đã có video, rồi bấm Phát khi OBS đã sẵn sàng." action={<button className="studio-button primary" disabled={disabled} onClick={() => setEditing('new')}><Plus size={16} />Tạo phiên đầu tiên</button>} />
        : <ul className="studio-session-list">{sessions.map(session => {
          const ready = session.product_ids.filter(id => videoCount.get(id)).length
          const isLive = session.status === 'live'
          return <li key={session.id} className={isLive ? 'live' : ''}>
            <div className="studio-session-thumbs" aria-hidden="true">{session.product_ids.slice(0, 3).map(id => { const product = productById.get(id); return <span key={id}>{product?.thumbnail_url ? <img src={product.thumbnail_url} alt="" /> : <Package size={14} />}</span> })}</div>
            <div className="studio-grow">
              <strong>{session.title}</strong>
              <small>{session.product_ids.length} sản phẩm · {ready} có video{session.started_at ? ` · lần phát gần nhất ${when.format(new Date(session.started_at))}` : ''}{session.status === 'ended' && session.end_reason ? ` · ${endReasons[session.end_reason] ?? session.end_reason}` : ''}</small>
            </div>
            {isLive ? <span className="studio-live-badge"><i />Đang phát</span> : <span className={`studio-tag ${session.status === 'ended' ? 'neutral' : 'honey'}`}>{session.status === 'ended' ? 'Đã kết thúc' : 'Nháp'}</span>}
            <div className="studio-actions">
              {!isLive && <button className="studio-button" disabled={disabled || !!busy || !!live || !ready} title={!ready ? 'Chưa có sản phẩm nào có video' : live ? 'Đang có phiên khác phát' : 'Bắt đầu phát'} onClick={() => void act('start:' + session.id, () => studioClient.liveStart(session.id), 'Đã bắt đầu phát phiên LIVE.')}><Play size={15} fill="currentColor" />{busy === 'start:' + session.id ? 'Đang mở…' : 'Phát'}</button>}
              <button className="studio-button ghost icon-only" disabled={disabled} aria-label={`Sửa ${session.title}`} title="Sửa phiên" onClick={() => setEditing(session)}><Pencil size={16} /></button>
              <button className="studio-button ghost icon-only danger" disabled={disabled || isLive} aria-label={`Xóa ${session.title}`} title={isLive ? 'Dừng phiên trước khi xóa' : 'Xóa phiên'} onClick={() => setDeleting(session)}><Trash2 size={16} /></button>
            </div>
          </li>
        })}</ul>}
    </section>
    {editing && <SessionEditor session={editing === 'new' ? null : editing} products={products} videoCount={videoCount} onClose={() => setEditing(null)} onSaved={async message => { setEditing(null); onNotice(message); await loadAll() }} />}
    {deleting && <Modal title="Xóa phiên LIVE?" onClose={() => setDeleting(null)}><div className="studio-form"><p>Phiên <strong>{deleting.title}</strong> và nhật ký của nó sẽ bị xóa. Sản phẩm và video vẫn được giữ.</p><div className="studio-form-actions"><button className="studio-button" onClick={() => setDeleting(null)}>Giữ lại</button><button className="studio-button danger-solid" disabled={!!busy} onClick={() => void removeSession()}>Xóa phiên</button></div></div></Modal>}
    {obsOpen && obs && <ObsSettings status={obs} live={!!live} onClose={() => setObsOpen(false)} onChanged={next => { setObs(next) }} onNotice={onNotice} />}
  </>
}

function ControlRoom({ status, session, productById, videoCount, mediaNames, busy, disabled, onSwitch, onControl }: {
  status: LiveStatus; session: LiveSession; productById: Map<string, Product>; videoCount: Map<string, number>; mediaNames: Map<string, string>; busy: string; disabled: boolean
  onSwitch: (productId: string, mode: 'immediate' | 'after_current') => void; onControl: (action: 'pause' | 'resume' | 'next' | 'previous' | 'cancel_pending' | 'stop') => void
}) {
  const [confirmStop, setConfirmStop] = useState(false)
  const current = status.product_id ? productById.get(status.product_id) : undefined
  const pending = status.pending_product_id ? productById.get(status.pending_product_id) : undefined
  const progress = status.duration_ms && status.cursor_ms !== null ? Math.min(100, Math.max(0, status.cursor_ms / status.duration_ms * 100)) : 0
  const locked = disabled || !!busy
  return <section className="studio-control-room" aria-label="Phòng điều khiển LIVE">
    <div className="studio-now-playing">
      <div className="studio-now-cover">{current?.thumbnail_url ? <img src={current.thumbnail_url} alt="" /> : <Package size={34} />}<span className="studio-live-badge"><i />{status.paused ? 'Tạm dừng' : 'Đang phát'}</span></div>
      <div className="studio-grow studio-now-info">
        <small>{session.title} · đã phát {duration(status.session_seconds)}</small>
        <h2>{current?.name ?? 'Sản phẩm không còn trong danh mục'}</h2>
        <p className="studio-muted"><Film size={13} />Video {status.index + 1}/{status.queue.length}{status.media_id ? ` · ${mediaNames.get(status.media_id) ?? ''}` : ''}</p>
        <div className="studio-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress)} aria-label="Tiến độ video"><span style={{ width: `${progress}%` }} /></div>
        <div className="studio-progress-time"><span>{clock(status.cursor_ms)}</span><span>{clock(status.duration_ms)}</span></div>
        {pending && <div className="studio-pending"><Clock3 size={14} />Sau video này sẽ chuyển sang <strong>{pending.name}</strong><button className="studio-button ghost" disabled={locked} onClick={() => onControl('cancel_pending')}><X size={14} />Hủy</button></div>}
        {!status.obs_connected && <ErrorNotice message={`OBS không phản hồi${status.obs_error ? `: ${status.obs_error}` : ''}. Engine sẽ tự thử lại.`} />}
      </div>
      <div className="studio-transport">
        <button className="studio-round" disabled={locked} aria-label="Video trước" onClick={() => onControl('previous')}><SkipBack size={18} /></button>
        <button className="studio-round main" disabled={locked} aria-label={status.paused ? 'Phát tiếp' : 'Tạm dừng'} onClick={() => onControl(status.paused ? 'resume' : 'pause')}>{status.paused ? <Play size={22} fill="currentColor" /> : <Pause size={22} fill="currentColor" />}</button>
        <button className="studio-round" disabled={locked} aria-label="Video kế tiếp" onClick={() => onControl('next')}><SkipForward size={18} /></button>
        <button className="studio-button danger-solid studio-stop" disabled={locked} onClick={() => setConfirmStop(true)}><CircleStop size={16} />Dừng phiên</button>
      </div>
    </div>
    <div className="studio-lineup">{session.product_ids.map(id => {
      const product = productById.get(id)
      const count = videoCount.get(id) ?? 0
      const isCurrent = id === status.product_id
      const isPending = id === status.pending_product_id
      return <article key={id} className={`studio-lineup-item${isCurrent ? ' current' : ''}${isPending ? ' pending' : ''}${!count ? ' empty' : ''}`}>
        <span className="studio-lineup-thumb">{product?.thumbnail_url ? <img src={product.thumbnail_url} alt="" /> : <Package size={16} />}</span>
        <div className="studio-grow"><strong title={product?.name}>{product?.name ?? 'Đã xóa'}</strong><small>{isCurrent ? 'Đang phát' : isPending ? 'Chờ chuyển' : `${count} video`}</small></div>
        {!isCurrent && count > 0 && <div className="studio-lineup-actions"><button className="studio-button ghost" disabled={locked} title="Chuyển khi video hiện tại kết thúc" onClick={() => onSwitch(id, 'after_current')}>Sau video này</button><button className="studio-button" disabled={locked} onClick={() => onSwitch(id, 'immediate')}>Chuyển ngay</button></div>}
      </article>
    })}</div>
    {status.events.length > 0 && <details className="studio-live-log"><summary>Nhật ký phiên ({status.events.length})</summary><ul>{status.events.map(event => <li key={event.id}><time>{new Date(event.created_at).toLocaleTimeString('vi-VN')}</time><span>{eventLabels[event.kind] ?? event.kind}</span>{event.product_id && <em>{productById.get(event.product_id)?.name}</em>}</li>)}</ul></details>}
    {confirmStop && <Modal title="Dừng phiên LIVE?" onClose={() => setConfirmStop(false)}><div className="studio-form"><p>Video trong OBS sẽ dừng ngay. TikTok LIVE và OBS stream không bị tắt; bạn tự kết thúc LIVE trên TikTok nếu cần.</p><div className="studio-form-actions"><button className="studio-button" onClick={() => setConfirmStop(false)}>Tiếp tục phát</button><button className="studio-button danger-solid" onClick={() => { setConfirmStop(false); onControl('stop') }}>Dừng phiên</button></div></div></Modal>}
  </section>
}

function SessionEditor({ session, products, videoCount, onClose, onSaved }: { session: LiveSession | null; products: Product[]; videoCount: Map<string, number>; onClose: () => void; onSaved: (message: string) => Promise<void> }) {
  const [title, setTitle] = useState(session?.title ?? `Phiên ${new Date().toLocaleDateString('vi-VN')}`)
  const [selected, setSelected] = useState<string[]>(session?.product_ids ?? [])
  const [policy, setPolicy] = useState<PlaybackPolicy>({ ...defaultPolicy, ...(session?.policy ?? {}) })
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const byId = useMemo(() => new Map(products.map(product => [product.id, product])), [products])
  const available = useMemo(() => products.filter(product => !selected.includes(product.id) && product.name.toLocaleLowerCase('vi').includes(query.trim().toLocaleLowerCase('vi')))
    .sort((a, b) => (videoCount.get(b.id) ? 1 : 0) - (videoCount.get(a.id) ? 1 : 0) || a.name.localeCompare(b.name, 'vi')), [products, selected, query, videoCount])
  const move = (index: number, delta: number) => setSelected(current => { const next = [...current]; const [item] = next.splice(index, 1); next.splice(index + delta, 0, item); return next })
  const rotating = policy.product_rotation !== 'off'
  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    if (!selected.length) { setError('Chọn ít nhất một sản phẩm cho phiên.'); return }
    if (rotating && !policy.rotate_after_videos && !policy.rotate_after_minutes) { setError('Khi tự đổi sản phẩm, hãy đặt số video hoặc số phút cho mỗi sản phẩm.'); return }
    setBusy(true)
    setError('')
    try {
      const input = { title: title.trim(), product_ids: selected, policy }
      if (session) await studioClient.updateLiveSession(session.id, input)
      else await studioClient.createLiveSession(input)
      await onSaved(session ? (session.status === 'live' ? 'Đã lưu, áp dụng ngay cho phiên đang phát.' : 'Đã lưu phiên.') : 'Đã tạo phiên LIVE.')
    } catch (cause) { setError(errorMessage(cause)); setBusy(false) }
  }
  const segment = <K extends keyof PlaybackPolicy>(key: K, options: [PlaybackPolicy[K], string][], label: string) => <div className="studio-field">{label}<div className="studio-segmented wide" role="radiogroup" aria-label={label}>{options.map(([value, text]) => <button type="button" role="radio" key={String(value)} aria-checked={policy[key] === value} className={policy[key] === value ? 'active' : ''} onClick={() => setPolicy({ ...policy, [key]: value })}>{text}</button>)}</div></div>
  return <Modal title={session ? 'Sửa phiên LIVE' : 'Tạo phiên LIVE'} onClose={() => { if (!busy) onClose() }} wide><form className="studio-form" onSubmit={event => void save(event)}>
    <label className="studio-field">Tên phiên<input data-autofocus autoFocus value={title} maxLength={200} disabled={busy} onChange={event => setTitle(event.target.value)} /></label>
    <div className="studio-picker">
      <div className="studio-picker-col">
        <div className="studio-picker-head"><strong>Sản phẩm</strong><input aria-label="Tìm sản phẩm" placeholder="Tìm…" value={query} onChange={event => setQuery(event.target.value)} /></div>
        <ul>{available.map(product => { const count = videoCount.get(product.id) ?? 0; return <li key={product.id}><button type="button" disabled={busy} onClick={() => setSelected(current => [...current, product.id])}><span className="studio-lineup-thumb">{product.thumbnail_url ? <img src={product.thumbnail_url} alt="" /> : <Package size={14} />}</span><span className="studio-grow">{product.name}<small className={count ? '' : 'warn'}>{count ? `${count} video` : 'Chưa có video'}</small></span><Plus size={15} /></button></li> })}
          {!available.length && <li className="studio-picker-empty">Không còn sản phẩm phù hợp.</li>}</ul>
      </div>
      <div className="studio-picker-col selected">
        <div className="studio-picker-head"><strong>Thứ tự phát ({selected.length})</strong></div>
        <ol>{selected.map((id, index) => { const product = byId.get(id); const count = videoCount.get(id) ?? 0; return <li key={id}><span className="studio-order">{index + 1}</span><span className="studio-grow">{product?.name ?? 'Đã xóa'}<small className={count ? '' : 'warn'}>{count ? `${count} video` : 'Chưa có video, sẽ bị bỏ qua'}</small></span>
          <button type="button" className="studio-button ghost icon-only" disabled={busy || index === 0} aria-label="Lên trên" onClick={() => move(index, -1)}><ArrowUp size={14} /></button>
          <button type="button" className="studio-button ghost icon-only" disabled={busy || index === selected.length - 1} aria-label="Xuống dưới" onClick={() => move(index, 1)}><ArrowDown size={14} /></button>
          <button type="button" className="studio-button ghost icon-only danger" disabled={busy} aria-label="Bỏ khỏi phiên" onClick={() => setSelected(current => current.filter(item => item !== id))}><X size={14} /></button></li> })}
          {!selected.length && <li className="studio-picker-empty">Bấm vào sản phẩm bên trái để thêm vào phiên.</li>}</ol>
      </div>
    </div>
    <div className="studio-policy">
      {segment('video_order', [['sequential', 'Lần lượt'], ['random', 'Ngẫu nhiên'], ['shuffle', 'Xáo trộn']], 'Thứ tự video trong sản phẩm')}
      {segment('product_rotation', [['off', 'Tắt'], ['sequential', 'Lần lượt'], ['random', 'Ngẫu nhiên'], ['shuffle', 'Xáo trộn']], 'Tự đổi sản phẩm')}
      {rotating && <div className="studio-form-row">
        <label className="studio-field">Đổi sau số video<input type="number" min={1} max={50} value={policy.rotate_after_videos ?? ''} placeholder="Không dùng" onChange={event => setPolicy({ ...policy, rotate_after_videos: event.target.value ? Number(event.target.value) : null })} /></label>
        <label className="studio-field">hoặc sau số phút<input type="number" min={1} max={240} value={policy.rotate_after_minutes ?? ''} placeholder="Không dùng" onChange={event => setPolicy({ ...policy, rotate_after_minutes: event.target.value ? Number(event.target.value) : null })} /></label>
      </div>}
      {rotating && segment('switch_mode', [['after_current', 'Đợi hết video'], ['immediate', 'Đổi ngay khi đủ thời gian']], 'Khi đến lượt đổi')}
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
