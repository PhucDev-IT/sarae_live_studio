import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { Alert, Badge, Button, Card, ConfirmModal, EmptyState, Input, Kbd, SegmentedControl, Select, Switch, useToast } from './ui/core'
import {
  ArrowLeft, Check, CheckCircle2, CircleAlert, Copy, Crosshair, Info, Pause, Pin, PinOff, Play, Plus, Rocket, Save, StickyNote, Trash2, ZoomIn,
} from 'lucide-react'
import { api, apiRequest, assetUrl, errorText } from './mediaApi'
import { listCloudMedia, type CloudMedia } from './cloudMediaApi'
import {
  ACTION_LABEL, TIMELINE_STATUS, hms, isSingle, parseHms, sessionHref, timelinesApi,
  type LiveProduct, type LiveSession, type MarkerAction, type Timeline, type TimelineIssue, type TimelineMarker,
} from './liveSessionsApi'
import TimelineAIPanel from './TimelineAIPanel'
import { overlaysApi, type OverlayAsset, type OverlayTemplates } from './overlaysApi'
import { EXTRA_PRESETS, endOf, fromPreset, MIN_DURATION_MS, type Overlay, type OverlayIssue, type OverlayPreset, type OverlayScene } from './overlay/overlayModel'
import { blocksSave, copyOverlay, hasClipboard, localIssues, pastedOverlay, useOverlayDraft } from './overlay/overlayDraft'
import { OverlayPreview, OverlayTracks, type PreviewMode } from './overlay/OverlayStage'
import OverlayPanel from './overlay/OverlayPanel'
import OverlayReadinessPanel from './overlay/OverlayReadinessPanel'
import './timeline-editor.css'
import './overlay/overlay-editor.css'

const ZOOMS = [1, 2, 4, 8, 16, 32]
const STEP_MS = 1000
const sessionIdFromHash = () => {
  const id = Number(new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('session'))
  return Number.isInteger(id) && id > 0 ? id : null
}
const isTyping = (target:EventTarget|null) => target instanceof HTMLElement && (target.isContentEditable || ['INPUT','TEXTAREA','SELECT'].includes(target.tagName))
/** Markers are kept in the order they take effect: position, then the order given (the later one wins). */
const sorted = (list:TimelineMarker[]) => list.map((m, i)=>({m, i})).sort((a, b)=>a.m.at_ms - b.m.at_ms || a.i - b.i).map(x=>x.m)
const blankMarker = (media_id:string, at_ms:number):TimelineMarker => ({media_id, at_ms, action:'PIN_PRODUCT', product_id:null, label:'', source:'MANUAL', review_status:'APPROVED', confidence:null, evidence:null})
type Confirm = null | {kind:'discard'; next:number} | {kind:'activate'|'delete'}

/**
 * Pin timeline editor of a Single Video session (#/timeline?session=ID): pick one of the session's videos, preview it,
 * place its markers on its timeline (drag, or type an exact HH:MM:SS), choose the product each one pins, save the draft
 * (every video's markers), approve and activate a version. Approved versions are read-only: edit a new draft made from
 * them.
 *
 * The same version holds the overlays drawn over the video (docs/LIVE_OVERLAYS.md): the preview shows them through the
 * shared OverlayRenderer at the current position, overlay tracks sit under the pin timeline, and the "Overlay" tab edits
 * them (blocks, box, timing, animations) with undo / redo. Saving the draft saves markers and overlays.
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
  const [media,setMedia] = useState<CloudMedia[]>([])
  // The video whose markers are shown and edited (markers of the other videos are kept as they are).
  const [videoId,setVideoId] = useState<string|null>(null)
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
  // ----- overlays of the version shown (every video), with undo / redo
  const draft = useOverlayDraft()
  const {overlays, reset:resetOverlays} = draft
  // The overlays as last loaded or saved ("discard changes" goes back to them).
  const timelineOverlays = useRef<Overlay[]>([])
  const [serverOverlayIssues,setServerOverlayIssues] = useState<OverlayIssue[]>([])
  const [ovSelected,setOvSelected] = useState<number|null>(null)
  const [templates,setTemplates] = useState<OverlayTemplates|null>(null)
  const [assets,setAssets] = useState<OverlayAsset[]|null>(null)
  const [tab,setTab] = useState<'pins'|'overlays'>('pins')
  const [previewMode,setPreviewMode] = useState<PreviewMode>('obs')
  const [showZones,setShowZones] = useState(false)
  const [canPaste,setCanPaste] = useState(hasClipboard)
  const loadTemplates = useCallback(()=>{void overlaysApi.templates().then(setTemplates).catch(()=>{})},[])
  useEffect(()=>{
    loadTemplates()
    void overlaysApi.assets().then(setAssets).catch(()=>setAssets([]))
  },[loadTemplates])

  const load = useCallback(async (keepVersion?:number|null)=>{
    if (sessionId == null) return
    const [s, list, catalog, media] = await Promise.all([
      apiRequest<LiveSession>(`/live/sessions/${sessionId}`), timelinesApi.list(sessionId).catch(()=>[] as Timeline[]),
      apiRequest<LiveProduct[]>('/live/catalog-products'), listCloudMedia().then(r=>r.items),
    ])
    setSession(s);setTimelines(list);setProducts(catalog);setMedia(media)
    setVideoId(current=>current && s.media_ids.includes(current) ? current : s.media_ids[0] ?? null)
    const pick = list.find(t=>t.id === keepVersion) ?? list.find(t=>t.status === 'DRAFT') ?? list.find(t=>t.status === 'ACTIVE') ?? list[0]
    setVersionId(pick?.id ?? null)
    setRevision(r=>r + 1)
    setLoaded(true)
  },[sessionId])
  useEffect(()=>{void load().catch(e=>{setLoaded(true);toast.push({tone:'danger', title:'Không tải được timeline', description:errorText(e, '')})})},[load,toast])
  useEffect(()=>{
    if (sessionId == null || versionId == null) {setTimeline(null);setMarkers([]);resetOverlays([]);setServerOverlayIssues([]);return}
    let alive = true
    void Promise.all([timelinesApi.get(sessionId, versionId), overlaysApi.list(sessionId, versionId)]).then(([t, view])=>{
      if (!alive) return
      setTimeline(t);setMarkers(t.markers ?? []);setDirty(false);setSelected(null)
      timelineOverlays.current = view.overlays;resetOverlays(view.overlays);setServerOverlayIssues(view.issues);setOvSelected(null)
    }).catch(e=>toast.push({tone:'danger', title:'Không tải được phiên bản', description:errorText(e, '')}))
    return ()=>{alive = false}
  },[sessionId, versionId, revision, toast, resetOverlays])

  // ----- the preview player
  const player = useRef<HTMLVideoElement>(null)
  const [now,setNow] = useState(0)        // ms
  const [playing,setPlaying] = useState(false)
  const [mediaDuration,setMediaDuration] = useState<number|null>(null)
  // Position where playback pauses by itself (previewing an overlay's animation).
  const stopAt = useRef<number|null>(null)
  const mediaById = useMemo(()=>new Map(media.map(v=>[v.id, v])),[media])
  const video = videoId ? mediaById.get(videoId) ?? null : null
  const duration = mediaDuration ?? (video?.duration_seconds != null ? video.duration_seconds * 1000 : null)
  const durationOf = (mediaId:string|null) => {
    if (mediaId === videoId) return duration
    const seconds = mediaId ? mediaById.get(mediaId)?.duration_seconds : null
    return seconds != null ? seconds * 1000 : null
  }
  // The signed URL changes on every list call; keep the first one per video so a reload does not restart playback.
  const streamUrl = useMemo(()=>assetUrl(video?.stream_url) ?? undefined,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [video?.id])
  useEffect(()=>{
    setMediaDuration(null);setNow(0);setPlaying(false)
    const el = player.current
    if (!el) return
    let frame = 0
    const tick = () => {
      setNow(el.currentTime * 1000)
      if (stopAt.current != null && el.currentTime * 1000 >= stopAt.current) {stopAt.current = null;el.pause()}
      if (!el.paused) frame = requestAnimationFrame(tick)
    }
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
  const toggle = () => {const el = player.current;stopAt.current = null;if (!el) return;if (el.paused) void el.play();else el.pause()}

  // ----- markers
  const editable = timeline?.status === 'DRAFT' && session?.status !== 'ENDED'
  const productById = useMemo(()=>new Map(products.map(p=>[p.id, p])),[products])
  const change = (next:TimelineMarker[]) => {setMarkers(next);setDirty(true)}
  // The markers of the video shown, with their index in `markers` (the whole draft).
  const shown = markers.map((m, i)=>({m, i})).filter(x=>x.m.media_id === videoId)
  const localIndex = (list:TimelineMarker[], index:number) => list.slice(0, index).filter(m=>m.media_id === list[index]?.media_id).length
  const update = (index:number, patch:Partial<TimelineMarker>) => change(markers.map((m, i)=>i === index ? {...m, ...patch, ...(patch.action && patch.action !== 'PIN_PRODUCT' ? {product_id:null} : {})} : m))
  const addAt = (ms:number) => {
    if (!videoId) return
    const at = Math.round(Math.max(0, Math.min(ms, duration ?? ms)) / STEP_MS) * STEP_MS
    const next = sorted([...markers, blankMarker(videoId, at)])
    change(next)
    setSelected(next.findIndex(m=>m.media_id === videoId && m.at_ms === at && m.product_id == null && m.action === 'PIN_PRODUCT'))
  }
  const remove = (index:number) => {change(markers.filter((_, i)=>i !== index));setSelected(null)}
  const moveTo = (index:number, ms:number) => {
    const at = Math.round(Math.max(0, Math.min(ms, duration ?? ms)) / STEP_MS) * STEP_MS
    if (at === markers[index]?.at_ms) return localIndex(markers, index)
    const moved = {...markers[index], at_ms:at}
    const rest = markers.filter((_, i)=>i !== index)
    const next = sorted([...rest, moved])
    change(next)
    const to = next.indexOf(moved)
    setSelected(to)
    return localIndex(next, to)  // its place among the markers of its video (the timeline bar)
  }
  /** What the editor itself can tell before saving; the server repeats these checks and adds its own. */
  const local:TimelineIssue[] = markers.flatMap((m, i)=>[
    ...(m.action === 'PIN_PRODUCT' && m.product_id == null ? [{level:'error' as const, code:'NO_PRODUCT', marker:i, message:'Chưa chọn sản phẩm để ghim'}] : []),
    ...(m.product_id != null && !productById.has(m.product_id) ? [{level:'error' as const, code:'PRODUCT_MISSING', marker:i, message:`Sản phẩm #${m.product_id} không có trong catalog`}] : []),
    ...((durationOf(m.media_id) ?? Infinity) < m.at_ms ? [{level:'error' as const, code:'AFTER_END', marker:i, message:'Mốc nằm sau cuối video'}] : []),
  ])
  const issues = dirty ? local : [...local, ...(timeline?.issues ?? []).filter(i=>!local.some(l=>l.code === i.code && l.marker === i.marker))]
  const markerIssues = (i:number) => issues.filter(x=>x.marker === i)

  // ----- overlays
  const presets = useMemo<OverlayPreset[]>(()=>{
    const list = templates?.templates ?? []
    // Built-in presets, then the owner's own templates (marked `owner`): all only starting points.
    return [...list, ...EXTRA_PRESETS.filter(p=>!list.some(t=>t.template === p.template)),
      ...(templates?.owner_templates ?? []).map(t=>({...t, owner:true}))]
  },[templates])
  /** Snapshot-shaped input of the renderer: the draft's overlays with the catalog products and images they can read. */
  const scene = useMemo<OverlayScene>(()=>({
    overlays,
    products:Object.fromEntries(products.map(p=>[String(p.id), {name:p.name, sku:p.sku, price:p.price ?? null, origin_price:p.origin_price ?? null, image_url:p.image_url}])),
    assets:Object.fromEntries((assets ?? []).map(a=>[a.id, a])),
  }),[overlays, products, assets])
  const overlayLocal = localIssues(overlays, {durationOf, hasProduct:id=>productById.has(id), hasAsset:id=>assets ? assets.some(a=>a.id === id) : null,
    maxConcurrent:templates?.max_concurrent ?? 4})
  const overlayIssues = draft.dirty ? overlayLocal
    : [...overlayLocal, ...serverOverlayIssues.filter(i=>!overlayLocal.some(l=>l.code === i.code && l.overlay === i.overlay && l.media_id === i.media_id))]
  const overlayBlocking = overlayLocal.some(blocksSave)
  const blocking = issues.some(i=>i.level === 'error') || overlayIssues.some(i=>i.level === 'error')
  const unsaved = dirty || draft.dirty
  const selectOverlay = (index:number|null) => {setOvSelected(index);if (index != null) setTab('overlays')}
  /** Start and length that fit in the video shown. */
  const fitTime = (start:number, length:number) => {
    if (duration == null) return {start_ms:start, duration_ms:length}
    const duration_ms = Math.max(MIN_DURATION_MS, Math.min(length, duration))
    return {start_ms:Math.max(0, Math.min(start, duration - duration_ms)), duration_ms}
  }
  /** The product pinned at `ms` of the video shown (the last pin marker before it), the default of a new overlay. */
  const pinnedAt = (ms:number) => {
    const last = shown.map(x=>x.m).filter(m=>m.at_ms <= ms && m.action !== 'NO_ACTION').at(-1)
    return last?.action === 'PIN_PRODUCT' ? last.product_id : null
  }
  const addOverlay = (preset:OverlayPreset|null) => {
    if (!videoId) return
    const created = fromPreset(preset, videoId, 0, pinnedAt(now))
    const next = {...created, ...fitTime(Math.round(now / 100) * 100, created.duration_ms)}
    draft.set(list=>[...list, next])
    selectOverlay(overlays.length)
  }
  const changeOverlay = (index:number, next:Overlay, field:string) => draft.set(list=>list.map((o, i)=>i === index ? next : o), `${index}-${field}`)
  const removeOverlay = (index:number) => {draft.set(list=>list.filter((_, i)=>i !== index));setOvSelected(null)}
  const duplicateOverlay = (index:number) => {
    const {id:_id, ...source} = overlays[index]
    const copy:Overlay = structuredClone(source)
    // Right after the original when it fits, else at the same time slightly lower.
    if (duration == null || endOf(copy) + copy.duration_ms <= duration) copy.start_ms = endOf(copy)
    else copy.layout = {...copy.layout, y:Math.min(1 - copy.layout.height, copy.layout.y + 0.03)}
    draft.set(list=>[...list.slice(0, index + 1), copy, ...list.slice(index + 1)])
    selectOverlay(index + 1)
  }
  const copySelected = (index:number) => {copyOverlay(overlays[index]);setCanPaste(true);toast.push({tone:'success', title:'Đã sao chép overlay', description:'Dán (Ctrl+V) tại vị trí đang xem, ở video hoặc phiên bản nháp khác.'})}
  const paste = () => {
    if (!videoId) return
    const pasted = pastedOverlay(videoId, Math.round(now / 100) * 100, duration)
    if (!pasted) return
    draft.set(list=>[...list, pasted])
    selectOverlay(overlays.length)
  }
  /** Plays the overlay's entry (or exit) from just before it, then pauses: the animations run from the clock. */
  const previewAnimation = (index:number, part:'in'|'out') => {
    const o = overlays[index], el = player.current
    if (!o || !el) return
    const from = part === 'in' ? o.start_ms - 400 : endOf(o) - o.animation_out.duration_ms - 600
    seek(Math.max(0, from))
    stopAt.current = part === 'in' ? o.start_ms + o.animation_in.duration_ms + 800 : endOf(o) + 400
    void el.play()
  }

  const run = async (action:()=>Promise<unknown>, success:string) => {
    setBusy(true)
    try {await action();toast.push({tone:'success', title:success});return true}
    catch(e){toast.push({tone:'danger', title:'Không thực hiện được', description:errorText(e, '')});return false}
    finally{setBusy(false)}
  }
  // Markers and overlays are saved separately (separate tables); each only when it changed.
  const save = () => timeline && run(async ()=>{
    if (dirty) {
      const saved = await timelinesApi.saveMarkers(timeline.session_id, timeline.id, markers)
      setTimeline(saved);setMarkers(saved.markers ?? []);setDirty(false)
      setTimelines(list=>list.map(t=>t.id === saved.id ? {...t, marker_count:saved.marker_count, updated_at:saved.updated_at} : t))
    }
    if (draft.dirty) {
      const view = await overlaysApi.save(timeline.session_id, timeline.id, overlays)
      timelineOverlays.current = view.overlays;resetOverlays(view.overlays);setServerOverlayIssues(view.issues)
      // The version's own issues (OVERLAY_ERRORS) follow its overlays.
      const fresh = await timelinesApi.get(timeline.session_id, timeline.id)
      setTimeline(t=>t ? {...t, issues:fresh.issues, overlay_count:fresh.overlay_count, updated_at:fresh.updated_at} : fresh)
      setTimelines(list=>list.map(t=>t.id === fresh.id ? {...t, overlay_count:fresh.overlay_count, updated_at:fresh.updated_at} : t))
    }
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
  const chooseVersion = (id:number) => {if (id === versionId) return;if (unsaved) setConfirm({kind:'discard', next:id});else setVersionId(id)}
  const onConfirm = () => {
    const c = confirm
    setConfirm(null)
    if (!c) return
    if (c.kind === 'discard') {setDirty(false);setVersionId(c.next)}
    if (c.kind === 'activate') void activate()
    if (c.kind === 'delete') void removeDraft()
  }

  // Keyboard: Space play/pause, M add a marker here, ←/→ 1 s (Shift 10 s), Delete removes the selected marker (or the
  // selected overlay in the Overlay tab). Overlay tab: Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y undo / redo, Ctrl+C / V / D.
  useEffect(()=>{
    const onKey = (e:KeyboardEvent) => {
      if (isTyping(e.target) || document.querySelector('.ui-modal__backdrop')) return
      if ((e.ctrlKey || e.metaKey) && !e.altKey) {
        if (tab !== 'overlays') return
        const key = e.key.toLowerCase()
        const has = ovSelected != null && overlays[ovSelected]?.media_id === videoId
        const shortcuts:Record<string, ()=>void> = {
          ...(has ? {c:()=>copySelected(ovSelected!)} : {}),
          ...(editable ? {z:e.shiftKey ? draft.redo : draft.undo, y:draft.redo, v:paste, ...(has ? {d:()=>duplicateOverlay(ovSelected!)} : {})} : {}),
        }
        if (shortcuts[key]) {e.preventDefault();shortcuts[key]()}
        return
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const step = e.shiftKey ? 10_000 : 1000
      const actions:Record<string, ()=>void> = {
        ' ':toggle, ArrowLeft:()=>seek(now - step), ArrowRight:()=>seek(now + step),
        ...(editable ? {m:()=>addAt(now), M:()=>addAt(now), Delete:()=>{
          if (tab === 'overlays') {if (ovSelected != null) removeOverlay(ovSelected)}
          else if (selected != null) remove(selected)
        }} : {}),
      }
      const action = actions[e.key]
      if (action) {e.preventDefault();action()}
    }
    window.addEventListener('keydown', onKey)
    return ()=>window.removeEventListener('keydown', onKey)
  })
  // Leaving the page with unsaved markers or overlays.
  useEffect(()=>{
    if (!unsaved) return
    const warn = (e:BeforeUnloadEvent) => {e.preventDefault()}
    window.addEventListener('beforeunload', warn)
    return ()=>window.removeEventListener('beforeunload', warn)
  },[unsaved])

  if (sessionId == null) return <Card><EmptyState title="Chưa chọn phiên LIVE" description="Mở Timeline Editor từ một phiên ở chế độ Video + lịch ghim." actionLabel="Về Phiên LIVE" onAction={()=>{window.location.hash = '/sessions'}}/></Card>
  if (!loaded || !session) return <Card><p className="te-muted">Đang tải…</p></Card>
  if (!isSingle(session)) return <Alert tone="warning" title="Phiên này dùng chế độ nâng cao">Lịch ghim theo thời gian chỉ dùng cho phiên Video + lịch ghim. <a href={sessionHref(session.id)}>Về phiên</a></Alert>
  if (!session.media_ids.length || !videoId) return <Alert tone="warning" title="Phiên chưa có video">Chọn video ở trang phiên trước khi tạo lịch ghim. <a href={sessionHref(session.id)}>Về phiên</a></Alert>

  const status = timeline ? TIMELINE_STATUS[timeline.status] : null
  const versionOptions = timelines.map(t=>({value:String(t.id), label:`Phiên bản ${t.version} · ${TIMELINE_STATUS[t.status].label} · ${t.marker_count} mốc${t.overlay_count ? ` · ${t.overlay_count} overlay` : ''}`}))
  const shownOverlays = overlays.filter(o=>o.media_id === videoId).length
  const readOnlyAction = !timeline ? <Alert tone="info">Tạo lịch ghim (bản nháp) trước để thêm overlay.</Alert>
    : (timeline.status === 'APPROVED' || timeline.status === 'ACTIVE') && session.status !== 'ENDED'
      ? <Alert tone="info" title={timeline.status === 'ACTIVE' ? 'Phiên bản đang dùng: chỉ xem' : 'Phiên bản đã duyệt: chỉ xem'}>
          <p className="te-muted">Tạo bản nháp mới từ phiên bản này (chép cả mốc và overlay) rồi sửa; phiên bản đang dùng giữ nguyên tới khi bạn kích hoạt bản mới.</p>
          <Button size="sm" variant="outline" leftIcon={<Copy size={14}/>} disabled={busy} onClick={()=>void newDraft(timeline.id)}>Sửa thành bản nháp mới</Button>
        </Alert> : undefined
  // The session's videos, then those that only markers of this version still name (removed from the session).
  const videoTabs = [...session.media_ids, ...new Set(markers.map(m=>m.media_id).filter((id):id is string=>!!id && !session.media_ids.includes(id)))]
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
          <Button variant="outline" leftIcon={<Save size={15}/>} loading={busy && unsaved} disabled={busy || !unsaved || local.some(i=>i.level === 'error') || overlayBlocking}
            title={overlayBlocking ? 'Overlay còn lỗi (xem tab Overlay)' : undefined} onClick={()=>void save()}>Lưu nháp</Button>
          <Button leftIcon={<Check size={15}/>} disabled={busy || unsaved || blocking || !markers.length} title={unsaved ? 'Lưu nháp trước khi duyệt' : blocking ? 'Sửa các lỗi trước khi duyệt' : undefined} onClick={()=>void approve()}>Duyệt</Button>
        </>}
        {timeline && (timeline.status === 'APPROVED' || timeline.status === 'ACTIVE') && <>
          <Button variant="outline" leftIcon={<Copy size={15}/>} disabled={busy || session.status === 'ENDED'} onClick={()=>void newDraft(timeline.id)}>Sửa thành bản nháp mới</Button>
          {timeline.status === 'APPROVED' && <Button leftIcon={<Rocket size={15}/>} disabled={busy || session.status === 'ENDED' || blocking} onClick={()=>setConfirm({kind:'activate'})}>Kích hoạt</Button>}
        </>}
        {!timeline && <Button leftIcon={<Plus size={15}/>} disabled={busy || session.status === 'ENDED'} onClick={()=>void newDraft()}>Tạo lịch ghim</Button>}
      </div>
    </div>

    {videoTabs.length > 1 && <div className="te-videos" role="tablist" aria-label="Video của phiên">
      {videoTabs.map((id, n)=>{
        const item = mediaById.get(id)
        const count = markers.filter(m=>m.media_id === id).length
        const removed = !session.media_ids.includes(id)
        return <button key={id} type="button" role="tab" aria-selected={id === videoId} className={'te-video-tab' + (id === videoId ? ' is-on' : '') + (removed ? ' is-removed' : '')}
          onClick={()=>{player.current?.pause();setSelected(null);setOvSelected(null);setVideoId(id)}} title={removed ? 'Video không còn trong phiên: các mốc của nó bị bỏ qua khi LIVE' : undefined}>
          <span className="te-video-no">{removed ? '!' : n + 1}</span>
          <span className="te-video-name">{item ? item.display_name || item.filename : 'Video không còn trong thư viện'}</span>
          <small>{count} mốc · {overlays.filter(o=>o.media_id === id).length} overlay{item?.duration_seconds != null ? ` · ${hms(item.duration_seconds * 1000)}` : ''}</small>
        </button>
      })}
    </div>}

    {/* Player and timeline: the 9:16 output frame (the video fitted inside, never cropped or stretched) with the overlays
        drawn on top by the shared renderer; the source video is never changed. */}
    <Card className="te-stage" padding={false}>
      <div className="te-stage-body">
        <div className="ove-preview">
          <div className="ove-preview-tools">
            <SegmentedControl<PreviewMode> value={previewMode} onChange={setPreviewMode} items={[{value:'obs', label:'OBS Preview'}, {value:'tiktok', label:'TikTok Viewer Preview'}]}/>
            <Switch label="Vùng an toàn" checked={showZones} onChange={e=>setShowZones(e.target.checked)}/>
          </div>
          <OverlayPreview scene={scene} mediaId={videoId} now={now} animate={playing} apiBase={api} mode={previewMode} showZones={showZones}
            selected={ovSelected} editable={!!editable} onSelect={selectOverlay} onGestureEnd={draft.seal}
            onLayout={(index, layout, gesture)=>draft.set(list=>list.map((o, i)=>i === index ? {...o, layout} : o), gesture)}>
            {streamUrl ? <video ref={player} src={streamUrl} preload="metadata" crossOrigin="anonymous" playsInline onClick={toggle}/> : <p className="te-muted">Không mở được video này trong Thư viện.</p>}
          </OverlayPreview>
          {previewMode === 'tiktok'
            ? <p className="ove-note"><Info size={14}/>Mô phỏng gần đúng giao diện người xem TikTok (bình luận, thẻ sản phẩm, thanh công cụ): vị trí thật thay đổi theo phiên bản app, điện thoại và cài đặt, không chính xác 100%.</p>
            : <p className="ove-note"><Info size={14}/>Khung OBS thật 1080×1920. Khi tạm dừng, overlay hiện đầy đủ để dễ chỉnh; hiệu ứng vào/ra chạy khi phát.</p>}
        </div>
        <div className="te-stage-side">
          <div className="te-clock"><strong>{hms(now)}</strong><span>/ {hms(duration)}</span></div>
          <div className="te-transport">
            <Button variant="outline" leftIcon={playing ? <Pause size={16}/> : <Play size={16}/>} disabled={!streamUrl} onClick={toggle}>{playing ? 'Tạm dừng' : 'Phát'}</Button>
            {editable && <Button leftIcon={<Plus size={16}/>} disabled={!duration} onClick={()=>addAt(now)}>Thêm mốc tại {hms(now)}</Button>}
          </div>
          <TimelineBar duration={duration} now={now} markers={shown.map(x=>x.m)} selected={shown.findIndex(x=>x.i === selected)} editable={!!editable} productName={id=>productById.get(id)?.name}
            onSeek={seek} onSelect={n=>{setSelected(shown[n].i);seek(shown[n].m.at_ms)}} onMove={(n, ms)=>moveTo(shown[n].i, ms)}/>
          <OverlayTracks overlays={overlays} mediaId={videoId} duration={duration} now={now} selected={ovSelected} editable={!!editable} presets={presets} issues={overlayIssues}
            onSelect={selectOverlay} onSeek={seek} onGestureEnd={draft.seal}
            onTime={(index, start_ms, duration_ms, gesture)=>draft.set(list=>list.map((o, i)=>i === index ? {...o, start_ms, duration_ms} : o), gesture)}/>
          <div className="te-shortcuts"><span><Kbd>Space</Kbd> phát/dừng</span><span><Kbd>←</Kbd><Kbd>→</Kbd> 1 giây (Shift: 10 giây)</span>{editable && <><span><Kbd>M</Kbd> thêm mốc</span><span><Kbd>Delete</Kbd> xóa mốc / overlay đang chọn</span></>}<span>Bấm mốc để tua tới đó{editable ? ', kéo để dời' : ''}</span>
            {editable && <span>Overlay: kéo trên khung để dời, kéo tay nắm để đổi cỡ, kéo thanh track để đổi giờ · <Kbd>Ctrl</Kbd>+<Kbd>Z</Kbd>/<Kbd>Y</Kbd> hoàn tác/làm lại · <Kbd>Ctrl</Kbd>+<Kbd>C</Kbd>/<Kbd>V</Kbd>/<Kbd>D</Kbd></span>}</div>
        </div>
      </div>
    </Card>

    <div className="ove-tabs">
      <SegmentedControl<'pins'|'overlays'> value={tab} onChange={setTab}
        items={[{value:'pins', label:`Mốc ghim (${shown.length})`}, {value:'overlays', label:`Overlay (${shownOverlays})`}]}/>
      {overlayIssues.some(i=>i.level === 'error') && <Badge tone="danger">Overlay có lỗi</Badge>}
    </div>

    {tab === 'overlays' && session && <OverlayReadinessPanel sessionId={session.id} sessionAgentId={session.agent_id ?? null}/>}
    {tab === 'overlays' ? <OverlayPanel overlays={overlays} selected={ovSelected} mediaId={videoId} now={now} editable={!!editable} dirty={draft.dirty} busy={busy}
      presets={presets} products={products} assets={assets ?? []} issues={overlayIssues} apiBase={api} canUndo={draft.canUndo} canRedo={draft.canRedo} canPaste={canPaste}
      blocking={overlayBlocking || local.some(i=>i.level === 'error')} onSelect={selectOverlay} onAdd={addOverlay} onChange={changeOverlay} onSeal={draft.seal}
      onRemove={removeOverlay} onDuplicate={duplicateOverlay} onCopy={copySelected} onPaste={paste} onUndo={draft.undo} onRedo={draft.redo}
      onPreview={previewAnimation} onSeek={seek} onSave={()=>void save()} onRevert={()=>{resetOverlays(timelineOverlays.current);setOvSelected(null)}}
      onAssetsChanged={setAssets} readOnlyAction={readOnlyAction} onTemplatesChanged={loadTemplates}/>
    : <div className="te-grid">
      <Card className="te-markers" padding={false} title={<span className="te-card-title"><Pin size={17}/>Các mốc của video này ({shown.length}{markers.length !== shown.length ? ` / ${markers.length} cả phiên` : ''})</span>}
        description={editable ? 'Mỗi mốc ghim hoặc bỏ ghim một sản phẩm từ thời điểm đó của video. Sang video mới, sản phẩm đang ghim được giữ tới mốc đầu của video đó. Hai mốc trùng giờ: mốc sau được áp dụng.' : 'Phiên bản đã duyệt chỉ xem; muốn sửa hãy tạo bản nháp mới.'}>
        {timeline?.note && <p className="te-note"><StickyNote size={14}/>{timeline.note}</p>}
        {issues.length > 0 && <ul className="te-issues">{issues.filter(i=>i.marker == null).map(i=><li key={i.code} className={'is-' + i.level}><CircleAlert size={14}/>{i.message}</li>)}</ul>}
        {!timeline ? <EmptyState icon={<Pin size={26}/>} title="Chưa có lịch ghim" description="Tạo lịch ghim để đặt các mốc ghim sản phẩm theo thời gian của video."/>
          : !shown.length ? <EmptyState icon={<Plus size={26}/>} title="Video này chưa có mốc nào" description={editable ? 'Tua video tới chỗ giới thiệu sản phẩm rồi bấm “Thêm mốc”, hoặc nhấn M.' : 'Phiên bản này không có mốc cho video này.'}/>
          : <ol className="te-list">{shown.map(({m, i}, n)=><MarkerRow key={`${i}-${m.at_ms}-${m.id ?? 'new'}`} marker={m} index={n} selected={selected === i} editable={!!editable}
              issues={markerIssues(i)} productOptions={productOptions} product={m.product_id != null ? productById.get(m.product_id) : undefined} duration={duration} now={now}
              onSelect={()=>{setSelected(i);seek(m.at_ms)}} onChange={patch=>update(i, patch)} onMove={ms=>moveTo(i, ms)} onRemove={()=>remove(i)}/>)}</ol>}
        {dirty && <div className="te-savebar"><span><CircleAlert size={15}/>Có thay đổi chưa lưu</span>
          <Button size="sm" variant="ghost" disabled={busy} onClick={()=>{setMarkers(timeline?.markers ?? []);setDirty(false)}}>Hoàn tác</Button>
          <Button size="sm" loading={busy} disabled={local.some(i=>i.level === 'error')} onClick={()=>void save()}>Lưu nháp</Button></div>}
      </Card>

      <TimelineAIPanel sessionId={session.id} mediaId={videoId} disabled={session.status === 'ENDED'} onSeek={seek}
        onDraft={id=>{if (unsaved) {toast.push({tone:'warning', title:'Bản nháp đang sửa chưa lưu', description:'Mở bản nháp AI sau khi lưu hoặc hoàn tác.'});void load(versionId)} else void load(id)}}/>
    </div>}

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
