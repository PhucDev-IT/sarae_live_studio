import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { Alert, Badge, Button, Card, ConfirmModal, EmptyState, Input, Kbd, Select, useToast } from './ui/core'
import {
  ArrowLeft, Check, CheckCircle2, CircleAlert, Copy, Crosshair, Pause, Pin, PinOff, Play, Plus, Rocket, Save, StickyNote, Trash2, ZoomIn,
} from 'lucide-react'
import { apiRequest, assetUrl, errorText } from './mediaApi'
import { listCloudMedia, type CloudMedia } from './cloudMediaApi'
import {
  ACTION_LABEL, TIMELINE_STATUS, hms, isSingle, parseHms, sessionHref, timelinesApi,
  type LiveProduct, type LiveSession, type MarkerAction, type Timeline, type TimelineIssue, type TimelineMarker,
} from './liveSessionsApi'
import TimelineAIPanel from './TimelineAIPanel'
import './timeline-editor.css'

const ZOOMS = [1, 2, 4, 8, 16, 32]
const STEP_MS = 1000
const sessionIdFromHash = () => {
  const id = Number(new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('session'))
  return Number.isInteger(id) && id > 0 ? id : null
}
const isTyping = (target:EventTarget|null) => target instanceof HTMLElement && (target.isContentEditable || ['INPUT','TEXTAREA','SELECT'].includes(target.tagName))
/** Markers are kept in the order they take effect: position, then the order given (the later one wins). */
const sorted = (list:TimelineMarker[]) => list.map((m, i)=>({m, i})).sort((a, b)=>a.m.at_ms - b.m.at_ms || a.i - b.i).map(x=>x.m)
const blankMarker = (at_ms:number):TimelineMarker => ({at_ms, action:'PIN_PRODUCT', product_id:null, label:'', source:'MANUAL', review_status:'APPROVED', confidence:null, evidence:null})
type Confirm = null | {kind:'discard'; next:number} | {kind:'activate'|'delete'}

/**
 * Pin timeline editor of a Single Video session (#/timeline?session=ID): preview the main video, place markers on its
 * timeline (drag, or type an exact HH:MM:SS), choose the product each one pins, save the draft, approve and activate a
 * version. Approved versions are read-only: edit a new draft made from them.
 */
export default function TimelineEditor(){
  const toast = useToast()
  const [sessionId,setSessionId] = useState<number|null>(sessionIdFromHash)
  useEffect(()=>{
    const sync = () => setSessionId(sessionIdFromHash())
    window.addEventListener('hashchange', sync)
    return ()=>window.removeEventListener('hashchange', sync)
  },[])
  const [session,setSession] = useState<LiveSession|null>(null)
  const [video,setVideo] = useState<CloudMedia|null>(null)
  const [products,setProducts] = useState<LiveProduct[]>([])
  const [timelines,setTimelines] = useState<Timeline[]>([])
  const [versionId,setVersionId] = useState<number|null>(null)
  const [timeline,setTimeline] = useState<Timeline|null>(null)
  const [markers,setMarkers] = useState<TimelineMarker[]>([])
  const [dirty,setDirty] = useState(false)
  const [selected,setSelected] = useState<number|null>(null)
  const [busy,setBusy] = useState(false)
  const [loaded,setLoaded] = useState(false)
  const [confirm,setConfirm] = useState<Confirm>(null)
  // Bumped by every reload, so the version shown is fetched again even when it stays selected (approved, activated).
  const [revision,setRevision] = useState(0)

  const load = useCallback(async (keepVersion?:number|null)=>{
    if (sessionId == null) return
    const [s, list, catalog, media] = await Promise.all([
      apiRequest<LiveSession>(`/live/sessions/${sessionId}`), timelinesApi.list(sessionId).catch(()=>[] as Timeline[]),
      apiRequest<LiveProduct[]>('/live/catalog-products'), listCloudMedia().then(r=>r.items),
    ])
    setSession(s);setTimelines(list);setProducts(catalog)
    setVideo(media.find(v=>v.id === s.main_media_id) ?? null)
    const pick = list.find(t=>t.id === keepVersion) ?? list.find(t=>t.status === 'DRAFT') ?? list.find(t=>t.status === 'ACTIVE') ?? list[0]
    setVersionId(pick?.id ?? null)
    setRevision(r=>r + 1)
    setLoaded(true)
  },[sessionId])
  useEffect(()=>{void load().catch(e=>{setLoaded(true);toast.push({tone:'danger', title:'Không tải được timeline', description:errorText(e, '')})})},[load,toast])
  useEffect(()=>{
    if (sessionId == null || versionId == null) {setTimeline(null);setMarkers([]);return}
    let alive = true
    void timelinesApi.get(sessionId, versionId).then(t=>{if (!alive) return;setTimeline(t);setMarkers(t.markers ?? []);setDirty(false);setSelected(null)})
      .catch(e=>toast.push({tone:'danger', title:'Không tải được phiên bản', description:errorText(e, '')}))
    return ()=>{alive = false}
  },[sessionId, versionId, revision, toast])

  // ----- the preview player
  const player = useRef<HTMLVideoElement>(null)
  const [now,setNow] = useState(0)        // ms
  const [playing,setPlaying] = useState(false)
  const [mediaDuration,setMediaDuration] = useState<number|null>(null)
  const duration = timeline?.duration_ms ?? mediaDuration ?? (video?.duration_seconds != null ? video.duration_seconds * 1000 : null)
  // The signed URL changes on every list call; keep the first one per video so a reload does not restart playback.
  const streamUrl = useMemo(()=>assetUrl(video?.stream_url) ?? undefined,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [video?.id])
  useEffect(()=>{
    const el = player.current
    if (!el) return
    let frame = 0
    const tick = () => {setNow(el.currentTime * 1000);if (!el.paused) frame = requestAnimationFrame(tick)}
    const onPlay = () => {setPlaying(true);frame = requestAnimationFrame(tick)}
    const onPause = () => {setPlaying(false);cancelAnimationFrame(frame);setNow(el.currentTime * 1000)}
    const onMeta = () => {if (Number.isFinite(el.duration) && el.duration > 0) setMediaDuration(Math.round(el.duration * 1000))}
    const onSeeked = () => setNow(el.currentTime * 1000)
    el.addEventListener('play', onPlay);el.addEventListener('pause', onPause);el.addEventListener('loadedmetadata', onMeta);el.addEventListener('seeked', onSeeked)
    return ()=>{cancelAnimationFrame(frame);el.removeEventListener('play', onPlay);el.removeEventListener('pause', onPause)
      el.removeEventListener('loadedmetadata', onMeta);el.removeEventListener('seeked', onSeeked)}
  },[streamUrl])
  const seek = (ms:number) => {
    const el = player.current
    const value = Math.max(0, Math.min(ms, duration ?? ms))
    setNow(value)
    if (el) el.currentTime = value / 1000
  }
  const toggle = () => {const el = player.current;if (!el) return;if (el.paused) void el.play();else el.pause()}
  // The frame takes the video's own proportions: a 1080×1920 TikTok video is shown portrait, never cropped or stretched.
  const videoRatio = video?.width && video?.height ? `${video.width} / ${video.height}` : '9 / 16'

  // ----- markers
  const editable = timeline?.status === 'DRAFT' && session?.status !== 'ENDED'
  const productById = useMemo(()=>new Map(products.map(p=>[p.id, p])),[products])
  const change = (next:TimelineMarker[]) => {setMarkers(next);setDirty(true)}
  const update = (index:number, patch:Partial<TimelineMarker>) => change(markers.map((m, i)=>i === index ? {...m, ...patch, ...(patch.action && patch.action !== 'PIN_PRODUCT' ? {product_id:null} : {})} : m))
  const addAt = (ms:number) => {
    const at = Math.round(Math.max(0, Math.min(ms, duration ?? ms)) / STEP_MS) * STEP_MS
    const next = sorted([...markers, blankMarker(at)])
    change(next)
    setSelected(next.findIndex(m=>m.at_ms === at && m.product_id == null && m.action === 'PIN_PRODUCT'))
  }
  const remove = (index:number) => {change(markers.filter((_, i)=>i !== index));setSelected(null)}
  const moveTo = (index:number, ms:number) => {
    const at = Math.round(Math.max(0, Math.min(ms, duration ?? ms)) / STEP_MS) * STEP_MS
    if (at === markers[index]?.at_ms) return index
    const moved = {...markers[index], at_ms:at}
    const rest = markers.filter((_, i)=>i !== index)
    const next = sorted([...rest, moved])
    change(next)
    const to = next.indexOf(moved)
    setSelected(to)
    return to
  }
  /** What the editor itself can tell before saving; the server repeats these checks and adds its own. */
  const local:TimelineIssue[] = markers.flatMap((m, i)=>[
    ...(m.action === 'PIN_PRODUCT' && m.product_id == null ? [{level:'error' as const, code:'NO_PRODUCT', marker:i, message:'Chưa chọn sản phẩm để ghim'}] : []),
    ...(m.product_id != null && !productById.has(m.product_id) ? [{level:'error' as const, code:'PRODUCT_MISSING', marker:i, message:`Sản phẩm #${m.product_id} không có trong catalog`}] : []),
    ...(duration != null && m.at_ms > duration ? [{level:'error' as const, code:'AFTER_END', marker:i, message:'Mốc nằm sau cuối video'}] : []),
  ])
  const issues = dirty ? local : [...local, ...(timeline?.issues ?? []).filter(i=>!local.some(l=>l.code === i.code && l.marker === i.marker))]
  const markerIssues = (i:number) => issues.filter(x=>x.marker === i)
  const blocking = issues.some(i=>i.level === 'error')

  const run = async (action:()=>Promise<unknown>, success:string) => {
    setBusy(true)
    try {await action();toast.push({tone:'success', title:success});return true}
    catch(e){toast.push({tone:'danger', title:'Không thực hiện được', description:errorText(e, '')});return false}
    finally{setBusy(false)}
  }
  const save = () => timeline && run(async ()=>{
    const saved = await timelinesApi.saveMarkers(timeline.session_id, timeline.id, markers)
    setTimeline(saved);setMarkers(saved.markers ?? []);setDirty(false)
    setTimelines(list=>list.map(t=>t.id === saved.id ? {...t, marker_count:saved.marker_count, updated_at:saved.updated_at} : t))
  }, 'Đã lưu bản nháp')
  const approve = () => timeline && run(async ()=>{
    await timelinesApi.approve(timeline.session_id, timeline.id);await load(timeline.id)
  }, 'Đã duyệt phiên bản ' + timeline.version)
  const activate = () => timeline && run(async ()=>{
    const result = await timelinesApi.activate(timeline.session_id, timeline.id)
    await load(timeline.id)
    if (result.sent === false && result.agent_error) toast.push({tone:'warning', title:'Chưa gửi được tới Agent', description:result.agent_error})
  }, session?.status === 'RUNNING' ? 'Đã kích hoạt — Agent áp dụng ngay tại vị trí đang phát' : 'Đã kích hoạt phiên bản ' + timeline.version)
  const newDraft = (copyFrom?:number) => sessionId != null && run(async ()=>{
    const created = await timelinesApi.create(sessionId, copyFrom);await load(created.id)
  }, 'Đã tạo bản nháp mới')
  const removeDraft = () => timeline && run(async ()=>{
    await timelinesApi.remove(timeline.session_id, timeline.id);await load()
  }, 'Đã xóa bản nháp')
  const chooseVersion = (id:number) => {if (id === versionId) return;if (dirty) setConfirm({kind:'discard', next:id});else setVersionId(id)}
  const onConfirm = () => {
    const c = confirm
    setConfirm(null)
    if (!c) return
    if (c.kind === 'discard') {setDirty(false);setVersionId(c.next)}
    if (c.kind === 'activate') void activate()
    if (c.kind === 'delete') void removeDraft()
  }

  // Keyboard: Space play/pause, M add a marker here, ←/→ 1 s (Shift 10 s), Delete removes the selected marker.
  useEffect(()=>{
    const onKey = (e:KeyboardEvent) => {
      if (isTyping(e.target) || e.ctrlKey || e.metaKey || e.altKey || document.querySelector('.ui-modal__backdrop')) return
      const step = e.shiftKey ? 10_000 : 1000
      const actions:Record<string, ()=>void> = {
        ' ':toggle, ArrowLeft:()=>seek(now - step), ArrowRight:()=>seek(now + step),
        ...(editable ? {m:()=>addAt(now), M:()=>addAt(now), Delete:()=>{if (selected != null) remove(selected)}} : {}),
      }
      const action = actions[e.key]
      if (action) {e.preventDefault();action()}
    }
    window.addEventListener('keydown', onKey)
    return ()=>window.removeEventListener('keydown', onKey)
  })
  // Leaving the page with unsaved markers.
  useEffect(()=>{
    if (!dirty) return
    const warn = (e:BeforeUnloadEvent) => {e.preventDefault()}
    window.addEventListener('beforeunload', warn)
    return ()=>window.removeEventListener('beforeunload', warn)
  },[dirty])

  if (sessionId == null) return <Card><EmptyState title="Chưa chọn phiên LIVE" description="Mở Timeline Editor từ một phiên ở chế độ Một video + lịch ghim." actionLabel="Về Phiên LIVE" onAction={()=>{window.location.hash = '/sessions'}}/></Card>
  if (!loaded || !session) return <Card><p className="te-muted">Đang tải…</p></Card>
  if (!isSingle(session)) return <Alert tone="warning" title="Phiên này dùng chế độ nâng cao">Lịch ghim theo thời gian chỉ dùng cho phiên Một video + lịch ghim. <a href={sessionHref(session.id)}>Về phiên</a></Alert>
  if (!session.main_media_id) return <Alert tone="warning" title="Phiên chưa có video chính">Chọn video chính ở trang phiên trước khi tạo lịch ghim. <a href={sessionHref(session.id)}>Về phiên</a></Alert>

  const status = timeline ? TIMELINE_STATUS[timeline.status] : null
  const versionOptions = timelines.map(t=>({value:String(t.id), label:`Phiên bản ${t.version} · ${TIMELINE_STATUS[t.status].label} · ${t.marker_count} mốc`}))
  const productOptions = [{value:'', label:'Chọn sản phẩm…'}, ...products.map(p=>({value:String(p.id), label:`${p.name}${p.sku ? ' · ' + p.sku : ''}${p.status === 'INACTIVE' ? ' (tạm ẩn)' : ''}${p.tiktok_product_id ? '' : ' · chưa đồng bộ TikTok'}`}))]

  return <div className="te-page">
    <div className="te-head">
      <a className="te-back" href={sessionHref(session.id)}><ArrowLeft size={16}/>{session.name}</a>
      <div className="te-version">
        {timelines.length > 0 && <Select aria-label="Phiên bản timeline" value={String(versionId ?? '')} onChange={v=>chooseVersion(Number(v))} options={versionOptions}/>}
        {status && <Badge tone={status.tone}>{status.label}</Badge>}
        {session.status === 'RUNNING' && <Badge tone="danger" dot>Phiên đang LIVE</Badge>}
      </div>
      <div className="te-actions">
        {editable && <>
          <Button variant="ghost" leftIcon={<Trash2 size={15}/>} disabled={busy} onClick={()=>setConfirm({kind:'delete'})}>Xóa nháp</Button>
          <Button variant="outline" leftIcon={<Save size={15}/>} loading={busy && dirty} disabled={busy || !dirty || local.some(i=>i.level === 'error')} onClick={()=>void save()}>Lưu nháp</Button>
          <Button leftIcon={<Check size={15}/>} disabled={busy || dirty || blocking || !markers.length} title={dirty ? 'Lưu nháp trước khi duyệt' : blocking ? 'Sửa các lỗi trước khi duyệt' : undefined} onClick={()=>void approve()}>Duyệt</Button>
        </>}
        {timeline && (timeline.status === 'APPROVED' || timeline.status === 'ACTIVE') && <>
          <Button variant="outline" leftIcon={<Copy size={15}/>} disabled={busy || session.status === 'ENDED'} onClick={()=>void newDraft(timeline.id)}>Sửa thành bản nháp mới</Button>
          {timeline.status === 'APPROVED' && <Button leftIcon={<Rocket size={15}/>} disabled={busy || session.status === 'ENDED' || blocking} onClick={()=>setConfirm({kind:'activate'})}>Kích hoạt</Button>}
        </>}
        {!timeline && <Button leftIcon={<Plus size={15}/>} disabled={busy || session.status === 'ENDED'} onClick={()=>void newDraft()}>Tạo lịch ghim</Button>}
      </div>
    </div>

    {/* Player and timeline: the video keeps its own proportions (portrait TikTok videos stay portrait). */}
    <Card className="te-stage" padding={false}>
      <div className="te-stage-body">
        <div className="te-video" style={{aspectRatio:videoRatio}}>
          {streamUrl ? <video ref={player} src={streamUrl} preload="metadata" crossOrigin="anonymous" playsInline onClick={toggle}/> : <p className="te-muted">Không mở được video chính trong Thư viện.</p>}
        </div>
        <div className="te-stage-side">
          <div className="te-clock"><strong>{hms(now)}</strong><span>/ {hms(duration)}</span></div>
          <div className="te-transport">
            <Button variant="outline" leftIcon={playing ? <Pause size={16}/> : <Play size={16}/>} disabled={!streamUrl} onClick={toggle}>{playing ? 'Tạm dừng' : 'Phát'}</Button>
            {editable && <Button leftIcon={<Plus size={16}/>} disabled={!duration} onClick={()=>addAt(now)}>Thêm mốc tại {hms(now)}</Button>}
          </div>
          <TimelineBar duration={duration} now={now} markers={markers} selected={selected} editable={!!editable} productName={id=>productById.get(id)?.name}
            onSeek={seek} onSelect={i=>{setSelected(i);seek(markers[i].at_ms)}} onMove={moveTo}/>
          <div className="te-shortcuts"><span><Kbd>Space</Kbd> phát/dừng</span><span><Kbd>←</Kbd><Kbd>→</Kbd> 1 giây (Shift: 10 giây)</span>{editable && <><span><Kbd>M</Kbd> thêm mốc</span><span><Kbd>Delete</Kbd> xóa mốc đang chọn</span></>}<span>Bấm mốc để tua tới đó{editable ? ', kéo để dời' : ''}</span></div>
        </div>
      </div>
    </Card>

    <div className="te-grid">
      <Card className="te-markers" padding={false} title={<span className="te-card-title"><Pin size={17}/>Các mốc ({markers.length})</span>}
        description={editable ? 'Mỗi mốc ghim hoặc bỏ ghim một sản phẩm từ thời điểm đó. Hai mốc trùng giờ: mốc sau được áp dụng.' : 'Phiên bản đã duyệt chỉ xem; muốn sửa hãy tạo bản nháp mới.'}>
        {timeline?.note && <p className="te-note"><StickyNote size={14}/>{timeline.note}</p>}
        {issues.length > 0 && <ul className="te-issues">{issues.filter(i=>i.marker == null).map(i=><li key={i.code} className={'is-' + i.level}><CircleAlert size={14}/>{i.message}</li>)}</ul>}
        {!timeline ? <EmptyState icon={<Pin size={26}/>} title="Chưa có lịch ghim" description="Tạo lịch ghim để đặt các mốc ghim sản phẩm theo thời gian của video."/>
          : !markers.length ? <EmptyState icon={<Plus size={26}/>} title="Chưa có mốc nào" description={editable ? 'Tua video tới chỗ giới thiệu sản phẩm rồi bấm “Thêm mốc”, hoặc nhấn M.' : 'Phiên bản này không có mốc.'}/>
          : <ol className="te-list">{markers.map((m, i)=><MarkerRow key={`${i}-${m.at_ms}-${m.id ?? 'new'}`} marker={m} index={i} selected={selected === i} editable={!!editable}
              issues={markerIssues(i)} productOptions={productOptions} product={m.product_id != null ? productById.get(m.product_id) : undefined} duration={duration} now={now}
              onSelect={()=>{setSelected(i);seek(m.at_ms)}} onChange={patch=>update(i, patch)} onMove={ms=>moveTo(i, ms)} onRemove={()=>remove(i)}/>)}</ol>}
        {dirty && <div className="te-savebar"><span><CircleAlert size={15}/>Có thay đổi chưa lưu</span>
          <Button size="sm" variant="ghost" disabled={busy} onClick={()=>{setMarkers(timeline?.markers ?? []);setDirty(false)}}>Hoàn tác</Button>
          <Button size="sm" loading={busy} disabled={local.some(i=>i.level === 'error')} onClick={()=>void save()}>Lưu nháp</Button></div>}
      </Card>

      <TimelineAIPanel sessionId={session.id} disabled={session.status === 'ENDED'} onSeek={seek}
        onDraft={id=>{if (dirty) {toast.push({tone:'warning', title:'Bản nháp đang sửa chưa lưu', description:'Mở bản nháp AI sau khi lưu hoặc hoàn tác.'});void load(versionId)} else void load(id)}}/>
    </div>

    <ConfirmModal open={!!confirm} loading={busy} onClose={()=>setConfirm(null)} onConfirm={onConfirm} tone={confirm?.kind === 'delete' ? 'danger' : 'primary'}
      title={confirm?.kind === 'discard' ? 'Bỏ thay đổi chưa lưu?' : confirm?.kind === 'delete' ? 'Xóa bản nháp?' : `Kích hoạt phiên bản ${timeline?.version}?`}
      description={confirm?.kind === 'discard' ? 'Các mốc vừa sửa của bản nháp này chưa được lưu.' : confirm?.kind === 'delete' ? 'Bản nháp và các mốc của nó sẽ bị xóa.'
        : session.status === 'RUNNING' ? 'Phiên đang LIVE: Agent đổi lịch ghim ngay, tính lại sản phẩm cần ghim tại vị trí đang phát. Video và OBS không bị khởi động lại.'
        : 'Phiên bản này sẽ được dùng khi phiên bắt đầu LIVE. Phiên bản đang dùng (nếu có) quay về trạng thái Đã duyệt.'}
      confirmLabel={confirm?.kind === 'discard' ? 'Bỏ thay đổi' : confirm?.kind === 'delete' ? 'Xóa' : 'Kích hoạt'}/>
  </div>
}

const ACTION_ICON:Record<MarkerAction, typeof Pin> = {PIN_PRODUCT:Pin, UNPIN_PRODUCT:PinOff, NO_ACTION:StickyNote}

/** One marker: exact time (HH:MM:SS), action, product, label; AI suggestions show their evidence and need a review. */
function MarkerRow({marker:m, index, selected, editable, issues, productOptions, product, duration, now, onSelect, onChange, onMove, onRemove}:{
  marker:TimelineMarker; index:number; selected:boolean; editable:boolean; issues:TimelineIssue[]; productOptions:{value:string;label:string}[]
  product?:LiveProduct; duration:number|null; now:number; onSelect:()=>void; onChange:(patch:Partial<TimelineMarker>)=>void; onMove:(ms:number)=>void; onRemove:()=>void
}){
  const [time,setTime] = useState(hms(m.at_ms))
  useEffect(()=>setTime(hms(m.at_ms)),[m.at_ms])
  const parsed = parseHms(time)
  const invalidTime = parsed == null || (duration != null && parsed > duration)
  const commit = () => {if (!invalidTime && parsed !== m.at_ms) onMove(parsed!);else setTime(hms(m.at_ms))}
  const Icon = ACTION_ICON[m.action]
  const evidence = useMemo(()=>{try{return m.evidence ? JSON.parse(m.evidence) as Record<string, unknown> : null}catch{return null}},[m.evidence])
  return <li className={'te-row is-' + m.action.toLowerCase() + (selected ? ' is-selected' : '') + (issues.some(i=>i.level === 'error') ? ' is-error' : '')}>
    <button type="button" className="te-row-jump" onClick={onSelect} title="Tua video tới mốc này"><Icon size={15}/><span>#{index + 1}</span></button>
    <div className="te-row-body">
      <div className="te-row-line">
        {editable ? <span className="te-time">
          <Input aria-label="Thời điểm (HH:MM:SS)" value={time} onChange={e=>setTime(e.target.value)} onBlur={commit}
            onKeyDown={(e:ReactKeyboardEvent<HTMLInputElement>)=>{if (e.key === 'Enter') {e.preventDefault();commit()}}} error={invalidTime ? 'Sai giờ' : undefined}/>
          <button type="button" className="te-icon-btn" title={`Đặt bằng vị trí hiện tại (${hms(now)})`} aria-label="Lấy vị trí hiện tại" onClick={()=>onMove(now)}><Crosshair size={15}/></button>
        </span> : <strong className="te-time-text">{hms(m.at_ms)}</strong>}
        {editable ? <Select aria-label="Hành động" value={m.action} onChange={v=>onChange({action:v as MarkerAction})}
            options={(Object.keys(ACTION_LABEL) as MarkerAction[]).map(a=>({value:a, label:ACTION_LABEL[a]}))}/>
          : <span>{ACTION_LABEL[m.action]}</span>}
        {editable && <button type="button" className="te-icon-btn is-danger" title="Xóa mốc" aria-label="Xóa mốc" onClick={onRemove}><Trash2 size={15}/></button>}
      </div>
      {m.action === 'PIN_PRODUCT' && (editable
        ? <Select aria-label="Sản phẩm" value={m.product_id != null ? String(m.product_id) : ''} onChange={v=>onChange({product_id:v ? Number(v) : null})} options={productOptions}/>
        : <span className="te-product">{product?.name ?? `Sản phẩm #${m.product_id}`}</span>)}
      {editable ? <Input aria-label="Ghi chú" placeholder="Ghi chú (không bắt buộc)" maxLength={200} value={m.label} onChange={e=>onChange({label:e.target.value})}/>
        : m.label && <small className="te-muted">{m.label}</small>}
      {m.source === 'AI' && <div className="te-ai">
        <Badge tone={m.review_status === 'APPROVED' ? 'success' : m.review_status === 'REJECTED' ? 'default' : 'warning'}>AI · {m.review_status === 'NEEDS_REVIEW' ? 'cần duyệt' : m.review_status === 'APPROVED' ? 'đã duyệt' : 'đã bỏ'}{m.confidence != null ? ` · ${Math.round(m.confidence * 100)}%` : ''}</Badge>
        {evidence && <small className="te-muted" title={JSON.stringify(evidence)}>{String(evidence.transcript ?? evidence.match_reason ?? '')}</small>}
        {editable && m.review_status !== 'APPROVED' && <Button size="sm" variant="outline" leftIcon={<CheckCircle2 size={14}/>} onClick={()=>onChange({review_status:'APPROVED'})}>Duyệt mốc</Button>}
        {editable && m.review_status === 'NEEDS_REVIEW' && <Button size="sm" variant="ghost" onClick={()=>onChange({review_status:'REJECTED'})}>Bỏ qua</Button>}
      </div>}
      {issues.map(i=><small key={i.code} className={'te-issue is-' + i.level}><CircleAlert size={13}/>{i.message}</small>)}
    </div>
  </li>
}

/**
 * The video's timeline with its markers: click/drag the track to seek, click a marker to jump to it, drag a marker to
 * move it (whole seconds). Zoom in for precise placement on long videos; the playhead stays in view.
 */
function TimelineBar({duration, now, markers, selected, editable, productName, onSeek, onSelect, onMove}:{
  duration:number|null; now:number; markers:TimelineMarker[]; selected:number|null; editable:boolean; productName:(id:number)=>string|undefined
  onSeek:(ms:number)=>void; onSelect:(index:number)=>void; onMove:(index:number, ms:number)=>number
}){
  const [zoom,setZoom] = useState(1)
  const scroller = useRef<HTMLDivElement>(null)
  const track = useRef<HTMLDivElement>(null)
  const dragging = useRef<{index:number;moved:boolean}|null>(null)
  const scrubbing = useRef(false)
  const [hover,setHover] = useState<number|null>(null)
  const total = duration ?? 0
  const pct = (ms:number) => total ? Math.max(0, Math.min(100, ms / total * 100)) : 0
  /** Keeps receiving the pointer outside the track; a pointer that is already gone has nothing to capture. */
  const capture = (pointerId:number) => {try{track.current?.setPointerCapture(pointerId)}catch{/* released already */}}
  const msAt = (clientX:number) => {
    const box = track.current?.getBoundingClientRect()
    return box && total ? Math.max(0, Math.min(total, (clientX - box.left) / box.width * total)) : 0
  }
  useEffect(()=>{
    const box = scroller.current
    if (!box || zoom === 1 || !total) return
    const x = now / total * box.scrollWidth
    if (x < box.scrollLeft + 40 || x > box.scrollLeft + box.clientWidth - 40) box.scrollLeft = x - box.clientWidth / 2
  },[now, zoom, total])
  // Tick marks every 1, 5, 10, 30 or 60 min depending on how much is visible.
  const visible = total / zoom
  const tickStep = [10_000, 30_000, 60_000, 300_000, 600_000, 1_800_000, 3_600_000].find(s=>visible / s <= 12) ?? 3_600_000
  const ticks = total ? Array.from({length:Math.floor(total / tickStep) + 1}, (_, i)=>i * tickStep) : []

  const onTrackDown = (e:ReactPointerEvent<HTMLDivElement>) => {
    if (!total || e.button !== 0) return
    capture(e.pointerId)
    scrubbing.current = true
    onSeek(msAt(e.clientX))
  }
  const onTrackMove = (e:ReactPointerEvent<HTMLDivElement>) => {
    const ms = msAt(e.clientX)
    setHover(ms)
    const drag = dragging.current
    if (drag) {drag.index = onMove(drag.index, ms);drag.moved = true;onSeek(Math.round(ms / STEP_MS) * STEP_MS);return}
    if (scrubbing.current) onSeek(ms)
  }
  const onTrackUp = () => {scrubbing.current = false;dragging.current = null}
  const onMarkerDown = (e:ReactPointerEvent<HTMLButtonElement>, index:number) => {
    e.stopPropagation()
    if (e.button !== 0) return
    onSelect(index)
    if (!editable) return
    capture(e.pointerId)
    dragging.current = {index, moved:false}
  }

  return <div className="te-bar">
    <div className="te-bar-tools">
      <ZoomIn size={15}/><span className="te-muted">Thu phóng</span>
      {ZOOMS.map(z=><button key={z} type="button" className={'te-zoom' + (z === zoom ? ' is-on' : '')} onClick={()=>setZoom(z)}>{z}×</button>)}
    </div>
    <div ref={scroller} className="te-scroller">
      <div ref={track} className={'te-track' + (total ? '' : ' is-disabled')} style={{width:`${zoom * 100}%`}}
        onPointerDown={onTrackDown} onPointerMove={onTrackMove} onPointerUp={onTrackUp} onPointerCancel={onTrackUp} onPointerLeave={()=>setHover(null)}
        role="slider" aria-label="Thanh thời gian video" aria-valuemin={0} aria-valuemax={Math.round(total / 1000)} aria-valuenow={Math.round(now / 1000)} aria-valuetext={hms(now)}>
        {ticks.map(t=><span key={t} className="te-tick" style={{left:`${pct(t)}%`}}><small>{hms(t)}</small></span>)}
        <span className="te-lane"/>
        {markers.map((m, i)=>{
          const name = m.product_id != null ? productName(m.product_id) : undefined
          return <button key={`${i}-${m.at_ms}`} type="button" className={'te-marker is-' + m.action.toLowerCase() + (i === selected ? ' is-selected' : '') + (m.review_status !== 'APPROVED' ? ' is-review' : '')}
            style={{left:`${pct(m.at_ms)}%`}} onPointerDown={e=>onMarkerDown(e, i)}
            title={`${hms(m.at_ms)} · ${ACTION_LABEL[m.action]}${name ? ' · ' + name : ''}`} aria-label={`Mốc ${i + 1} lúc ${hms(m.at_ms)}`}>
            <span>{i + 1}</span>
          </button>
        })}
        <span className="te-playhead" style={{left:`${pct(now)}%`}}/>
        {hover != null && <span className="te-hover" style={{left:`${pct(hover)}%`}}>{hms(hover)}</span>}
      </div>
    </div>
  </div>
}
