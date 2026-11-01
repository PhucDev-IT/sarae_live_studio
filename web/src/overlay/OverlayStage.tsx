import { useRef, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import OverlayRenderer from './OverlayRenderer'
import {
  CANVAS, SAFE_AREA, TIKTOK_ZONES, clampFrame, coveredBy, endOf, isShown, round4,
  type Frame, type OverlayLayout, type OverlayScene,
} from './overlayModel'
import { newStep } from './overlayDraft'
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

