import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import {
  Alert, Badge, Button, Card, Collapse, ConfirmModal, EmptyState, Input, Kbd, Modal, PasswordInput, SearchInput, Select, Switch, useToast,
} from './ui/core'
import {
  CalendarClock, CircleAlert, Clock, FastForward, Film, ListVideo, Package, Pause, Pencil, Play, Radio, RefreshCw, Settings2, SkipBack, SkipForward,
  MonitorPlay, Square, Undo2, Wifi, WifiOff,
} from 'lucide-react'
import { agentAction, apiRequest, durationLabel, errorText, listVideos, onAgent, type Media, type OwnerAgent } from './mediaApi'
import { sessionHref, sessionsApi, type LiveProduct, type LiveSession } from './liveSessionsApi'
import { WithAgent, useAgent } from './agentContext'
import VideoThumb from './VideoThumb'
import './media.css'
import './live-control.css'

type ObsSettings = { host:string; port:number; input_name:string; dual_mode:boolean; dual_sources?:string[]; has_password:boolean }
type ObsInput = { name:string; kind:string; in_scene:boolean }
type ObsInputs = { scene:string; inputs:ObsInput[] }
type LiveStatus = {
  connected:boolean; error?:string; obs_version?:string; media_state?:string; media_cursor?:number|null; media_duration?:number|null
  running:boolean; paused?:boolean; loop?:boolean; session_mode?:string; playlist_id:number|null; product_id?:number|null; pending_product_id?:number|null; session_id?:number|null
  queue_ids?:string[]; index:number; count:number; last_status:string; retry_count?:number; error_count?:number; transition_count?:number
  recovery_count?:number; session_seconds?:number; obs?:ObsSettings
}
type Playlist = { id:string; name:string; media_ids:string[]; loop:boolean }

const POLL_MS = 3000
const post = (body?:unknown):RequestInit => body === undefined ? {method:'POST'} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}
const clock = (ms?:number|null) => ms == null || ms < 0 ? '—' : durationLabel(ms / 1000) ?? '—'
const sessionLabel = (seconds = 0) => `${Math.floor(seconds / 3600)}h ${String(Math.floor(seconds % 3600 / 60)).padStart(2, '0')}m`
/** OBS media states, see obs-websocket ObsMediaInputAction / media_state. */
const MEDIA_STATE:Record<string,{label:string;tone:'success'|'warning'|'danger'|'default'|'info'}> = {
  OBS_MEDIA_STATE_PLAYING:{label:'Đang phát', tone:'success'},
  OBS_MEDIA_STATE_PAUSED:{label:'Tạm dừng', tone:'warning'},
  OBS_MEDIA_STATE_OPENING:{label:'Đang mở', tone:'info'},
  OBS_MEDIA_STATE_BUFFERING:{label:'Đang tải', tone:'info'},
  OBS_MEDIA_STATE_STOPPED:{label:'Đã dừng', tone:'default'},
  OBS_MEDIA_STATE_ENDED:{label:'Hết video', tone:'default'},
  OBS_MEDIA_STATE_ERROR:{label:'Lỗi', tone:'danger'},
  OBS_MEDIA_STATE_NONE:{label:'Trống', tone:'default'},
}
/** True when a key press belongs to a field or the seek slider rather than the global shortcuts. */
const isTyping = (target:EventTarget|null) => target instanceof HTMLElement && (target.isContentEditable || ['INPUT','TEXTAREA','SELECT'].includes(target.tagName) || target.getAttribute('role') === 'slider')

/**
 * Operator console for the Windows Agent this tab works with: the LIVE session running on it (product switching,
 * transport controls, OBS health), or one of that Agent's prepared sessions to start. Other Agents keep their own LIVE.
 */
export default function LiveControl(){
  return <WithAgent purpose="Điều khiển LIVE">{agent=><Console key={agent.id} agent={agent}/>}</WithAgent>
}

function Console({agent}:{agent:OwnerAgent}){
  const toast = useToast()
  const {agents, select} = useAgent()
  const [status,setStatus] = useState<LiveStatus|null>(null)
  const [receivedAt,setReceivedAt] = useState(0)
  const [now,setNow] = useState(()=>Date.now())
  const [products,setProducts] = useState<LiveProduct[]>([])
  const [playlists,setPlaylists] = useState<Playlist[]>([])
  const [videos,setVideos] = useState<Media[]>([])
  const [busy,setBusy] = useState(false)
  const [sessions,setSessions] = useState<LiveSession[]>([])
  const [target,setTarget] = useState<number|null>(null)
  const [pickSession,setPickSession] = useState<number|null>(null)
  const [startProduct,setStartProduct] = useState<number|null>(null)
  const [productQuery,setProductQuery] = useState('')
  const [confirmStop,setConfirmStop] = useState(false)

  const [obsOpen,setObsOpen] = useState(false)
  const [obsForm,setObsForm] = useState({host:'127.0.0.1', port:'4455', password:'', input_name:'Sarae Video', dual_mode:false, source_a:'', source_b:''})
  const [obsInputs,setObsInputs] = useState<ObsInputs|null>(null)
  const [inputsLoading,setInputsLoading] = useState(false)
  const [playlistId,setPlaylistId] = useState('')

  const applyStatus = (next:LiveStatus) => {setStatus(next);setReceivedAt(Date.now())}
  const agentId = agent.id
  const pollStatus = useCallback(async ()=>{try{applyStatus(await apiRequest<LiveStatus>(onAgent('/live/status', agentId)))}catch{/* keep last known status */}},[agentId])
  const loadAll = useCallback(async ()=>{
    const [s, p, l, v, ss] = await Promise.all([
      apiRequest<LiveStatus>(onAgent('/live/status', agentId)), sessionsApi.products(agentId),
      apiRequest<Playlist[]>(onAgent('/agent-playlists', agentId)), listVideos(agentId), sessionsApi.list(),
    ])
    applyStatus(s);setProducts(p);setPlaylists(l);setVideos(v);setSessions(ss)
  },[agentId])
  useEffect(()=>{
    void loadAll().catch(e=>toast.push({tone:'danger',title:'Không tải được dữ liệu LIVE',description:errorText(e, '')}))
    const poll = window.setInterval(()=>void pollStatus(), POLL_MS)
    const tick = window.setInterval(()=>setNow(Date.now()), 500)
    return ()=>{window.clearInterval(poll);window.clearInterval(tick)}
  },[loadAll,pollStatus,toast])

  // Session lineups change on the planner page and the session ends from here; reload when playback changes owner.
  useEffect(()=>{void sessionsApi.list().then(setSessions).catch(()=>{})},[status?.running,status?.session_id])

  const perform = async (request:()=>Promise<unknown>, success:string|null) => {
    setBusy(true)
    try{
      await request()
      if (success) toast.push({tone:'success',title:success})
      await pollStatus()
      return true
    }catch(e){toast.push({tone:'danger',title:'Không thực hiện được',description:errorText(e, '')});return false}
    finally{setBusy(false)}
  }
  /** A control on this Agent; resolves once the Agent confirmed it (the Backend may answer 202 while it is on its way). */
  const act = (path:string, init:RequestInit, success:string|null) => perform(()=>agentAction(agentId, onAgent(path, agentId), init), success)

  const videoById = useMemo(()=>new Map(videos.map(v=>[v.id,v])),[videos])
  const productById = useMemo(()=>new Map(products.map(p=>[p.id,p])),[products])
  const running = !!status?.running
  const productSession = running && status?.session_mode === 'product'
  const queue = status?.queue_ids ?? []
  const currentId = running ? queue[status?.index ?? 0] : undefined
  const current = currentId != null ? videoById.get(currentId) : undefined
  const liveProduct = status?.product_id != null ? productById.get(status.product_id) : undefined
  const pendingProduct = status?.pending_product_id != null ? productById.get(status.pending_product_id) : undefined
  const mediaState = status?.media_state ? MEDIA_STATE[status.media_state] : undefined

  // Interpolate the cursor between polls so the progress bar moves smoothly while OBS is playing.
  const duration = status?.media_duration ?? (current?.duration_seconds != null ? current.duration_seconds * 1000 : undefined)
  const playing = status?.media_state === 'OBS_MEDIA_STATE_PLAYING'
  const cursor = status?.media_cursor == null ? null : Math.min(status.media_cursor + (playing ? now - receivedAt : 0), duration ?? Infinity)

  // The running managed session. Its list is a prepared pool, not a fixed order: the operator pushes any product live,
  // and a catalog product outside the list is added to the session when it goes live.
  const activeSession = running && status?.session_id != null ? sessions.find(s=>s.id === status.session_id && s.agent_id === agentId) : undefined
  const lineup = activeSession?.product_ids ?? []
  const q = productQuery.trim().toLocaleLowerCase('vi')
  const matches = (p:LiveProduct) => !q || (p.name + ' ' + p.sku).toLocaleLowerCase('vi').includes(q)
  const inSession = lineup.map(id=>productById.get(id) ?? {id, shop_id:null, name:`Sản phẩm #${id}`, sku:'', video_count:0}).filter(matches)
  const outside = q ? products.filter(p=>!lineup.includes(p.id) && p.status !== 'INACTIVE' && matches(p)) : []
  const targetProduct = target != null && target !== status?.product_id ? productById.get(target) : undefined
  const targetOutside = !!targetProduct && !lineup.includes(targetProduct.id)

  // Nothing running: pick one of the prepared (DRAFT) sessions and the product to open with.
  // Only this Agent's sessions: a session always plays on the Agent it belongs to.
  const mine = sessions.filter(s=>s.agent_id === agentId)
  const drafts = mine.filter(s=>s.status === 'DRAFT')
  // RUNNING rows with no playback behind them (Agent, OBS or PC restarted mid-LIVE).
  const stale = running ? [] : mine.filter(s=>s.status === 'RUNNING')
  // Other PCs of the owner that are LIVE right now (switching shows their console instead).
  const elsewhere = agents.filter(a=>a.id !== agentId && a.playing)
  const offline = !agent.online
  const picked = drafts.find(s=>s.id === pickSession) ?? drafts[0]
  const pickedReady = picked ? picked.product_ids.filter(id=>productById.get(id)?.video_count) : []
  const startId = startProduct != null && pickedReady.includes(startProduct) ? startProduct : pickedReady[0]
  const startSession = (session:LiveSession, productId:number) => perform(()=>sessionsApi.start(session, productId), `Đã bắt đầu “${session.name}”`).then(ok=>{if (ok) void loadAll()})
  const switchProduct = (id:number, mode:'immediate'|'after_current') => perform(
    async ()=>{if (activeSession) await sessionsApi.switchTo(activeSession, id, mode)},
    mode === 'immediate' ? 'Đã chuyển sản phẩm' : 'Sẽ chuyển sau video hiện tại',
  ).then(ok=>{
    if (!ok) return
    setTarget(null);setProductQuery('')
    void sessionsApi.list().then(setSessions).catch(()=>{}) // the product may have been added to the session
  })
  const productRow = (p:LiveProduct, isOutside:boolean) => {
    const isLive = p.id === status?.product_id
    const isPending = p.id === status?.pending_product_id
    return <li key={p.id}><button type="button" className={(target === p.id && !isLive ? 'is-selected ' : '') + (isLive ? 'is-live' : '')} disabled={!p.video_count || isLive || busy} onClick={()=>setTarget(p.id)}>
      <span className="lc-p-index">{isLive ? <Radio size={13}/> : <Package size={13}/>}</span>
      <span className="lc-p-text"><strong>{p.name}</strong><small>{p.sku || '—'} · {p.video_count ? `${p.video_count} video` : 'chưa có video'}</small></span>
      {isLive ? <Badge tone="danger" dot>LIVE</Badge> : isPending ? <Badge tone="warning">Chờ</Badge> : isOutside ? <Badge>Ngoài DS</Badge> : null}
    </button></li>
  }
  const togglePause = () => act(status?.paused ? '/live/resume' : '/live/pause', post(), status?.paused ? 'Đã tiếp tục phát' : 'Đã tạm dừng')
  const next = () => act('/live/next', post(), 'Đã chuyển video tiếp theo')
  const previous = () => act('/live/previous', post(), 'Đã quay lại video trước')
  const seek = (ms:number) => {
    // Show the new position right away; the next poll confirms it from OBS.
    setStatus(prev=>prev ? {...prev, media_cursor:ms} : prev)
    setReceivedAt(Date.now())
    void act('/live/seek', post({milliseconds:Math.round(ms)}), null)
  }

  // Operator shortcuts; ignored while typing in a field or when a dialog is open.
  useEffect(()=>{
    const onKey = (e:KeyboardEvent) => {
      if (!running || busy || isTyping(e.target) || e.ctrlKey || e.metaKey || e.altKey || document.querySelector('.ui-modal__backdrop')) return
      if (e.key === ' ') {e.preventDefault();void togglePause()}
      else if (e.key === 'ArrowRight' || e.key.toLowerCase() === 'n') {e.preventDefault();void next()}
      else if (e.key === 'ArrowLeft' || e.key.toLowerCase() === 'p') {e.preventDefault();void previous()}
    }
    window.addEventListener('keydown', onKey)
    return ()=>window.removeEventListener('keydown', onKey)
  })

  const openObs = () => {
    const obs = status?.obs
    if (obs) setObsForm(f=>({...f, host:obs.host, port:String(obs.port), input_name:obs.input_name, dual_mode:obs.dual_mode, source_a:obs.dual_sources?.[0] ?? '', source_b:obs.dual_sources?.[1] ?? ''}))
    setObsOpen(true)
  }
  /** Reads the real input names from OBS with the connection typed in the form (empty password = the saved one). */
  const loadObsInputs = async () => {
    setInputsLoading(true)
    try{setObsInputs(await apiRequest<ObsInputs>(onAgent('/live/obs/inputs', agentId), post({host:obsForm.host, port:Number(obsForm.port), password:obsForm.password})))}
    catch(e){setObsInputs(null);toast.push({tone:'danger',title:'Không đọc được source từ OBS',description:errorText(e, '')})}
    finally{setInputsLoading(false)}
  }
  const sameAB = obsForm.dual_mode && (obsForm.source_a.trim() || obsForm.input_name + ' A') === (obsForm.source_b.trim() || obsForm.input_name + ' B')
  /** Hint under a source-name field telling whether OBS actually has that input in the live scene. */
  const sourceHint = (name:string, fallback?:string) => {
    const value = name.trim() || fallback || ''
    if (!obsInputs) return fallback && !name.trim() ? `Để trống sẽ dùng “${fallback}”. Bấm “Lấy danh sách từ OBS” để chọn tên có sẵn.` : 'Bấm “Lấy danh sách từ OBS” để kiểm tra tên.'
    const found = obsInputs.inputs.find(i=>i.name === value)
    if (!found) return `⚠ OBS không có source “${value}”.`
    if (!found.in_scene) return `⚠ “${value}” có trong OBS nhưng chưa nằm trong scene “${obsInputs.scene}”.`
    return `✓ Có trong scene “${obsInputs.scene}”${found.kind === 'ffmpeg_source' ? '' : ` (loại ${found.kind}, nên dùng Media Source)`}.`
  }
  const saveObs = async (event:FormEvent) => {
    event.preventDefault()
    if (sameAB) return
    setBusy(true)
    try{
      const result = await apiRequest<LiveStatus>(onAgent('/live/config', agentId), post({...obsForm, port:Number(obsForm.port)}))
      applyStatus(result)
      if (result.connected) {setObsOpen(false);toast.push({tone:'success',title:'Đã kết nối OBS',description:result.obs_version ? 'OBS ' + result.obs_version : undefined})}
      else toast.push({tone:'danger',title:'Chưa kết nối được OBS',description:result.error})
    }catch(e){toast.push({tone:'danger',title:'Không lưu được cấu hình',description:errorText(e, '')})}
    finally{setBusy(false)}
  }

  const onAir = running && !status?.paused

  return <div className="lc-page">
    <div className={'agent-note lc-agent' + (offline ? ' is-offline' : '')}>
      <MonitorPlay size={16}/>
      <span>Đang điều khiển máy <b>{agent.hostname}</b> · {offline ? 'offline — mở Sarae Agent trên máy đó' : 'online'}. Đổi máy ở góc trên bên phải; tab khác có thể điều khiển máy khác cùng lúc.</span>
    </div>
    {elsewhere.length > 0 && <Alert tone="info" title="Máy khác đang LIVE">
      {elsewhere.map(a=><div key={a.id} className="lc-alert"><span><b>{a.hostname}</b> đang phát{a.session_id ? ` phiên #${a.session_id}` : ''}.</span>
        <Button size="sm" variant="outline" onClick={()=>select(a.id)}>Điều khiển máy này</Button></div>)}
    </Alert>}
    {/* Connection + session health */}
    <section className={'lc-status' + (onAir ? ' is-live' : '')} aria-live="polite">
      <div className="lc-status-main">
        <span className={'lc-onair' + (onAir ? ' is-on' : status?.paused ? ' is-paused' : '')}><Radio size={16}/>{onAir ? 'ĐANG PHÁT' : status?.paused ? 'TẠM DỪNG' : 'CHƯA PHÁT'}</span>
        <div className="lc-status-text">
          <strong>{liveProduct ? liveProduct.name : running ? `Playlist #${status?.playlist_id ?? '—'}` : 'Chưa có phiên phát'}</strong>
          <small>{activeSession ? <><a href={sessionHref(activeSession.id)}>{activeSession.name}</a>{status?.last_status ? ' · ' + status.last_status : ''}</> : status?.last_status || 'Chọn một phiên LIVE đã chuẩn bị ở khung bên phải để bắt đầu.'}</small>
        </div>
      </div>
      <div className="lc-metrics">
        <Metric icon={<Clock size={15}/>} label="Thời gian phiên" value={sessionLabel(status?.session_seconds)}/>
        <Metric icon={<FastForward size={15}/>} label="Lần chuyển video" value={status?.transition_count ?? 0}/>
        <Metric icon={<CircleAlert size={15}/>} label="Lỗi / phục hồi" value={`${status?.error_count ?? 0} / ${status?.recovery_count ?? 0}`} warn={(status?.error_count ?? 0) > 0}/>
      </div>
      <button type="button" className={'lc-obs' + (status?.connected ? ' is-ok' : ' is-off')} onClick={openObs} title="Cấu hình kết nối OBS">
        {status?.connected ? <Wifi size={16}/> : <WifiOff size={16}/>}
        <span><strong>{status?.connected ? `OBS ${status.obs_version ?? ''}` : 'OBS chưa kết nối'}</strong><small>{status?.obs?.dual_mode ? `A/B: ${status.obs.dual_sources?.join(' · ') ?? ''}` : status?.obs?.input_name ?? 'Sarae Video'}</small></span>
        <Settings2 size={15}/>
      </button>
    </section>

    {status && !status.connected && <Alert tone="danger" title="OBS chưa kết nối">
      <div className="lc-alert">
        <span>{status.error || 'Không kết nối được OBS WebSocket.'}</span>
        <Button size="sm" variant="outline" onClick={openObs}>Cấu hình kết nối</Button>
      </div>
    </Alert>}

    <div className="lc-grid">
      <div className="lc-main">
        {/* Now playing + transport */}
        <Card className="lc-now" padding={false}>
          <div className="lc-now-body">
            <div className="lc-now-thumb">{currentId != null ? <VideoThumb src={current?.thumb_url}/> : <Film size={30}/>}</div>
            <div className="lc-now-info">
              <div className="lc-now-tags">
                {liveProduct && <Badge tone="primary"><Package size={12}/>{liveProduct.name}</Badge>}
                {running && <Badge>Video {(status?.index ?? 0) + 1}/{status?.count ?? 0}</Badge>}
                {mediaState && <Badge tone={mediaState.tone}>{mediaState.label}</Badge>}
                {status?.loop && running && <Badge tone="info">Lặp</Badge>}
              </div>
              <h2 title={current?.filename}>{current?.filename ?? (running ? 'Video không có trong thư viện' : 'Chưa phát video nào')}</h2>
              <SeekBar cursor={cursor} duration={duration} disabled={busy || offline || !running} onSeek={seek}/>
            </div>
          </div>
          <div className="lc-transport">
            <Button variant="outline" leftIcon={<SkipBack size={17}/>} disabled={busy || offline || !running} onClick={()=>void previous()} title="Video trước (←)">Trước</Button>
            <Button className="lc-play" leftIcon={status?.paused ? <Play size={18}/> : <Pause size={18}/>} disabled={busy || offline || !running} onClick={()=>void togglePause()} title="Tạm dừng / tiếp tục (Space)">{status?.paused ? 'Tiếp tục' : 'Tạm dừng'}</Button>
            <Button variant="outline" rightIcon={<SkipForward size={17}/>} disabled={busy || offline || !running} onClick={()=>void next()} title="Video tiếp (→)">Tiếp</Button>
            <Button className="lc-stop" variant="danger" leftIcon={<Square size={16}/>} disabled={busy || offline || !running} onClick={()=>setConfirmStop(true)}>{activeSession ? 'Kết thúc' : 'Dừng'}</Button>
          </div>
          {pendingProduct && <div className="lc-pending">
            <span>Sau video này sẽ chuyển sang <b>{pendingProduct.name}</b></span>
            <Button size="sm" variant="ghost" leftIcon={<Undo2 size={14}/>} disabled={busy} onClick={()=>void act('/live/product-session/cancel-switch', post(), 'Đã hủy chuyển sản phẩm')}>Hủy</Button>
          </div>}
          <div className="lc-shortcuts"><Kbd>Space</Kbd> tạm dừng / tiếp <Kbd>←</Kbd> video trước <Kbd>→</Kbd> video tiếp · kéo thanh thời gian để tua</div>
        </Card>

        {/* Queue of the running session */}
        <Card className="lc-queue" padding={false} title={<span className="lc-card-title"><ListVideo size={17}/>Hàng đợi phát</span>}
          description={running ? `${queue.length} video · ${status?.loop ? 'lặp liên tục' : 'phát một lượt'}` : 'Hàng đợi sẽ hiện khi phiên phát bắt đầu'}>
          {!running || !queue.length
            ? <EmptyState icon={<ListVideo size={28}/>} title="Chưa có hàng đợi" description="Bắt đầu một phiên LIVE để xem và chọn video đang phát."/>
            : <ol className="lc-queue-list">{queue.map((id,i)=>{
              const video = videoById.get(id)
              const isCurrent = i === status?.index
              return <li key={`${id}-${i}`} className={isCurrent ? 'is-current' : ''}>
                <span className="lc-q-index">{isCurrent ? <Radio size={14}/> : i + 1}</span>
                <span className="lc-q-thumb"><VideoThumb src={video?.thumb_url}/></span>
                <span className="lc-q-info"><strong title={video?.filename}>{video?.filename ?? 'Video không có trong thư viện'}</strong><small>{durationLabel(video?.duration_seconds) ?? '—'}{isCurrent ? ' · đang phát' : ''}</small></span>
                {!isCurrent && <Button size="sm" variant="ghost" leftIcon={<Play size={14}/>} disabled={busy} onClick={()=>void act('/live/select', post({index:i}), 'Đã phát video được chọn')}>Phát</Button>}
              </li>
            })}</ol>}
        </Card>
      </div>

      {/* Products of the running session, or the prepared sessions to start one */}
      <Card className="lc-products" padding={false}
        title={<span className="lc-card-title">{running ? <Package size={17}/> : <CalendarClock size={17}/>}{activeSession ? 'Sản phẩm trong phiên' : running ? 'Sản phẩm LIVE' : 'Bắt đầu phiên LIVE'}</span>}
        description={activeSession ? `${activeSession.name} · ${lineup.length} sản phẩm` : running ? 'Phát ngoài Phiên LIVE' : 'Chọn một phiên đã chuẩn bị'}
        actions={<>
          {activeSession && <a className="lc-icon-link" href={sessionHref(activeSession.id)} title="Sửa danh sách sản phẩm của phiên" aria-label="Sửa danh sách sản phẩm của phiên"><Pencil size={15}/></a>}
          <Button variant="ghost" aria-label="Tải lại" title="Tải lại" onClick={()=>void loadAll()}><RefreshCw size={16}/></Button>
        </>}>
        {activeSession ? <>
          <div className="lc-products-search"><SearchInput value={productQuery} onChange={e=>setProductQuery(e.target.value)} placeholder="Tìm trong phiên hoặc toàn bộ sản phẩm…" aria-label="Tìm sản phẩm"/></div>
          <ul className="lc-product-list">
            {inSession.map(p=>productRow(p, false))}
            {q && !inSession.length && <li className="lc-muted">Không có sản phẩm khớp trong phiên.</li>}
            {outside.length > 0 && <li className="lc-group">Ngoài danh sách phiên</li>}
            {outside.map(p=>productRow(p, true))}
            {!q && <li className="lc-muted lc-small">Cần sản phẩm khác? Gõ tên để tìm trong toàn bộ danh mục.</li>}
          </ul>
          <div className="lc-product-actions">
            {!targetProduct ? <p className="lc-muted">Chọn sản phẩm muốn đưa lên LIVE — không cần theo thứ tự.</p> : <>
              <p className="lc-target">Đưa <b>{targetProduct.name}</b> lên LIVE{targetOutside ? ' (sẽ thêm vào phiên)' : ''}</p>
              <Button block leftIcon={<SkipForward size={16}/>} disabled={busy} onClick={()=>void switchProduct(targetProduct.id, 'immediate')}>Chuyển ngay</Button>
              <Button block variant="outline" disabled={busy || targetProduct.id === status?.pending_product_id} onClick={()=>void switchProduct(targetProduct.id, 'after_current')}>Chuyển sau video hiện tại</Button>
            </>}
            <small className="lc-tip">Ghim sản phẩm trên TikTok LIVE trước, rồi mới chuyển video.</small>
          </div>
        </> : running ? <div className="lc-pad">
          <Alert tone="warning" title={productSession ? 'Đang phát ngoài Phiên LIVE' : 'Đang phát playlist thủ công'}>
            {productSession
              ? 'Phiên phát này không gắn với Phiên LIVE nào nên không có danh sách sản phẩm và không được lưu lịch sử. Dừng phát rồi bắt đầu lại từ một phiên đã chuẩn bị.'
              : 'Dừng playlist để bắt đầu một phiên LIVE theo danh sách sản phẩm.'}
          </Alert>
        </div> : <>{stale.length > 0 && <div className="lc-pad lc-stale">
          <Alert tone="warning" title="Phiên bị ngắt">
            {stale.map(s=><div key={s.id} className="lc-alert">
              <span>“{s.name}” không còn phát trên máy này (Agent, OBS hoặc máy đã khởi động lại). Tiếp tục để phát lại {productById.get(s.current_product_id ?? -1)?.name ?? 'sản phẩm đang LIVE'}.</span>
              <span className="lc-stale-actions">
                <Button size="sm" variant="outline" disabled={busy} onClick={()=>void perform(()=>sessionsApi.finish(s.id), 'Đã đóng phiên bị ngắt').then(()=>loadAll())}>Đóng</Button>
                <Button size="sm" leftIcon={<Play size={14}/>} disabled={busy || !status?.connected} title={status?.connected ? undefined : 'Cần kết nối OBS trước'}
                  onClick={()=>void perform(()=>sessionsApi.resume(s), `Đã tiếp tục “${s.name}”`).then(()=>loadAll())}>Tiếp tục phiên</Button>
              </span>
            </div>)}
          </Alert>
        </div>}{!drafts.length ? <div className="lc-pad">
          <EmptyState icon={<CalendarClock size={28}/>} title="Máy này chưa có phiên nào được chuẩn bị" description={`Tạo phiên LIVE cho máy ${agent.hostname} và chọn những sản phẩm sẽ bán, rồi quay lại đây để bắt đầu.`}
            actionLabel="Tạo phiên LIVE" onAction={()=>{window.location.hash = '/sessions'}}/>
        </div> : <>
          <ul className="lc-product-list">{drafts.map(s=>{
            const ready = s.product_ids.filter(id=>productById.get(id)?.video_count).length
            return <li key={s.id}><button type="button" className={picked?.id === s.id ? 'is-selected' : ''} onClick={()=>{setPickSession(s.id);setStartProduct(null)}}>
              <span className="lc-p-text"><strong>{s.name}</strong><small>{s.product_ids.length} sản phẩm · {ready}/{s.product_ids.length} có video</small></span>
              {!s.product_ids.length ? <Badge tone="warning">Trống</Badge> : !ready ? <Badge tone="warning">Thiếu video</Badge> : null}
            </button></li>
          })}</ul>
          <div className="lc-product-actions">
            {picked && (pickedReady.length
              ? <Select label="Phát đầu tiên" value={String(startId)} onChange={v=>setStartProduct(Number(v))}
                options={pickedReady.map(id=>({value:String(id), label:productById.get(id)?.name ?? `Sản phẩm #${id}`}))}/>
              : <small className="lc-hint">Phiên chưa có sản phẩm nào có video.</small>)}
            <Button block leftIcon={<Play size={16}/>} disabled={busy || !picked || !status?.connected || startId == null} onClick={()=>{if (picked && startId != null) void startSession(picked, startId)}}>Bắt đầu LIVE</Button>
            {!status?.connected && <small className="lc-hint">Cần kết nối OBS trước khi bắt đầu.</small>}
            <a className="lc-link lc-center" href={sessionHref(picked?.id)}>Xem / sửa phiên ở trang Phiên LIVE →</a>
          </div>
        </>}</>}
      </Card>
    </div>

    <Collapse title="Phát thử playlist thủ công (chế độ phụ)">
      <div className="lc-manual">
        <Select label="Playlist" value={playlistId} onChange={setPlaylistId} options={[{value:'',label:playlists.length ? 'Chọn playlist…' : 'Chưa có playlist'},...playlists.map(p=>({value:String(p.id),label:`${p.name} · ${p.media_ids.length} video`}))]}/>
        <Button leftIcon={<Play size={16}/>} disabled={busy || !playlistId || running || !status?.connected} onClick={()=>void perform(()=>agentAction(agentId, `/agent-playlists/${playlistId}/play`, {method:'POST'}), 'Đã phát playlist lên OBS')}>Phát thử</Button>
        <a className="lc-link" href="#/playlists">Quản lý playlist →</a>
      </div>
    </Collapse>
    <p className="lc-disclaimer">Bản thử nghiệm chỉ điều khiển Media Source trong OBS; không tự bật Start Streaming. Kiểm tra trên OBS Preview trước khi phát thật.</p>

    <Modal open={obsOpen} title="Kết nối OBS" size="md" onClose={()=>{if(!busy)setObsOpen(false)}} footer={<>
      <Button variant="ghost" disabled={busy} onClick={()=>setObsOpen(false)}>Hủy</Button>
      <Button type="submit" form="lc-obs-form" loading={busy} disabled={sameAB}>Kết nối / kiểm tra</Button>
    </>}>
      <form id="lc-obs-form" className="media-form" onSubmit={event=>void saveObs(event)}>
        <Alert tone="info" title="Chuẩn bị trong OBS">OBS → Tools → WebSocket Server Settings: bật server (port mặc định 4455). Tạo Media Source trong scene đang phát và tắt Loop; đặt tên tùy ý rồi chọn lại ở dưới.</Alert>
        <div className="lc-form-row">
          <Input label="Host" value={obsForm.host} onChange={e=>setObsForm(f=>({...f, host:e.target.value}))} hint="Chỉ hỗ trợ OBS trên cùng máy"/>
          <Input label="Port" type="number" value={obsForm.port} onChange={e=>setObsForm(f=>({...f, port:e.target.value}))}/>
        </div>
        <PasswordInput label="Mật khẩu WebSocket" autoComplete="off" value={obsForm.password} onChange={e=>setObsForm(f=>({...f, password:e.target.value}))}
          hint={status?.obs?.has_password ? 'Đã lưu mật khẩu trên Backend; để trống để giữ nguyên.' : 'Để trống nếu OBS không bật xác thực.'}/>

        <div className="lc-sources">
          <div className="lc-sources-head">
            <span><strong>Media Source phát video</strong><small>{obsInputs ? `Scene đang phát: ${obsInputs.scene} · ${obsInputs.inputs.filter(i=>i.kind === 'ffmpeg_source').length} Media Source` : 'Nhập tên hoặc lấy danh sách từ OBS'}</small></span>
            <Button type="button" size="sm" variant="outline" leftIcon={<RefreshCw size={14}/>} loading={inputsLoading} onClick={()=>void loadObsInputs()}>Lấy danh sách từ OBS</Button>
          </div>
          <datalist id="lc-obs-inputs">{obsInputs?.inputs.map(i=><option key={i.name} value={i.name}>{i.in_scene ? 'Trong scene' : 'Ngoài scene'} · {i.kind}</option>)}</datalist>
          <Switch label="Phát A/B (2 source luân phiên, giảm nháy khi chuyển video)" checked={obsForm.dual_mode} onChange={e=>setObsForm(f=>({...f, dual_mode:e.target.checked}))}/>
          {obsForm.dual_mode
            ? <div className="lc-form-row lc-form-row--even">
              <Input label="Source A" list="lc-obs-inputs" value={obsForm.source_a} placeholder={obsForm.input_name + ' A'} onChange={e=>setObsForm(f=>({...f, source_a:e.target.value}))} hint={sourceHint(obsForm.source_a, obsForm.input_name + ' A')}/>
              <Input label="Source B" list="lc-obs-inputs" value={obsForm.source_b} placeholder={obsForm.input_name + ' B'} onChange={e=>setObsForm(f=>({...f, source_b:e.target.value}))} hint={sourceHint(obsForm.source_b, obsForm.input_name + ' B')}
                error={sameAB ? 'Source B phải khác Source A' : undefined}/>
            </div>
            : <Input label="Tên Media Source" list="lc-obs-inputs" required value={obsForm.input_name} onChange={e=>setObsForm(f=>({...f, input_name:e.target.value}))} hint={sourceHint(obsForm.input_name)}/>}
        </div>
        {running && <Alert tone="warning">Lưu cấu hình sẽ ngắt phiên phát hiện tại.</Alert>}
      </form>
    </Modal>

    <ConfirmModal open={confirmStop} loading={busy}
      title={activeSession ? 'Kết thúc phiên LIVE?' : 'Dừng phát?'}
      description={activeSession ? `OBS sẽ dừng phát và “${activeSession.name}” được lưu vào lịch sử. Không thể tiếp tục phiên sau khi kết thúc.` : 'Media Source trong OBS sẽ dừng phát.'}
      confirmLabel={activeSession ? 'Kết thúc phiên' : 'Dừng phát'}
      onClose={()=>setConfirmStop(false)} onConfirm={()=>{setConfirmStop(false);void perform(()=>apiRequest(onAgent('/live/stop', agentId), post()), activeSession ? 'Đã kết thúc phiên LIVE' : 'Đã dừng phát').then(()=>loadAll())}}/>
  </div>
}

function Metric({icon,label,value,warn}:{icon:ReactNode;label:string;value:ReactNode;warn?:boolean}){
  return <div className={'lc-metric' + (warn ? ' is-warn' : '')}><span>{icon}{label}</span><strong>{value}</strong></div>
}

const SEEK_STEP_MS = 5000

/** Timeline you can click or drag to seek; the seek is sent once on release. Arrow keys step 5 s when focused. */
function SeekBar({cursor,duration,disabled,onSeek}:{cursor:number|null;duration?:number;disabled:boolean;onSeek:(ms:number)=>void}){
  const track = useRef<HTMLDivElement>(null)
  const [drag,setDrag] = useState<number|null>(null)
  const [hover,setHover] = useState<number|null>(null)
  const usable = !disabled && !!duration
  const total = duration ?? 0
  const shown = drag ?? cursor
  const percent = (ms:number|null) => ms != null && total ? Math.max(0, Math.min(100, ms / total * 100)) : 0
  const msAt = (clientX:number) => {
    const rect = track.current!.getBoundingClientRect()
    return Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)) * total
  }
  const onPointerDown = (e:ReactPointerEvent<HTMLDivElement>) => {
    if (!usable || e.button !== 0) return
    e.preventDefault() // no text selection while dragging past the bar
    e.currentTarget.setPointerCapture(e.pointerId)
    e.currentTarget.focus()
    setDrag(msAt(e.clientX))
  }
  const onPointerMove = (e:ReactPointerEvent<HTMLDivElement>) => {
    if (!usable) return
    const ms = msAt(e.clientX)
    setHover(ms)
    if (drag != null) setDrag(ms)
  }
  const onPointerUp = () => {
    if (drag == null) return
    onSeek(drag)
    setDrag(null)
  }
  const onKeyDown = (e:ReactKeyboardEvent<HTMLDivElement>) => {
    if (!usable || cursor == null) return
    const target = e.key === 'ArrowRight' || e.key === 'ArrowUp' ? cursor + SEEK_STEP_MS
      : e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? cursor - SEEK_STEP_MS
      : e.key === 'Home' ? 0 : e.key === 'End' ? total - 1000 : null
    if (target == null) return
    e.preventDefault()
    e.stopPropagation()
    onSeek(Math.max(0, Math.min(total, target)))
  }
  const tip = drag ?? hover
  return <div className="lc-seekbar-wrap">
    <div ref={track} className={'lc-seekbar' + (usable ? '' : ' is-disabled') + (drag != null ? ' is-dragging' : '')}
      role="slider" tabIndex={usable ? 0 : -1} aria-label="Tua video" aria-disabled={!usable}
      aria-valuemin={0} aria-valuemax={Math.round(total / 1000)} aria-valuenow={Math.round((shown ?? 0) / 1000)} aria-valuetext={clock(shown)}
      onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={()=>setDrag(null)}
      onPointerLeave={()=>setHover(null)} onKeyDown={onKeyDown}>
      <span className="lc-seekbar-track"><span className="lc-seekbar-fill" style={{width:`${percent(shown)}%`}}/></span>
      {usable && <span className="lc-seekbar-thumb" style={{left:`${percent(shown)}%`}}/>}
      {usable && tip != null && <span className="lc-seekbar-tip" style={{left:`${percent(tip)}%`}}>{clock(tip)}</span>}
    </div>
    <div className="lc-times"><span>{clock(shown)}</span><span>{clock(duration)}</span></div>
  </div>
}
