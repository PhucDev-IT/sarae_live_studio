import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import { Layers, ZoomIn } from 'lucide-react'
import OverlayRenderer from './OverlayRenderer'
import {
  CANVAS, MIN_DURATION_MS, SAFE_AREA, TIKTOK_ZONES, clampFrame, coveredBy, endOf, isShown, round4,
  type Frame, type Overlay, type OverlayIssue, type OverlayLayout, type OverlayPreset, type OverlayScene,
} from './overlayModel'
import { newStep, overlayName, trackName } from './overlayDraft'
import { hms } from '../liveSessionsApi'

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

const ZOOMS = [1, 2, 4, 8, 16, 32]
const SNAP_MS = 100
type TrackRow = {name:string; template:string; lanes:{o:Overlay; index:number}[][]}

/**
 * Overlay tracks of the video shown: one track per `template` label (any label, custom ones included; no fixed list),
 * overlapping overlays of a track on extra lanes. Drag a bar to move it, its edges to change start / end (0.1 s steps);
 * click the ruler to seek.
 */
export function OverlayTracks({overlays, mediaId, duration, now, selected, editable, presets, issues, onSelect, onSeek, onTime, onGestureEnd}:{
  overlays:Overlay[]; mediaId:string; duration:number|null; now:number; selected:number|null; editable:boolean; presets:OverlayPreset[]; issues:OverlayIssue[]
  onSelect:(index:number)=>void; onSeek:(ms:number)=>void; onTime:(index:number, start:number, length:number, gesture:string)=>void; onGestureEnd:()=>void
}){
  const [zoom,setZoom] = useState(1)
  const scroller = useRef<HTMLDivElement>(null)
  const area = useRef<HTMLDivElement>(null)
  const drag = useRef<{index:number; mode:'move'|'start'|'end'; x:number; start:number; length:number; key:string}|null>(null)
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

  const msOf = (clientX:number) => {const box = area.current?.getBoundingClientRect();return box ? (clientX - box.left) / box.width * total : 0}
  const onBarDown = (e:ReactPointerEvent<HTMLButtonElement>, index:number) => {
    e.stopPropagation()
    if (e.button !== 0) return
    onSelect(index)
    if (!editable) return
    const rect = e.currentTarget.getBoundingClientRect(), edge = Math.min(10, rect.width / 3)
    const mode = e.clientX - rect.left < edge ? 'start' : rect.right - e.clientX < edge ? 'end' : 'move'
    try {e.currentTarget.setPointerCapture(e.pointerId)} catch {/* pointer gone */}
    drag.current = {index, mode, x:e.clientX, start:overlays[index].start_ms, length:overlays[index].duration_ms, key:newStep('time')}
  }
  const onBarMove = (e:ReactPointerEvent) => {
    const d = drag.current, box = area.current?.getBoundingClientRect()
    if (!d || !box) return
    const delta = Math.round((e.clientX - d.x) / box.width * total / SNAP_MS) * SNAP_MS
    const limit = duration ?? Infinity
    let start = d.start, length = d.length
    if (d.mode === 'move') start = Math.max(0, Math.min(limit - length, d.start + delta))
    if (d.mode === 'start') {start = Math.max(0, Math.min(d.start + d.length - MIN_DURATION_MS, d.start + delta));length = d.start + d.length - start}
    if (d.mode === 'end') length = Math.max(MIN_DURATION_MS, Math.min(limit - d.start, d.length + delta))
    onTime(d.index, Math.round(start), Math.round(length), d.key)
  }
  const onBarUp = () => {if (drag.current) {drag.current = null;onGestureEnd()}}

  return <div className="ove-tracks">
    <div className="te-bar-tools"><Layers size={15}/><span className="te-muted">Overlay của video này</span><span className="ove-spacer"/>
      <ZoomIn size={15}/>{ZOOMS.map(z=><button key={z} type="button" className={'te-zoom' + (z === zoom ? ' is-on' : '')} onClick={()=>setZoom(z)}>{z}×</button>)}
    </div>
    <div className="ove-tracks-body">
      <div className="ove-track-names">
        <span className="ove-ruler-name"/>
        {rows.map(r=><span key={r.template} className="ove-track-name" style={{height:r.lanes.length * 30 + 8}} title={r.template || 'Không nhãn'}>{r.name}</span>)}
      </div>
      <div ref={scroller} className="ove-tracks-scroll">
        <div ref={area} className="ove-tracks-area" style={{width:`${zoom * 100}%`}} onPointerMove={onBarMove} onPointerUp={onBarUp} onPointerCancel={onBarUp}>
          <div className="ove-ruler" onPointerDown={e=>{if (e.button === 0) onSeek(Math.max(0, Math.min(total, msOf(e.clientX))))}}>
            {crowded.map(i=><span key={i.at_ms} className="ove-crowded" style={{left:at(i.at_ms ?? 0)}} title={i.message}>!</span>)}
          </div>
          {rows.map(r=><div key={r.template} className="ove-track" style={{height:r.lanes.length * 30 + 8}}>
            {r.lanes.map((lane, n)=>lane.map(({o, index})=><button key={index} type="button"
              className={'ove-bar' + (index === selected ? ' is-selected' : '') + (o.enabled === false ? ' is-off' : '') + (issues.some(i=>i.overlay === index && i.level === 'error') ? ' is-error' : '')}
              style={{left:at(o.start_ms), width:`max(6px, calc(${at(endOf(o))} - ${at(o.start_ms)}))`, top:4 + n * 30}}
              title={`${overlayName(o, presets)} · ${hms(o.start_ms)} → ${hms(endOf(o))}`} onPointerDown={e=>onBarDown(e, index)}>
              <span>{overlayName(o, presets)}</span>
            </button>))}
          </div>)}
          {!rows.length && <p className="ove-tracks-empty">Chưa có overlay cho video này.</p>}
          <span className="te-playhead" style={{left:at(now)}}/>
        </div>
      </div>
    </div>
  </div>
}
