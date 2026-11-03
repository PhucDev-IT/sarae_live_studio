import { useEffect, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import { ChevronDown, ChevronRight, Eye, EyeOff, Image as ImageIcon, Layers, Maximize2, Minus, Pin, PinOff, Plus, ScanSearch, StickyNote } from 'lucide-react'
import { assetUrl } from '../mediaApi'
import { ACTION_LABEL, hms, type LiveProduct, type MarkerAction, type TimelineMarker } from '../liveSessionsApi'
import { MIN_DURATION_MS, endOf, type Overlay, type OverlayIssue, type OverlayPreset } from '../overlay/overlayModel'
import { newStep, overlayName, secondsText } from '../overlay/overlayDraft'
import { showMsOf, type Appearance } from '../overlay/overlaySchedule'
import { ticksOf } from '../timelineView'
import { overlayThumb } from './thumbs'
import './studio-timeline.css'

/** Width of the sticky header column (group titles, row names); the time area starts after it. */
const HEAD = 196
const MIN_ZOOM = 1, MAX_ZOOM = 64
const PRESET_ZOOMS = [1, 2, 4, 8, 16, 32]
const MARKER_CARD = 196   // px a marker card takes on its lane
const SNAP_MS = 100
const MARKER_STEP_MS = 1000
const ACTION_ICON:Record<MarkerAction, typeof Pin> = {PIN_PRODUCT:Pin, UNPIN_PRODUCT:PinOff, NO_ACTION:StickyNote}

type MarkerItem = {marker:TimelineMarker; index:number}
type OverlayItem = {overlay:Overlay; index:number}
type Drag = {kind:'marker'; index:number} | {kind:'overlay'; index:number; mode:'move'|'start'|'end'; x:number; start:number; length:number; key:string} | {kind:'scrub'}

function Thumb({src, size = 28, icon}:{src?:string|null; size?:number; icon?:ReactNode}){
  const [broken,setBroken] = useState(false)
  return <span className="st-thumb" style={{width:size, height:size}}>
    {src && !broken ? <img src={src} alt="" loading="lazy" onError={()=>setBroken(true)}/> : icon ?? <ImageIcon size={Math.round(size * 0.5)}/>}
  </span>
}

/**
 * The editor's one timeline, laid out like a video editor: a ruler with the playhead, then collapsible groups of rows —
 * the video's pin markers (cards with the product's photo, on as many lanes as needed so none hides another) and one
 * row per overlay (eye to turn it on / off, status, a clip with its product photo, name and times; drag to move, edges
 * to trim). Press and drag on the ruler or any empty part to seek. Zoom with the presets, the slider or ⌘/Ctrl + wheel.
 * Random schedule (`plan` given): a clip is the window the overlay may show in (hatched, dragged the same way) and the
 * appearances of the chosen loop are the solid segments inside it.
 */
export default function StudioTimeline(p:{
  duration:number|null; now:number; zoom:number; onZoom:(zoom:number)=>void; editable:boolean
  markers:MarkerItem[]; selectedMarker:number|null; product:(id:number)=>LiveProduct|undefined
  overlays:OverlayItem[]; selectedOverlay:number|null; presets:OverlayPreset[]; issues:OverlayIssue[]; assetUrlOf:(id:string)=>string|undefined
  onSeek:(ms:number)=>void; onSelectMarker:(index:number)=>void; onMoveMarker:(index:number, ms:number)=>number
  onSelectOverlay:(index:number)=>void; onTimeOverlay:(index:number, start:number, length:number, gesture:string)=>void; onGestureEnd:()=>void
  onToggleOverlay:(index:number)=>void; onAddOverlay:()=>void
  /** Random schedule: the appearances of the loop shown (indexes into the whole overlay list); absent = fixed. */
  plan?:Appearance[]|null; iteration?:number
}){
  const scroller = useRef<HTMLDivElement>(null)
  const content = useRef<HTMLDivElement>(null)
  const drag = useRef<Drag|null>(null)
  const [width,setWidth] = useState(800)
  const [hover,setHover] = useState<number|null>(null)
  const [open,setOpen] = useState({pins:true, overlays:true})
  const total = p.duration ?? Math.max(60_000, ...p.overlays.map(x=>endOf(x.overlay)), ...p.markers.map(x=>x.marker.at_ms + 10_000))
  const lane = Math.max(200, (width - HEAD) * p.zoom)
  const x = (ms:number) => HEAD + Math.max(0, Math.min(total, ms)) / total * lane
  const msAt = (clientX:number) => {
    const box = content.current?.getBoundingClientRect()
    return box ? Math.max(0, Math.min(total, (clientX - box.left - HEAD) / lane * total)) : 0
  }
  useLayoutEffect(()=>{
    const el = scroller.current
    if (!el) return
    const fit = () => setWidth(el.clientWidth)
    fit()
    const observer = new ResizeObserver(fit)
    observer.observe(el)
    return ()=>observer.disconnect()
  },[])
  // Keep the playhead in view while playing or after a seek.
  useEffect(()=>{
    const el = scroller.current
    if (!el || p.zoom === 1) return
    const px = x(p.now)
    if (px < el.scrollLeft + HEAD + 30 || px > el.scrollLeft + el.clientWidth - 30) el.scrollLeft = px - HEAD - (el.clientWidth - HEAD) / 2
  // eslint-disable-next-line react-hooks/exhaustive-deps
  },[p.now, p.zoom, total, lane])
  // Ctrl / ⌘ + wheel zooms around the pointer.
  useEffect(()=>{
    const el = scroller.current
    if (!el) return
    const onWheel = (e:WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return
      e.preventDefault()
      p.onZoom(Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, p.zoom * (e.deltaY < 0 ? 1.25 : 0.8))))
    }
    el.addEventListener('wheel', onWheel, {passive:false})
    return ()=>el.removeEventListener('wheel', onWheel)
  },[p])

  // Marker cards on lanes: a card never covers another.
  const markerLanes:number[] = []
  const markerLane = new Map<number, number>()
  for (const item of [...p.markers].sort((a, b)=>a.marker.at_ms - b.marker.at_ms)) {
    const left = x(item.marker.at_ms)
    let n = markerLanes.findIndex(end=>end <= left)
    if (n < 0) {n = markerLanes.length;markerLanes.push(0)}
    markerLanes[n] = left + MARKER_CARD + 6
    markerLane.set(item.index, n)
  }
  const pinsHeight = Math.max(1, markerLanes.length) * 58 + 10

  const capture = (pointerId:number) => {try {content.current?.setPointerCapture(pointerId)} catch {/* pointer gone */}}
  const onDown = (e:ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || (e.target as HTMLElement).closest('.st-head, .st-add')) return
    capture(e.pointerId)
    drag.current = {kind:'scrub'}
    p.onSeek(msAt(e.clientX))
  }
  const onMarkerDown = (e:ReactPointerEvent, index:number) => {
    e.stopPropagation()
    if (e.button !== 0) return
    p.onSelectMarker(index)
    if (!p.editable) return
    capture(e.pointerId)
    drag.current = {kind:'marker', index}
  }
  const onClipDown = (e:ReactPointerEvent<HTMLButtonElement>, item:OverlayItem) => {
    e.stopPropagation()
    if (e.button !== 0) return
    p.onSelectOverlay(item.index)
    if (!p.editable) return
    const rect = e.currentTarget.getBoundingClientRect(), edge = Math.min(10, rect.width / 3)
    const mode = e.clientX - rect.left < edge ? 'start' : rect.right - e.clientX < edge ? 'end' : 'move'
    capture(e.pointerId)
    drag.current = {kind:'overlay', index:item.index, mode, x:e.clientX, start:item.overlay.start_ms, length:item.overlay.duration_ms, key:newStep('time')}
  }
  const onMove = (e:ReactPointerEvent) => {
    const ms = msAt(e.clientX)
    setHover(ms)
    const d = drag.current
    if (!d) return
    if (d.kind === 'scrub') {p.onSeek(ms);return}
    if (d.kind === 'marker') {
      const at = Math.round(ms / MARKER_STEP_MS) * MARKER_STEP_MS
      d.index = p.onMoveMarker(d.index, at)
      p.onSeek(at)
      return
    }
    const delta = Math.round((e.clientX - d.x) / lane * total / SNAP_MS) * SNAP_MS
    const limit = p.duration ?? Infinity
    let start = d.start, length = d.length
    if (d.mode === 'move') start = Math.max(0, Math.min(limit - length, d.start + delta))
    if (d.mode === 'start') {start = Math.max(0, Math.min(d.start + d.length - MIN_DURATION_MS, d.start + delta));length = d.start + d.length - start}
    if (d.mode === 'end') length = Math.max(MIN_DURATION_MS, Math.min(limit - d.start, d.length + delta))
    p.onTimeOverlay(d.index, Math.round(start), Math.round(length), d.key)
  }
  const onUp = () => {
    const d = drag.current
    drag.current = null
    if (d?.kind === 'overlay') p.onGestureEnd()
  }

  const ticks = ticksOf(total, p.zoom)
  const minor = ticks.length > 1 ? (ticks[1] - ticks[0]) / 5 : 0
  const zoomTo = (z:number) => p.onZoom(Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, z)))
  const fitSelection = () => {
    const sel = p.overlays.find(o=>o.index === p.selectedOverlay)?.overlay
    if (!sel) return
    zoomTo(total / Math.max(sel.duration_ms * 1.6, 5000))
    requestAnimationFrame(()=>{const el = scroller.current;if (el) el.scrollLeft = x(sel.start_ms) - HEAD - 40})
  }
  const sliderOf = (z:number) => Math.round(Math.log(z) / Math.log(MAX_ZOOM) * 100)

  return <div className="st">
    <div className="st-zoom">
      <div className="st-zoom-presets" role="group" aria-label="Thu phóng">
        {PRESET_ZOOMS.map(z=><button key={z} type="button" className={Math.abs(p.zoom - z) < 0.01 ? 'is-on' : ''} onClick={()=>zoomTo(z)}>{z}×</button>)}
      </div>
      <button type="button" className="st-icon" title="Vừa khung (cả video)" aria-label="Vừa khung" onClick={()=>zoomTo(1)}><Maximize2 size={15}/></button>
      <button type="button" className="st-icon" title="Phóng tới overlay đang chọn" aria-label="Phóng tới overlay đang chọn" disabled={p.selectedOverlay == null} onClick={fitSelection}><ScanSearch size={15}/></button>
      <button type="button" className="st-icon" title="Thu nhỏ" aria-label="Thu nhỏ" onClick={()=>zoomTo(p.zoom / 1.5)}><Minus size={15}/></button>
      <input className="st-slider" type="range" min={0} max={100} value={sliderOf(p.zoom)} aria-label="Mức thu phóng"
        onChange={e=>zoomTo(Math.pow(MAX_ZOOM, Number(e.target.value) / 100))}/>
      <button type="button" className="st-icon" title="Phóng to (Ctrl + lăn chuột)" aria-label="Phóng to" onClick={()=>zoomTo(p.zoom * 1.5)}><Plus size={15}/></button>
    </div>

    <div ref={scroller} className="st-scroll">
      <div ref={content} className="st-content" style={{width:HEAD + lane}} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
        onPointerLeave={()=>setHover(null)} role="slider" aria-label="Thời gian video" aria-valuemin={0} aria-valuemax={Math.round(total / 1000)}
        aria-valuenow={Math.round(p.now / 1000)} aria-valuetext={hms(p.now)}>
        {/* ruler */}
        <div className="st-ruler">
          <div className="st-head st-corner"/>
          {ticks.map(t=><span key={t} className="st-tick" style={{left:x(t)}}><small>{hms(t)}</small></span>)}
          {minor > 0 && ticks.flatMap(t=>[1, 2, 3, 4].map(k=>t + k * minor)).filter(t=>t < total)
            .map(t=><span key={'m' + t} className="st-tick is-minor" style={{left:x(t)}}/>)}
          {p.markers.map(({marker:m, index})=><span key={'r' + index} className={'st-ruler-pin' + (index === p.selectedMarker ? ' is-selected' : '')}
            style={{left:x(m.at_ms)}}>{p.markers.findIndex(i=>i.index === index) + 1}</span>)}
          <span className="st-bubble" style={{left:x(p.now)}}>{hms(p.now)}</span>
        </div>

        {/* pin markers */}
        <div className="st-group">
          <button type="button" className="st-head st-group-head" onClick={()=>setOpen(o=>({...o, pins:!o.pins}))} aria-expanded={open.pins}>
            {open.pins ? <ChevronDown size={14}/> : <ChevronRight size={14}/>}<Pin size={14}/>Mốc ghim sản phẩm <small>({p.markers.length})</small>
          </button>
        </div>
        {open.pins && <div className="st-row st-pins" style={{height:pinsHeight}}>
          <div className="st-head st-row-head"><small className="st-muted">{p.markers.length ? 'Kéo thẻ để dời mốc' : 'Chưa có mốc'}</small></div>
          {p.markers.map(({marker:m, index}, n)=>{
            const product = m.product_id != null ? p.product(m.product_id) : undefined
            const Icon = ACTION_ICON[m.action]
            return <div key={`${index}-${m.at_ms}`} className={'st-pin is-' + m.action.toLowerCase() + (index === p.selectedMarker ? ' is-selected' : '') + (m.review_status !== 'APPROVED' ? ' is-review' : '')}
              style={{left:x(m.at_ms), top:6 + (markerLane.get(index) ?? 0) * 58, width:MARKER_CARD}} onPointerDown={e=>onMarkerDown(e, index)}
              title={`${hms(m.at_ms)} · ${ACTION_LABEL[m.action]}${product ? ' · ' + product.name : ''}`}>
              <Thumb src={product?.image_url ? assetUrl(product.image_url) : null} size={40} icon={<Icon size={18}/>}/>
              <span className="st-pin-text"><b><i>{n + 1}</i>{hms(m.at_ms)}</b>
                <span>{m.action === 'PIN_PRODUCT' ? product?.name ?? (m.product_id != null ? `Sản phẩm #${m.product_id}` : 'Chưa chọn sản phẩm') : ACTION_LABEL[m.action]}</span></span>
            </div>
          })}
        </div>}

        {/* overlays */}
        <div className="st-group">
          <button type="button" className="st-head st-group-head" onClick={()=>setOpen(o=>({...o, overlays:!o.overlays}))} aria-expanded={open.overlays}>
            {open.overlays ? <ChevronDown size={14}/> : <ChevronRight size={14}/>}<Layers size={14}/>Overlay <small>({p.overlays.length})</small>
          </button>
        </div>
        {open.overlays && <div className="st-overlays">
          {p.overlays.map(item=>{
            const o = item.overlay, on = o.enabled !== false
            const error = p.issues.some(i=>i.overlay === item.index && i.level === 'error')
            const warn = !error && p.issues.some(i=>i.overlay === item.index)
            const productName = o.product_id != null ? p.product(o.product_id)?.name : undefined
            const name = overlayName(o, p.presets)
            const shows = p.plan ? p.plan.filter(a=>a.index === item.index) : null
            return <div key={item.index} className={'st-row st-ov' + (item.index === p.selectedOverlay ? ' is-selected' : '')}>
              <div className="st-head st-row-head">
                <button type="button" className="st-eye" title={on ? 'Đang bật: bấm để tạm ẩn' : 'Tạm ẩn: bấm để bật'} aria-label={on ? 'Tạm ẩn overlay' : 'Bật overlay'}
                  disabled={!p.editable} onClick={()=>p.onToggleOverlay(item.index)}>{on ? <Eye size={15}/> : <EyeOff size={15}/>}</button>
                <button type="button" className="st-row-name" onClick={()=>{p.onSelectOverlay(item.index);p.onSeek(o.start_ms)}} title={name}>{name}</button>
                <span className={'st-chip' + (on ? ' is-on' : '')}>{on ? 'Đang bật' : 'Tạm ẩn'}</span>
              </div>
              <button type="button" className={'st-clip' + (shows ? ' is-window' : '') + (on ? '' : ' is-off') + (error ? ' is-error' : warn ? ' is-warn' : '')}
                style={{left:x(o.start_ms), width:Math.max(8, x(endOf(o)) - x(o.start_ms))}} onPointerDown={e=>onClipDown(e, item)}
                title={shows ? `${name} · được hiện trong ${hms(o.start_ms)} → ${hms(endOf(o))}, mỗi lần ${secondsText(showMsOf(o))} · lượt ${(p.iteration ?? 0) + 1}: ${shows.length} lần`
                  : `${name} · ${hms(o.start_ms)} → ${hms(endOf(o))}`}>
                <Thumb src={overlayThumb(o, p.product, p.assetUrlOf)} size={shows ? 22 : 26} icon={<Layers size={13}/>}/>
                <span className="st-clip-name">{shows && <i className="st-dice" aria-hidden>🎲</i>}{productName ?? name}</span>
                <span className="st-clip-time">{shows ? `${shows.length} lần · ` : ''}{hms(o.start_ms)} – {hms(endOf(o))}</span>
              </button>
              {shows?.map(a=><span key={a.start_ms} className={'st-appear' + (p.now >= a.start_ms && p.now < a.end_ms ? ' is-now' : '')}
                style={{left:x(a.start_ms), width:Math.max(3, x(a.end_ms) - x(a.start_ms))}} title={`Hiện ${hms(a.start_ms)} – ${hms(a.end_ms)}`}/>)}
            </div>
          })}
          {p.editable && <div className="st-row st-add-row"><div className="st-head st-row-head"/>
            <button type="button" className="st-add" style={{width:Math.max(120, width - HEAD - 22)}} onClick={p.onAddOverlay}><Plus size={15}/>Thêm overlay</button></div>}
          {!p.overlays.length && !p.editable && <div className="st-row"><div className="st-head st-row-head"><small className="st-muted">Chưa có overlay</small></div></div>}
        </div>}

        <span className="st-playhead" style={{left:x(p.now)}}/>
        {hover != null && <span className="st-hover" style={{left:x(hover)}}/>}
      </div>
    </div>
  </div>
}
