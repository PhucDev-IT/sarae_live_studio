import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import {
  Alert, Badge, Button, Card, ConfirmModal, EmptyState, Kbd, Modal, SearchInput, useToast,
} from './ui/core'
import {
  CalendarClock, CircleAlert, Clock, FastForward, Film, ListVideo, Package, Pause, Pencil, Play, Radio, RefreshCw, Settings2, SkipBack, SkipForward,
  MonitorPlay, Square, Undo2, Wifi, WifiOff,
} from 'lucide-react'
import { agentAction, apiRequest, assetUrl, durationLabel, errorText, onAgent, type OwnerAgent } from './mediaApi'
import { listCloudMedia, type CloudMedia } from './cloudMediaApi'
import { isSingle, sessionHref, sessionsApi, type LiveProduct, type LiveSession, type PlaybackPolicy, type RotationStatus } from './liveSessionsApi'
import PlaybackSettings from './PlaybackSettings'
import TimelinePinPanel from './TimelinePinPanel'
import SessionLiveChat from './SessionLiveChat'
import { WithAgent, useAgent } from './agentContext'
import VideoThumb from './VideoThumb'
import './media.css'
import './live-control.css'

type ObsSettings = { host:string; port:number; input_name:string; dual_mode:boolean; dual_sources?:string[]; has_password:boolean }
type PinCard = { index:number; token:string; name:string; image_url:string; price_text:string; product_ids:string[]; pinned:boolean }
type PinState = { status?:'IDLE'|'PENDING'|'SUCCESS'|'FAILED'; product_id?:string|null; card_name?:string|null; action?:string|null; error?:string|null; auto_enabled?:boolean; browser_open?:boolean }
type LiveStatus = {
  connected:boolean; error?:string; obs_version?:string; media_state?:string; media_cursor?:number|null; media_duration?:number|null
  running:boolean; paused?:boolean; loop?:boolean; session_mode?:string; playlist_id:number|null; product_id?:number|null; pending_product_id?:number|null; session_id?:number|null
  queue_ids?:string[]; index:number; count:number; last_status:string; retry_count?:number; error_count?:number; transition_count?:number
  recovery_count?:number; session_seconds?:number; obs?:ObsSettings
  /** Video order / product rotation the Agent applies, and its rotation progress (docs/PLAYBACK_POLICY.md). */
  policy?:PlaybackPolicy|null; rotation?:RotationStatus|null
  pin?:PinState
}

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
  const [videos,setVideos] = useState<CloudMedia[]>([])
  const [busy,setBusy] = useState(false)
  const [sessions,setSessions] = useState<LiveSession[]>([])
  const [target,setTarget] = useState<number|null>(null)
  const [productQuery,setProductQuery] = useState('')
  const [confirmStop,setConfirmStop] = useState(false)

  const [obsOpen,setObsOpen] = useState(false)
  const [pinBusy,setPinBusy] = useState(false)
  const [pinObserved,setPinObserved] = useState(false)
  const [pinCandidates,setPinCandidates] = useState<PinCard[]>([])
  const [pinTarget,setPinTarget] = useState<LiveProduct|null>(null)

  const applyStatus = (next:LiveStatus) => {setStatus(next);setReceivedAt(Date.now())}
  const agentId = agent.id
  const pollStatus = useCallback(async ()=>{try{applyStatus(await apiRequest<LiveStatus>(onAgent('/live/status', agentId)))}catch{/* keep last known status */}},[agentId])
  const loadAll = useCallback(async ()=>{
    const [s, p, v, ss] = await Promise.all([
      apiRequest<LiveStatus>(onAgent('/live/status', agentId)), sessionsApi.products(agentId), listCloudMedia().then(r=>r.items), sessionsApi.list(),
    ])
    applyStatus(s);setProducts(p);setVideos(v);setSessions(ss)
  },[agentId])
  useEffect(()=>{
    void loadAll().catch(e=>toast.push({tone:'danger',title:'Không tải được dữ liệu LIVE',description:errorText(e, '')}))
    const poll = window.setInterval(()=>void pollStatus(), POLL_MS)
    const tick = window.setInterval(()=>setNow(Date.now()), 500)
    return ()=>{window.clearInterval(poll);window.clearInterval(tick)}
  },[loadAll,pollStatus,toast])

  const refreshPin = useCallback(async () => {
    if (!agent.online) return
    try {
      const result = await apiRequest<{pinned_product_id:string|null;pinned_card:PinCard|null;multiple_pins:boolean}>(onAgent('/live/pin-state',agentId))
      setPinObserved(true)
      setStatus(prev=>prev ? {...prev,pin:{...prev.pin,product_id:result.pinned_product_id,
        card_name:result.pinned_card?.name ?? null,
        ...(result.multiple_pins ? {status:'FAILED' as const,error:'Nhiều product card đang ghim'} : {})}} : prev)
    } catch {/* Console may be closed or not logged in; the Agent heartbeat carries that state. */}
  },[agent.online,agentId])
  useEffect(()=>{
    if (!agent.online) return
    void refreshPin()
    const poll = window.setInterval(()=>void refreshPin(),12000)
    return ()=>window.clearInterval(poll)
  },[agent.online,refreshPin])

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
  const queue = status?.queue_ids ?? []
  const currentId = running ? queue[status?.index ?? 0] : undefined
  const current = currentId != null ? videoById.get(currentId) : undefined
  const liveProduct = status?.product_id != null ? productById.get(status.product_id) : undefined
  const pinSelection = (target != null ? productById.get(target) : undefined) ?? liveProduct
  const pinnedProduct = products.find(p=>p.tiktok_product_id && p.tiktok_product_id === status?.pin?.product_id)
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
  // One main video with a pin timeline: no product switching, rotation or video queue navigation.
  const single = isSingle(activeSession)
  const q = productQuery.trim().toLocaleLowerCase('vi')
  const matches = (p:LiveProduct) => !q || (p.name + ' ' + p.sku).toLocaleLowerCase('vi').includes(q)
  const inSession = lineup.map(id=>productById.get(id) ?? {id, shop_id:null, name:`Sản phẩm #${id}`, sku:'', video_count:0}).filter(matches)
  const outside = q ? products.filter(p=>!lineup.includes(p.id) && p.status !== 'INACTIVE' && matches(p)) : []
  const targetProduct = target != null && target !== status?.product_id ? productById.get(target) : undefined
  const targetOutside = !!targetProduct && !lineup.includes(targetProduct.id)

  const sendPin = async (product:LiveProduct, confirmedToken?:string) => {
    setPinBusy(true)
    try {
      await agentAction(agentId, onAgent('/live/pin', agentId), post({product_id:product.id, confirmed_token:confirmedToken ?? null}))
      toast.push({tone:'success',title:`Đã ghim ${product.name}`})
      setPinCandidates([]);setPinTarget(null)
      await pollStatus()
      await refreshPin()
    } catch(e) {toast.push({tone:'danger',title:'Không ghim được sản phẩm',description:errorText(e, '')})}
    finally {setPinBusy(false)}
  }
  const previewPin = async () => {
    if (!pinSelection) return
    setPinBusy(true)
    try {
      const state = await apiRequest<{candidates:PinCard[]}>(onAgent(`/live/pin-state?product_id=${pinSelection.id}`,agentId))
      if (!state.candidates.length) throw new Error('LIVE Console chưa tìm thấy product card khớp tên, ảnh hoặc giá. Kiểm tra Console và đồng bộ lại sản phẩm.')
      if (state.candidates.length === 1) await sendPin(pinSelection)
      else {setPinTarget(pinSelection);setPinCandidates(state.candidates)}
    } catch(e) {toast.push({tone:'danger',title:'Không xác định được product card',description:errorText(e, '')})}
    finally {setPinBusy(false)}
  }
  const unpin = async () => {
    setPinBusy(true)
    try {await agentAction(agentId,onAgent('/live/unpin',agentId),post());toast.push({tone:'success',title:'Đã bỏ ghim'});await pollStatus();await refreshPin()}
    catch(e){toast.push({tone:'danger',title:'Không bỏ ghim được',description:errorText(e,'')})}
    finally{setPinBusy(false)}
  }
  const toggleAutoPin = async () => {
    const enabled = !status?.pin?.auto_enabled
    setPinBusy(true)
    try {
      await agentAction(agentId,onAgent('/live/auto-pin',agentId),post({enabled}))
      setStatus(prev=>prev ? {...prev,pin:{...prev.pin,auto_enabled:enabled}} : prev)
      toast.push({tone:'success',title:enabled?'Đã bật tự động ghim':'Đã tắt tự động ghim'})
    } catch(e) {toast.push({tone:'danger',title:'Không đổi được tự động ghim',description:errorText(e,'')})}
    finally{setPinBusy(false)}
  }

  // Nothing running: pick one of the prepared (DRAFT) sessions and the product to open with.
  // Only this Agent's sessions: a session always plays on the Agent it belongs to.
  const mine = sessions.filter(s=>s.agent_id === agentId)
  // DRAFTs have no Agent yet: they are started from the session page, after the Console sync and product review.
  const drafts = sessions.filter(s=>s.status === 'DRAFT')
  // RUNNING rows with no playback behind them (Agent, OBS or PC restarted mid-LIVE).
  const stale = running ? [] : mine.filter(s=>s.status === 'RUNNING')
  // Other PCs of the owner that are LIVE right now (switching shows their console instead).
  const elsewhere = agents.filter(a=>a.id !== agentId && a.playing)
  const offline = !agent.online
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
    return <li key={p.id}><button type="button" className={(target === p.id ? 'is-selected ' : '') + (isLive ? 'is-live' : '')} aria-pressed={target === p.id} disabled={busy} onClick={()=>setTarget(p.id)}>
      <span className="lc-p-thumb">{isLive ? <Radio size={16}/> : <Package size={16}/>}{p.image_url && <img src={assetUrl(p.image_url) ?? undefined} alt="" loading="lazy" onError={e=>{e.currentTarget.style.display = 'none'}}/>}</span>
      <span className="lc-p-text"><strong title={p.name}>{p.name}</strong><small title={p.sku || undefined}>{p.sku || '—'} · {p.video_count ? `${p.video_count} video` : 'chưa có video'}</small></span>
      {isLive ? <span className="lc-p-badge"><Badge tone="danger" dot>Đang phát</Badge></span> : isPending ? <span className="lc-p-badge"><Badge tone="warning">Chờ</Badge></span> : isOutside ? <span className="lc-p-badge"><Badge>Ngoài DS</Badge></span> : null}
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
      else if (!single && (e.key === 'ArrowRight' || e.key.toLowerCase() === 'n')) {e.preventDefault();void next()}
      else if (!single && (e.key === 'ArrowLeft' || e.key.toLowerCase() === 'p')) {e.preventDefault();void previous()}
    }
    window.addEventListener('keydown', onKey)
    return ()=>window.removeEventListener('keydown', onKey)
  })

  const openObs = () => setObsOpen(true)

  const onAir = running && !status?.paused

  return <div className="lc-page">
    <div className={'agent-note lc-agent' + (offline ? ' is-offline' : '')}>
      <MonitorPlay size={16}/>
      <span>Đang điều khiển máy <b>{agent.hostname}</b> · {offline ? 'offline — mở Sarae Agent trên máy đó' : 'online'}. Đổi máy ở góc trên bên phải; tab khác có thể điều khiển máy khác cùng lúc.</span>
    </div>
    {elsewhere.length > 0 && <Alert tone="info" title="Máy khác đang phát">
      {elsewhere.map(a=><div key={a.id} className="lc-alert"><span><b>{a.hostname}</b> đang phát{a.session_id ? ` phiên #${a.session_id}` : ''}.</span>
        <Button size="sm" variant="outline" onClick={()=>select(a.id)}>Điều khiển máy này</Button></div>)}
    </Alert>}
    {/* Connection + session health */}
    <section className={'lc-status' + (onAir ? ' is-live' : '')} aria-live="polite">
      <div className="lc-status-head">
        <span className={'lc-onair' + (onAir ? ' is-on' : status?.paused ? ' is-paused' : '')}><Radio size={14}/>{onAir ? 'ĐANG PHÁT' : status?.paused ? 'TẠM DỪNG' : 'CHƯA PHÁT'}</span>
        <div className="lc-status-text">
          <strong title={liveProduct?.name}>{single ? `Một video + lịch ghim · ${current?.display_name ?? 'video chính'}` : liveProduct ? liveProduct.name : running ? 'Đang phát ngoài Phiên LIVE' : 'Chưa có phiên phát'}</strong>
          <small title={activeSession ? activeSession.name + (status?.last_status ? ' · ' + status.last_status : '') : status?.last_status}>{activeSession ? <><a href={sessionHref(activeSession.id)}>{activeSession.name}</a>{status?.last_status ? ' · ' + status.last_status : ''}</> : status?.last_status || 'Chọn một phiên LIVE đã chuẩn bị để bắt đầu.'}</small>
        </div>
        <button type="button" className={'lc-obs' + (status?.connected ? ' is-ok' : ' is-off')} onClick={openObs}
          title={`Xem trạng thái OBS · ${status?.obs?.dual_mode ? `A/B: ${status.obs.dual_sources?.join(' · ') ?? ''}` : status?.obs?.input_name ?? 'Sarae Video'}`}>
          {status?.connected ? <Wifi size={14}/> : <WifiOff size={14}/>}
          <span className="lc-obs-text"><strong>{status?.connected ? `OBS ${status.obs_version ?? ''}` : 'OBS chưa kết nối'}</strong><small>{status?.obs?.dual_mode ? `A/B: ${status.obs.dual_sources?.join(' · ') ?? ''}` : status?.obs?.input_name ?? 'Sarae Video'}</small></span>
          <Settings2 size={14}/>
        </button>
      </div>
      <div className="lc-metrics">
        <Metric icon={<Clock size={15}/>} label="Thời gian phiên" value={sessionLabel(status?.session_seconds)}/>
        <Metric icon={<FastForward size={15}/>} label="Lần chuyển video" value={status?.transition_count ?? 0}/>
        <Metric icon={<CircleAlert size={15}/>} label="Lỗi / phục hồi" value={`${status?.error_count ?? 0} / ${status?.recovery_count ?? 0}`} warn={(status?.error_count ?? 0) > 0}/>
      </div>
    </section>

    {status && !status.connected && <Alert tone="danger" title="OBS chưa kết nối">
      <div className="lc-alert">
        <span>{status.error || 'Không kết nối được OBS WebSocket.'}</span>
        <Button size="sm" variant="outline" onClick={openObs}>Xem hướng dẫn</Button>
      </div>
    </Alert>}

    <div className="lc-grid">
      <div className="lc-main">
        {/* Now playing + transport */}
        <Card className="lc-now" padding={false}>
          <div className="lc-now-body">
            <div className="lc-now-thumb">{currentId != null ? <VideoThumb src={current?.thumb_url}/> : <Film size={30}/>}</div>
            <div className="lc-now-info">
              {liveProduct && <div className="lc-now-product" title={liveProduct.name}><Package size={14}/><span>{liveProduct.name}</span></div>}
              <h2 title={current?.display_name}>{current?.display_name ?? (running ? 'Video không có trong thư viện' : 'Chưa phát video nào')}</h2>
              <div className="lc-now-tags">
                {running && <Badge>Video {(status?.index ?? 0) + 1}/{status?.count ?? 0}</Badge>}
                {mediaState && <Badge tone={mediaState.tone}>{mediaState.label}</Badge>}
                {status?.loop && running && <Badge tone="info">Lặp</Badge>}
              </div>
              <SeekBar cursor={cursor} duration={duration} disabled={busy || offline || !running} onSeek={seek}/>
            </div>
          </div>
          <div className="lc-transport">
            <div className="lc-transport-group">
              {!single && <Button variant="outline" leftIcon={<SkipBack size={17}/>} disabled={busy || offline || !running} onClick={()=>void previous()} title="Video trước (←)">Trước</Button>}
              <Button className="lc-play" leftIcon={status?.paused ? <Play size={18}/> : <Pause size={18}/>} disabled={busy || offline || !running} onClick={()=>void togglePause()} title="Tạm dừng / tiếp tục (Space)">{status?.paused ? 'Tiếp tục' : 'Tạm dừng'}</Button>
              {!single && <Button variant="outline" rightIcon={<SkipForward size={17}/>} disabled={busy || offline || !running} onClick={()=>void next()} title="Video tiếp (→)">Tiếp</Button>}
            </div>
            <Button className="lc-stop" variant="danger" leftIcon={<Square size={16}/>} disabled={busy || offline || !running} onClick={()=>setConfirmStop(true)}>{activeSession ? 'Kết thúc' : 'Dừng'}</Button>
          </div>
          {pendingProduct && <div className="lc-pending">
            <span title={pendingProduct.name}>Sau video này sẽ chuyển sang <b>{pendingProduct.name}</b></span>
            <Button size="sm" variant="ghost" leftIcon={<Undo2 size={14}/>} disabled={busy} onClick={()=>void act('/live/product-session/cancel-switch', post(), 'Đã hủy chuyển sản phẩm')}>Hủy</Button>
          </div>}
          <div className="lc-shortcuts"><span><Kbd>Space</Kbd> tạm dừng</span>{!single && <><span><Kbd>←</Kbd> trước</span><span><Kbd>→</Kbd> tiếp</span></>}<span>Kéo thanh thời gian để tua{single ? ' (lịch ghim tự tính lại theo vị trí mới)' : ''}</span></div>
        </Card>

        {activeSession && single && <TimelinePinPanel key={activeSession.id} session={activeSession}
          productName={id=>productById.get(id)?.name ?? `Sản phẩm #${id}`}/>}

        {activeSession && !single && <PlaybackSettings key={activeSession.id} sessionId={activeSession.id} rotation={status?.rotation} agentPolicy={status?.policy}
          productName={id=>productById.get(id)?.name ?? `Sản phẩm #${id}`}/>}

        {activeSession && <SessionLiveChat key={activeSession.id} sessionId={activeSession.id} handle="" agent={agent}/>}

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
                <span className="lc-q-info"><strong title={video?.display_name}>{video?.display_name ?? 'Video không có trong thư viện'}</strong><small>{durationLabel(video?.duration_seconds) ?? '—'}</small></span>
                {isCurrent ? <span className="lc-q-action"><Badge tone="danger" dot>Đang phát</Badge></span>
                  : <span className="lc-q-action"><Button size="sm" variant="ghost" leftIcon={<Play size={14}/>} disabled={busy} onClick={()=>void act('/live/select', post({index:i}), 'Đã phát video được chọn')}>Phát</Button></span>}
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
          {!single && <div className="lc-pin-panel">
            <div><strong>Sản phẩm đang phát:</strong> {liveProduct?.name ?? '—'}</div>
            <div><strong>Đang ghim trên TikTok:</strong> {pinnedProduct?.name ?? status?.pin?.card_name ?? status?.pin?.product_id ?? (pinObserved ? 'Chưa ghim' : 'Chưa xác định')}
              {status?.pin?.status && status.pin.status !== 'IDLE' && <Badge tone={status.pin.status === 'FAILED' ? 'danger' : status.pin.status === 'SUCCESS' ? 'success' : 'warning'}>{status.pin.status === 'PENDING' ? 'Đang xử lý' : status.pin.status === 'SUCCESS' ? 'Thành công' : 'Thất bại'}</Badge>}
            </div>
            {status?.pin?.error && <p className="lc-pin-error">{status.pin.error}</p>}
            {pinSelection && !pinSelection.tiktok_product_id && <p className="lc-pin-note">Chưa có TikTok Product ID; Agent sẽ đối chiếu tên, ảnh và giá trên LIVE Console.</p>}
            <div className="lc-pin-actions">
              <Button size="sm" disabled={pinBusy || offline || !running || !pinSelection} title={pinSelection?.name} onClick={()=>void previewPin()}>Ghim sản phẩm đã chọn</Button>
              <Button size="sm" variant="outline" disabled={pinBusy || offline} onClick={()=>void unpin()}>Bỏ ghim</Button>
              <label><input type="checkbox" checked={!!status?.pin?.auto_enabled} disabled={pinBusy || offline} onChange={()=>void toggleAutoPin()}/> Tự động ghim theo video</label>
            </div>
          </div>}
          <div className="lc-products-search"><SearchInput value={productQuery} onChange={e=>setProductQuery(e.target.value)} placeholder="Tìm trong phiên hoặc toàn bộ sản phẩm…" aria-label="Tìm sản phẩm"/></div>
          <ul className="lc-product-list">
            {inSession.map(p=>productRow(p, false))}
            {q && !inSession.length && <li className="lc-muted">Không có sản phẩm khớp trong phiên.</li>}
            {outside.length > 0 && <li className="lc-group">Ngoài danh sách phiên</li>}
            {outside.map(p=>productRow(p, true))}
            {!q && <li className="lc-muted lc-small">Cần sản phẩm khác? Gõ tên để tìm trong toàn bộ danh mục.</li>}
          </ul>
          <div className="lc-product-actions">
            {single ? <p className="lc-muted">Phiên một video: Sarae tự ghim sản phẩm theo lịch ghim. Muốn đổi sản phẩm hay thời điểm, hãy sửa lịch ghim (áp dụng ngay khi kích hoạt).</p>
            : !targetProduct ? <p className="lc-muted">Chọn sản phẩm muốn đưa lên LIVE — không cần theo thứ tự.</p> : <>
              <p className="lc-target" title={targetProduct.name}>Đưa <b>{targetProduct.name}</b> lên LIVE{targetOutside ? ' (sẽ thêm vào phiên)' : ''}</p>
              <Button block leftIcon={<SkipForward size={16}/>} disabled={busy || !targetProduct.video_count} onClick={()=>void switchProduct(targetProduct.id, 'immediate')}>Chuyển ngay</Button>
              <Button block variant="outline" disabled={busy || !targetProduct.video_count || targetProduct.id === status?.pending_product_id} onClick={()=>void switchProduct(targetProduct.id, 'after_current')}>Chuyển sau video hiện tại</Button>
              {!targetProduct.video_count && <small className="lc-tip">Sản phẩm chưa có video nên chưa thể đưa lên LIVE; vẫn có thể ghim trên TikTok.</small>}
            </>}
            {!single && <small className="lc-tip">Chọn sản phẩm trong danh sách để ghim thủ công; khi bật tự động ghim, Agent ghim sau khi video chuyển thành công.</small>}
          </div>
        </> : running ? <div className="lc-pad">
          <Alert tone="warning" title="Đang phát ngoài Phiên LIVE">
            Video đang phát không gắn với Phiên LIVE nào nên không có danh sách sản phẩm và không được lưu lịch sử. Dừng phát rồi bắt đầu lại từ một phiên đã chuẩn bị.
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
          <EmptyState icon={<CalendarClock size={28}/>} title="Chưa có phiên nào được chuẩn bị" description={`Tạo phiên trên trang Phiên LIVE, chọn TikTok Account; khi bắt đầu chọn máy ${agent.hostname}, đồng bộ LIVE Console và xác nhận sản phẩm.`}
            actionLabel="Tạo phiên LIVE" onAction={()=>{window.location.hash = '/sessions'}}/>
        </div> : <>
          <p className="lc-muted lc-pad">Bắt đầu một phiên tại trang Phiên LIVE: Agent kiểm tra đúng TikTok Account, đồng bộ giỏ sản phẩm rồi bạn xác nhận danh sách trước khi phát.</p>
          <ul className="lc-product-list">{drafts.map(s=><li key={s.id}><a className="lc-draft-link" href={sessionHref(s.id)}>
            <span className="lc-p-text"><strong title={s.name}>{s.name}</strong><small>{s.product_ids.length} sản phẩm · mở để bắt đầu</small></span>
            {!s.product_ids.length ? <span className="lc-p-badge"><Badge tone="warning">Trống</Badge></span> : null}
          </a></li>)}</ul>
        </>}</>}
      </Card>
    </div>

    <p className="lc-disclaimer">Bản thử nghiệm chỉ điều khiển Media Source trong OBS; không tự bật Start Streaming. Kiểm tra trên OBS Preview trước khi phát thật.</p>

    <Modal open={obsOpen} title="Trạng thái OBS" size="md" onClose={()=>setObsOpen(false)} footer={<Button onClick={()=>setObsOpen(false)}>Đóng</Button>}>
      <div className="media-form">
        <Alert tone="info" title="Kết nối OBS được quản lý tại Windows Agent">
          Trên máy <strong>{agent.hostname}</strong>, mở Sarae Windows Agent → Cài đặt → OBS WebSocket để nhập Host, Port và mật khẩu rồi chọn “Lưu & kết nối”. Sarae Web chỉ theo dõi trạng thái và gửi lệnh điều khiển LIVE.
        </Alert>
        <p><strong>Trạng thái:</strong> {offline ? 'Agent offline' : status?.connected ? 'Đã kết nối OBS' : 'OBS chưa kết nối'}</p>
        {status?.obs && <p><strong>Media Source:</strong> {status.obs.dual_mode ? status.obs.dual_sources?.join(' · ') || 'A/B' : status.obs.input_name}</p>}
        {status?.error && <p className="muted small selectable">{status.error}</p>}
      </div>
    </Modal>

    <ConfirmModal open={confirmStop} loading={busy}
      title={activeSession ? 'Kết thúc phiên LIVE?' : 'Dừng phát?'}
      description={activeSession ? `OBS sẽ dừng phát và “${activeSession.name}” được lưu vào lịch sử. Không thể tiếp tục phiên sau khi kết thúc.` : 'Media Source trong OBS sẽ dừng phát.'}
      confirmLabel={activeSession ? 'Kết thúc phiên' : 'Dừng phát'}
      onClose={()=>setConfirmStop(false)} onConfirm={()=>{setConfirmStop(false);void perform(()=>apiRequest(onAgent('/live/stop', agentId), post()), activeSession ? 'Đã kết thúc phiên LIVE' : 'Đã dừng phát').then(()=>loadAll())}}/>
    <Modal open={pinCandidates.length > 1} title="Xác nhận product card trên TikTok" size="md" onClose={()=>{setPinCandidates([]);setPinTarget(null)}} footer={<Button variant="outline" onClick={()=>{setPinCandidates([]);setPinTarget(null)}}>Hủy</Button>}>
      <p>Có nhiều card trùng thông tin. Chọn đúng card cho <strong>{pinTarget?.name}</strong>; thứ tự dưới đây chỉ dùng để xác nhận thao tác này.</p>
      <ul className="lc-pin-candidates">{pinCandidates.map(card=><li key={card.index}>
        {card.image_url && <img src={card.image_url} alt={`Ảnh sản phẩm ${card.name}`}/>}
        <span><strong>{card.name}</strong><small>{card.price_text || 'Không có giá'} · Card #{card.index + 1}{card.product_ids.length ? ` · ID ${card.product_ids.join(', ')}` : ''}</small></span>
        <Button size="sm" disabled={pinBusy || !pinTarget} onClick={()=>pinTarget && void sendPin(pinTarget,card.token)}>Chọn card này</Button>
      </li>)}</ul>
    </Modal>
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
