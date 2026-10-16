import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import {
  Alert, Badge, Button, Card, Collapse, ConfirmModal, EmptyState, Input, Kbd, Modal, PasswordInput, SearchInput, Select, Switch, useToast,
} from './ui/core'
import {
  CircleAlert, Clock, FastForward, Film, ListVideo, Package, Pause, Play, Radio, RefreshCw, Settings2, SkipBack, SkipForward, Square, Undo2,
  Wifi, WifiOff,
} from 'lucide-react'
import { apiRequest, durationLabel, errorText, type Media } from './mediaApi'
import VideoThumb, { type ClientMeta } from './VideoThumb'
import './media.css'
import './live-control.css'

type ObsSettings = { host:string; port:number; input_name:string; dual_mode:boolean; dual_sources?:string[]; has_password:boolean }
type ObsInput = { name:string; kind:string; in_scene:boolean }
type ObsInputs = { scene:string; inputs:ObsInput[] }
type LiveStatus = {
  connected:boolean; error?:string; obs_version?:string; media_state?:string; media_cursor?:number|null; media_duration?:number|null
  running:boolean; paused?:boolean; loop?:boolean; session_mode?:string; playlist_id:number|null; product_id?:number|null; pending_product_id?:number|null
  queue_ids?:number[]; index:number; count:number; last_status:string; retry_count?:number; error_count?:number; transition_count?:number
  recovery_count?:number; session_seconds?:number; obs?:ObsSettings
}
type LiveProduct = { id:number; name:string; sku:string; video_count:number }
type Playlist = { id:number; name:string; media_ids:number[] }

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

/** Operator console for a running OBS session: product queue, transport controls and connection health. */
export default function LiveControl(){
  const toast = useToast()
  const [status,setStatus] = useState<LiveStatus|null>(null)
  const [receivedAt,setReceivedAt] = useState(0)
  const [now,setNow] = useState(()=>Date.now())
  const [products,setProducts] = useState<LiveProduct[]>([])
  const [playlists,setPlaylists] = useState<Playlist[]>([])
  const [videos,setVideos] = useState<Media[]>([])
  const [meta,setMeta] = useState<Record<number,ClientMeta>>({})
  const [busy,setBusy] = useState(false)
  const [productQuery,setProductQuery] = useState('')
  const [target,setTarget] = useState<number|null>(null)
  const [confirmStop,setConfirmStop] = useState(false)

  const [obsOpen,setObsOpen] = useState(false)
  const [obsForm,setObsForm] = useState({host:'127.0.0.1', port:'4455', password:'', input_name:'Sarae Video', dual_mode:false, source_a:'', source_b:''})
  const [obsInputs,setObsInputs] = useState<ObsInputs|null>(null)
  const [inputsLoading,setInputsLoading] = useState(false)
  const [playlistId,setPlaylistId] = useState('')
  const [loop,setLoop] = useState(true)

  const applyStatus = (next:LiveStatus) => {setStatus(next);setReceivedAt(Date.now())}
  const pollStatus = useCallback(async ()=>{try{applyStatus(await apiRequest<LiveStatus>('/live/status'))}catch{/* keep last known status */}},[])
  const loadAll = useCallback(async ()=>{
    const [s, p, l, v] = await Promise.all([
      apiRequest<LiveStatus>('/live/status'), apiRequest<LiveProduct[]>('/live/products'),
      apiRequest<Playlist[]>('/live/playlists'), apiRequest<Media[]>('/media?limit=500'),
    ])
    applyStatus(s);setProducts(p);setPlaylists(l);setVideos(v)
  },[])
  useEffect(()=>{
    void loadAll().catch(e=>toast.push({tone:'danger',title:'Không tải được dữ liệu LIVE',description:errorText(e, '')}))
    const poll = window.setInterval(()=>void pollStatus(), POLL_MS)
    const tick = window.setInterval(()=>setNow(Date.now()), 500)
    return ()=>{window.clearInterval(poll);window.clearInterval(tick)}
  },[loadAll,pollStatus,toast])

  const act = async (path:string, init:RequestInit, success:string|null) => {
    setBusy(true)
    try{
      await apiRequest(path, init)
      if (success) toast.push({tone:'success',title:success})
      await pollStatus()
    }catch(e){toast.push({tone:'danger',title:'Không thực hiện được',description:errorText(e, '')})}
    finally{setBusy(false)}
  }

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
  const duration = status?.media_duration ?? (current?.duration_seconds != null ? current.duration_seconds * 1000 : undefined) ?? (currentId != null && meta[currentId]?.duration ? meta[currentId].duration! * 1000 : undefined)
  const playing = status?.media_state === 'OBS_MEDIA_STATE_PLAYING'
  const cursor = status?.media_cursor == null ? null : Math.min(status.media_cursor + (playing ? now - receivedAt : 0), duration ?? Infinity)

  const visibleProducts = products.filter(p=>(p.name + ' ' + p.sku).toLocaleLowerCase('vi').includes(productQuery.trim().toLocaleLowerCase('vi')))
  const targetProduct = target != null ? productById.get(target) : undefined

  const startProduct = (id:number) => act('/live/product-session/start', post({product_id:id}), 'Đã bắt đầu phiên LIVE')
  const switchProduct = (id:number, mode:'immediate'|'after_current') => act('/live/product-session/switch', post({product_id:id, mode}), mode === 'immediate' ? 'Đã chuyển sản phẩm' : 'Sẽ chuyển sau video hiện tại')
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
    try{setObsInputs(await apiRequest<ObsInputs>('/live/obs/inputs', post({host:obsForm.host, port:Number(obsForm.port), password:obsForm.password})))}
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
      const result = await apiRequest<LiveStatus>('/live/config', post({...obsForm, port:Number(obsForm.port)}))
      applyStatus(result)
      if (result.connected) {setObsOpen(false);toast.push({tone:'success',title:'Đã kết nối OBS',description:result.obs_version ? 'OBS ' + result.obs_version : undefined})}
      else toast.push({tone:'danger',title:'Chưa kết nối được OBS',description:result.error})
    }catch(e){toast.push({tone:'danger',title:'Không lưu được cấu hình',description:errorText(e, '')})}
    finally{setBusy(false)}
  }

  const reportMeta = (id:number, next:ClientMeta) => setMeta(prev=>prev[id]?.duration === next.duration ? prev : {...prev, [id]:{...prev[id], ...next}})
  const onAir = running && !status?.paused

  return <div className="lc-page">
    {/* Connection + session health */}
    <section className={'lc-status' + (onAir ? ' is-live' : '')} aria-live="polite">
      <div className="lc-status-main">
        <span className={'lc-onair' + (onAir ? ' is-on' : status?.paused ? ' is-paused' : '')}><Radio size={16}/>{onAir ? 'ĐANG PHÁT' : status?.paused ? 'TẠM DỪNG' : 'CHƯA PHÁT'}</span>
        <div className="lc-status-text">
          <strong>{liveProduct ? liveProduct.name : running ? `Playlist #${status?.playlist_id ?? '—'}` : 'Chưa có phiên phát'}</strong>
          <small>{status?.last_status || 'Chọn sản phẩm bên phải để bắt đầu phiên LIVE.'}</small>
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
            <div className="lc-now-thumb">{currentId != null ? <VideoThumb id={currentId} onMeta={m=>reportMeta(currentId,m)}/> : <Film size={30}/>}</div>
            <div className="lc-now-info">
              <div className="lc-now-tags">
                {liveProduct && <Badge tone="primary"><Package size={12}/>{liveProduct.name}</Badge>}
                {running && <Badge>Video {(status?.index ?? 0) + 1}/{status?.count ?? 0}</Badge>}
                {mediaState && <Badge tone={mediaState.tone}>{mediaState.label}</Badge>}
                {status?.loop && running && <Badge tone="info">Lặp</Badge>}
              </div>
              <h2 title={current?.filename}>{current?.filename ?? (running ? `Video #${currentId ?? '—'}` : 'Chưa phát video nào')}</h2>
              <SeekBar cursor={cursor} duration={duration} disabled={busy || !running} onSeek={seek}/>
            </div>
          </div>
          <div className="lc-transport">
            <Button variant="outline" leftIcon={<SkipBack size={17}/>} disabled={busy || !running} onClick={()=>void previous()} title="Video trước (←)">Trước</Button>
            <Button className="lc-play" leftIcon={status?.paused ? <Play size={18}/> : <Pause size={18}/>} disabled={busy || !running} onClick={()=>void togglePause()} title="Tạm dừng / tiếp tục (Space)">{status?.paused ? 'Tiếp tục' : 'Tạm dừng'}</Button>
            <Button variant="outline" rightIcon={<SkipForward size={17}/>} disabled={busy || !running} onClick={()=>void next()} title="Video tiếp (→)">Tiếp</Button>
            <Button className="lc-stop" variant="danger" leftIcon={<Square size={16}/>} disabled={busy || !running} onClick={()=>setConfirmStop(true)}>Dừng</Button>
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
            ? <EmptyState icon={<ListVideo size={28}/>} title="Chưa có hàng đợi" description="Bắt đầu phiên theo sản phẩm để xem và chọn video đang phát."/>
            : <ol className="lc-queue-list">{queue.map((id,i)=>{
              const video = videoById.get(id)
              const isCurrent = i === status?.index
              return <li key={`${id}-${i}`} className={isCurrent ? 'is-current' : ''}>
                <span className="lc-q-index">{isCurrent ? <Radio size={14}/> : i + 1}</span>
                <span className="lc-q-thumb"><VideoThumb id={id} onMeta={m=>reportMeta(id,m)}/></span>
                <span className="lc-q-info"><strong title={video?.filename}>{video?.filename ?? `Video #${id}`}</strong><small>{durationLabel(video?.duration_seconds ?? meta[id]?.duration) ?? '—'}{isCurrent ? ' · đang phát' : ''}</small></span>
                {!isCurrent && <Button size="sm" variant="ghost" leftIcon={<Play size={14}/>} disabled={busy} onClick={()=>void act('/live/select', post({index:i}), 'Đã phát video được chọn')}>Phát</Button>}
              </li>
            })}</ol>}
        </Card>
      </div>

      {/* Product picker */}
      <Card className="lc-products" padding={false} title={<span className="lc-card-title"><Package size={17}/>Sản phẩm LIVE</span>}
        description={productSession ? 'Chọn sản phẩm để chuyển, không cần dừng phiên' : 'Chọn sản phẩm để bắt đầu phiên'}
        actions={<Button variant="ghost" aria-label="Tải lại" title="Tải lại" onClick={()=>void loadAll()}><RefreshCw size={16}/></Button>}>
        <div className="lc-products-search"><SearchInput value={productQuery} onChange={e=>setProductQuery(e.target.value)} placeholder="Tìm sản phẩm…" aria-label="Tìm sản phẩm"/></div>
        <ul className="lc-product-list">{visibleProducts.map(p=>{
          const isLive = productSession && p.id === status?.product_id
          const isPending = p.id === status?.pending_product_id
          return <li key={p.id}><button type="button" className={(target === p.id ? 'is-selected ' : '') + (isLive ? 'is-live' : '')} disabled={!p.video_count} onClick={()=>setTarget(p.id)}>
            <span className="lc-p-text"><strong>{p.name}</strong><small>{p.sku} · {p.video_count ? `${p.video_count} video` : 'chưa có video'}</small></span>
            {isLive ? <Badge tone="danger" dot>LIVE</Badge> : isPending ? <Badge tone="warning">Chờ</Badge> : null}
          </button></li>
        })}{!visibleProducts.length && <li className="lc-muted">Không có sản phẩm khớp.</li>}</ul>
        <div className="lc-product-actions">
          {!targetProduct ? <p className="lc-muted">Chọn một sản phẩm trong danh sách.</p>
          : !running ? <Button block leftIcon={<Play size={16}/>} disabled={busy || !status?.connected} onClick={()=>void startProduct(targetProduct.id)}>Bắt đầu LIVE · {targetProduct.name}</Button>
          : productSession ? (targetProduct.id === status?.product_id
            ? <p className="lc-muted">{targetProduct.name} đang phát.</p>
            : <>
              <Button block leftIcon={<SkipForward size={16}/>} disabled={busy} onClick={()=>void switchProduct(targetProduct.id, 'immediate')}>Chuyển ngay</Button>
              <Button block variant="outline" disabled={busy} onClick={()=>void switchProduct(targetProduct.id, 'after_current')}>Chuyển sau video hiện tại</Button>
            </>)
          : <p className="lc-muted">Đang phát playlist thủ công. Dừng phát để bắt đầu phiên theo sản phẩm.</p>}
          {!status?.connected && targetProduct && !running && <small className="lc-hint">Cần kết nối OBS trước khi bắt đầu.</small>}
        </div>
      </Card>
    </div>

    <Collapse title="Phát thử playlist thủ công (chế độ phụ)">
      <div className="lc-manual">
        <Select label="Playlist" value={playlistId} onChange={setPlaylistId} options={[{value:'',label:playlists.length ? 'Chọn playlist…' : 'Chưa có playlist'},...playlists.map(p=>({value:String(p.id),label:`${p.name} · ${p.media_ids.length} video`}))]}/>
        <Switch label="Lặp playlist liên tục" checked={loop} onChange={e=>setLoop(e.target.checked)}/>
        <Button leftIcon={<Play size={16}/>} disabled={busy || !playlistId || running || !status?.connected} onClick={()=>void act('/live/start', post({playlist_id:Number(playlistId), start_index:0, loop}), 'Đã phát playlist lên OBS')}>Phát thử</Button>
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

    <ConfirmModal open={confirmStop} title="Dừng phát?" description="Media Source trong OBS sẽ dừng và phiên LIVE theo sản phẩm kết thúc." confirmLabel="Dừng phát" loading={busy}
      onClose={()=>setConfirmStop(false)} onConfirm={()=>{setConfirmStop(false);void act('/live/stop', post(), 'Đã dừng phát')}}/>
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
