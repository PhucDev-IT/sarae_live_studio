import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import { Layers } from 'lucide-react'
import OverlayRenderer from './OverlayRenderer'
import {
  CANVAS, MIN_DURATION_MS, SAFE_AREA, TIKTOK_ZONES, clampFrame, coveredBy, endOf, isShown, round4,
  type Frame, type Overlay, type OverlayIssue, type OverlayLayout, type OverlayPreset, type OverlayScene,
} from './overlayModel'
import { newStep, overlayName, trackName } from './overlayDraft'
import { hms } from '../liveSessionsApi'
import { ticksOf } from '../timelineView'

export type PreviewMode = 'obs' | 'tiktok'
type Handle = 'move' | 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw'
const HANDLES:Exclude<Handle, 'move'>[] = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w']
const pct = (v:number) => `${v * 100}%`

/**
 * The 9:16 output frame of the editor: the video (untouched), the shared OverlayRenderer on top of it, then the editor's
 * own layer: the selected overlay's box with handles (drag to move, handles to resize; positions are stored as fractions
 * of the 1080×1920 canvas), the safe zones and, in TikTok mode, the simulated viewer UI.
 */
export function OverlayPreview({scene, mediaId, now, animate, apiBase, mode, showZones, selected, editable, onSelect, onLayout, onGestureEnd, children}:{
  scene:OverlayScene; mediaId:string; now:number; animate:boolean; apiBase:string; mode:PreviewMode; showZones:boolean
  selected:number|null; editable:boolean; onSelect:(index:number|null)=>void
  onLayout:(index:number, layout:OverlayLayout, gesture:string)=>void; onGestureEnd:()=>void
  /** The video element (drawn under everything). */
  children:ReactNode
}){
  const frame = useRef<HTMLDivElement>(null)
  const drag = useRef<{index:number; handle:Handle; x:number; y:number; start:OverlayLayout; key:string}|null>(null)
  const visible = scene.overlays.map((o, index)=>({o, index})).filter(x=>isShown(x.o, mediaId, now))
  const current = selected != null ? scene.overlays[selected] : undefined
  const selectedHere = current && current.media_id === mediaId ? current : undefined

  const onDown = (e:ReactPointerEvent, index:number, handle:Handle) => {
    e.stopPropagation()
    if (e.button !== 0) return
    onSelect(index)
    if (!editable) return
    try {(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)} catch {/* pointer gone */}
    drag.current = {index, handle, x:e.clientX, y:e.clientY, start:scene.overlays[index].layout, key:newStep('drag')}
  }
  const onMove = (e:ReactPointerEvent) => {
    const d = drag.current, box = frame.current?.getBoundingClientRect()
    if (!d || !box) return
    const dx = (e.clientX - d.x) / box.width, dy = (e.clientY - d.y) / box.height
    const s = d.start
    let next:Frame
    if (d.handle === 'move') next = {...s, x:Math.max(0, Math.min(1 - s.width, s.x + dx)), y:Math.max(0, Math.min(1 - s.height, s.y + dy))}
    else {
      let {x, y} = s, right = s.x + s.width, bottom = s.y + s.height
      if (d.handle.includes('w')) x = Math.max(0, Math.min(right - 0.02, s.x + dx))
      if (d.handle.includes('e')) right = Math.min(1, Math.max(x + 0.02, right + dx))
      if (d.handle.includes('n')) y = Math.max(0, Math.min(bottom - 0.02, s.y + dy))
      if (d.handle.includes('s')) bottom = Math.min(1, Math.max(y + 0.02, bottom + dy))
      next = {x, y, width:right - x, height:bottom - y}
    }
    const c = clampFrame(next)
    onLayout(d.index, {...s, x:round4(c.x), y:round4(c.y), width:round4(c.width), height:round4(c.height)}, d.key)
  }
  const onUp = () => {if (drag.current) {drag.current = null;onGestureEnd()}}

  return <div ref={frame} className={'ove-frame is-' + mode} onPointerDown={()=>onSelect(null)} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}>
    {children}
    <OverlayRenderer scene={scene} mediaId={mediaId} positionMs={now} animate={animate} apiBase={apiBase} placeholders/>
    {mode === 'tiktok' && <TikTokChrome/>}
    {showZones && <div className="ove-zones" aria-hidden>
      <span className="ove-safe" style={{left:pct(SAFE_AREA.x), top:pct(SAFE_AREA.y), width:pct(SAFE_AREA.width), height:pct(SAFE_AREA.height)}}><small>Vùng an toàn</small></span>
      {TIKTOK_ZONES.map(z=><span key={z.key} className="ove-zone" style={{left:pct(z.x), top:pct(z.y), width:pct(z.width), height:pct(z.height)}}><small>{z.label}</small></span>)}
    </div>}
    {/* hit areas of the overlays shown now; the selected one gets handles (also outside its time, dashed) */}
    {visible.filter(x=>x.index !== selected).map(({o, index})=><button key={index} type="button" className="ove-hit" aria-label={`Chọn overlay ${index + 1}`}
      style={{left:pct(o.layout.x), top:pct(o.layout.y), width:pct(o.layout.width), height:pct(o.layout.height)}}
      onPointerDown={e=>onDown(e, index, 'move')}/>)}
    {selectedHere && selected != null && <div className={'ove-sel' + (isShown(selectedHere, mediaId, now) ? '' : ' is-hidden') + (coveredBy(selectedHere.layout).length ? ' is-covered' : '') + (editable ? ' is-editable' : '')}
      style={{left:pct(selectedHere.layout.x), top:pct(selectedHere.layout.y), width:pct(selectedHere.layout.width), height:pct(selectedHere.layout.height)}}
      onPointerDown={e=>onDown(e, selected, 'move')}>
      <span className="ove-sel-size">{Math.round(selectedHere.layout.width * CANVAS.width)}×{Math.round(selectedHere.layout.height * CANVAS.height)}</span>
      {!isShown(selectedHere, mediaId, now) && <span className="ove-sel-note">{selectedHere.enabled === false ? 'Đang tắt' : `Hiện ${hms(selectedHere.start_ms)}–${hms(endOf(selectedHere))}`}</span>}
      {editable && HANDLES.map(h=><span key={h} className={'ove-handle is-' + h} onPointerDown={e=>onDown(e, selected, h)}/>)}
    </div>}
  </div>
}

/** A rough picture of the TikTok LIVE viewer UI over the stream (approximate, see TIKTOK_ZONES). */
function TikTokChrome(){
  return <div className="ove-tiktok" aria-hidden>
    <div className="ove-tt-top"><span className="ove-tt-avatar"/><span className="ove-tt-host"><b>Shop của bạn</b><small>1.2K lượt thích</small></span><span className="ove-tt-pill">LIVE</span><span className="ove-tt-viewers">356 ×</span></div>
    <div className="ove-tt-product"><span className="ove-tt-thumb"/><span><b>Sản phẩm đang ghim</b><small>199.000đ · Mua</small></span></div>
    <div className="ove-tt-comments">
      {['an***: còn size M không shop', 'minh***: giá bao nhiêu ạ', 'thu***: đã đặt 2 cái', 'khoa***: ship tỉnh mấy ngày'].map(c=><p key={c}>{c}</p>)}
    </div>
    <div className="ove-tt-actions">{['🎁', '❤', '↗'].map(i=><span key={i}>{i}</span>)}</div>
    <div className="ove-tt-bar"><span>Thêm bình luận…</span><i/><i/><i/></div>
  </div>
}

const SNAP_MS = 100
/** Height of the strip above each track's bars that holds its name (never under a bar). */
const LABEL_PX = 16
type TrackRow = {name:string; template:string; lanes:{o:Overlay; index:number}[][]}

/**
 * Overlay tracks of the video shown: one track per `template` label (any label, custom ones included; no fixed list),
 * overlapping overlays of a track on extra lanes, the track's name as a label inside its row. Same width, zoom, ticks
 * and scroll as the pin timeline above, so a time is at the same x in both. Press and drag anywhere outside a bar to
 * seek (the pointer is kept when it leaves the tracks); drag a bar to move it, its edges to change start / end (0.1 s).
 */
export function OverlayTracks({overlays, mediaId, duration, now, selected, editable, presets, issues, zoom, scrollRef, onSelect, onSeek, onTime, onGestureEnd}:{
  overlays:Overlay[]; mediaId:string; duration:number|null; now:number; selected:number|null; editable:boolean; presets:OverlayPreset[]; issues:OverlayIssue[]
  /** Shared with the pin timeline. */
  zoom:number; scrollRef:(el:HTMLDivElement|null)=>void
  onSelect:(index:number)=>void; onSeek:(ms:number)=>void; onTime:(index:number, start:number, length:number, gesture:string)=>void; onGestureEnd:()=>void
}){
  const scroller = useRef<HTMLDivElement>(null)
  const area = useRef<HTMLDivElement>(null)
  const drag = useRef<{index:number; mode:'move'|'start'|'end'; x:number; start:number; length:number; key:string}|null>(null)
  const scrubbing = useRef(false)
  const [hover,setHover] = useState<number|null>(null)
  const total = duration ?? Math.max(60_000, ...overlays.filter(o=>o.media_id === mediaId).map(endOf))
  const at = (ms:number) => `${Math.max(0, Math.min(100, ms / total * 100))}%`
  useEffect(()=>{
    const box = scroller.current
    if (!box || zoom === 1) return
    const x = now / total * box.scrollWidth
    if (x < box.scrollLeft + 40 || x > box.scrollLeft + box.clientWidth - 40) box.scrollLeft = x - box.clientWidth / 2
  },[now, zoom, total])

  const rows:TrackRow[] = []
  overlays.forEach((o, index)=>{
    if (o.media_id !== mediaId) return
    let row = rows.find(r=>r.template === o.template)
    if (!row) {row = {template:o.template, name:trackName(o.template, presets), lanes:[]};rows.push(row)}
    const lane = row.lanes.find(l=>l.every(x=>endOf(x.o) <= o.start_ms || endOf(o) <= x.o.start_ms))
    if (lane) lane.push({o, index});else row.lanes.push([{o, index}])
  })
  const crowded = issues.filter(i=>i.code === 'TOO_MANY_AT_ONCE' && i.media_id === mediaId)

  const msOf = (clientX:number) => {
    const box = area.current?.getBoundingClientRect()
    return box ? Math.max(0, Math.min(total, (clientX - box.left) / box.width * total)) : 0
  }
  const capture = (pointerId:number) => {try {area.current?.setPointerCapture(pointerId)} catch {/* pointer gone */}}
  const onAreaDown = (e:ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    capture(e.pointerId)
    scrubbing.current = true
    onSeek(msOf(e.clientX))
  }
  const onBarDown = (e:ReactPointerEvent<HTMLButtonElement>, index:number) => {
    e.stopPropagation()
    if (e.button !== 0) return
    onSelect(index)
    if (!editable) return
    const rect = e.currentTarget.getBoundingClientRect(), edge = Math.min(10, rect.width / 3)
    const mode = e.clientX - rect.left < edge ? 'start' : rect.right - e.clientX < edge ? 'end' : 'move'
    capture(e.pointerId)
    drag.current = {index, mode, x:e.clientX, start:overlays[index].start_ms, length:overlays[index].duration_ms, key:newStep('time')}
  }
  const onAreaMove = (e:ReactPointerEvent) => {
    setHover(msOf(e.clientX))
    const d = drag.current, box = area.current?.getBoundingClientRect()
    if (d && box) {
      const delta = Math.round((e.clientX - d.x) / box.width * total / SNAP_MS) * SNAP_MS
      const limit = duration ?? Infinity
      let start = d.start, length = d.length
      if (d.mode === 'move') start = Math.max(0, Math.min(limit - length, d.start + delta))
      if (d.mode === 'start') {start = Math.max(0, Math.min(d.start + d.length - MIN_DURATION_MS, d.start + delta));length = d.start + d.length - start}
      if (d.mode === 'end') length = Math.max(MIN_DURATION_MS, Math.min(limit - d.start, d.length + delta))
      onTime(d.index, Math.round(start), Math.round(length), d.key)
      return
    }
    if (scrubbing.current) onSeek(msOf(e.clientX))
  }
  const onAreaUp = () => {
    scrubbing.current = false
    if (drag.current) {drag.current = null;onGestureEnd()}
  }

  return <div className="ove-tracks">
    <div className="te-bar-tools"><Layers size={15}/><span className="te-muted">Overlay của video này</span>
      <small className="te-muted">· bấm hoặc kéo trên vùng trống để tua</small></div>
    <div ref={el=>{scroller.current = el;scrollRef(el)}} className="ove-tracks-scroll">
      <div ref={area} className="ove-tracks-area" style={{width:`${zoom * 100}%`}} onPointerDown={onAreaDown} onPointerMove={onAreaMove}
        onPointerUp={onAreaUp} onPointerCancel={onAreaUp} onPointerLeave={()=>setHover(null)}
        role="slider" aria-label="Thời gian overlay" aria-valuemin={0} aria-valuemax={Math.round(total / 1000)} aria-valuenow={Math.round(now / 1000)} aria-valuetext={hms(now)}>
        {ticksOf(total, zoom).map(t=><span key={t} className="te-tick ove-tick" style={{left:at(t)}}><small>{hms(t)}</small></span>)}
        <div className="ove-ruler">
          {crowded.map(i=><span key={i.at_ms} className="ove-crowded" style={{left:at(i.at_ms ?? 0)}} title={i.message}>!</span>)}
        </div>
        {rows.map(r=><div key={r.template} className="ove-track" style={{height:LABEL_PX + r.lanes.length * 30 + 6}}>
          <span className="ove-track-label" title={r.template || 'Không nhãn'}>{r.name}</span>
          {r.lanes.map((lane, n)=>lane.map(({o, index})=><button key={index} type="button"
            className={'ove-bar' + (index === selected ? ' is-selected' : '') + (o.enabled === false ? ' is-off' : '') + (issues.some(i=>i.overlay === index && i.level === 'error') ? ' is-error' : '')}
            style={{left:at(o.start_ms), width:`max(6px, calc(${at(endOf(o))} - ${at(o.start_ms)}))`, top:LABEL_PX + n * 30}}
            title={`${overlayName(o, presets)} · ${hms(o.start_ms)} → ${hms(endOf(o))}`} onPointerDown={e=>onBarDown(e, index)}>
            <span>{overlayName(o, presets)}</span>
          </button>))}
        </div>)}
        {!rows.length && <p className="ove-tracks-empty">Chưa có overlay cho video này.</p>}
        <span className="te-playhead" style={{left:at(now)}}/>
        {hover != null && <span className="te-hover" style={{left:at(hover)}}>{hms(hover)}</span>}
      </div>
    </div>
  </div>
}
