import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import {
  CANVAS, animationState, fieldText, productOf, safeImageUrl, shownAt,
  type ImageElement, type Overlay, type OverlayElement, type OverlayScene, type TextStyle,
} from './overlayModel'
import './overlay-fonts'
import './overlay-renderer.css'

/**
 * The one LIVE overlay renderer, shared by the web editor preview and the OBS browser source (Phase 3): draws the
 * overlays of a snapshot-shaped scene that are shown at `positionMs` of video `mediaId`.
 *
 * It lays everything out on a fixed 1080×1920 logical canvas (the overlay geometry is in fractions of it, text sizes in
 * its pixels) and scales that canvas to its container with a CSS transform, so the picture is the same at any size and
 * never depends on the page viewport, fonts of the page or its CSS. The container should be 9:16 (it is letterboxed
 * otherwise). It never touches the video: it is a transparent layer drawn on top of it.
 *
 * Images: only asset URLs and product photos from the scene, filtered by `safeImageUrl` (signed `/api/…` or https).
 */
export default function OverlayRenderer({scene, mediaId, positionMs, apiBase, animate = true, placeholders = false, className, children}:{
  scene:OverlayScene; mediaId:string|null; positionMs:number
  /** Base the signed `/api/…` URLs resolve against (the Backend origin). */
  apiBase:string
  /** false: overlays are drawn fully shown (an editor paused on a frame); true: in/out animations from the clock. */
  animate?:boolean
  /** Show a dashed box where an image is not chosen or cannot load (editor); OBS leaves it empty. */
  placeholders?:boolean
  className?:string
  /** Drawn above the canvas, in the container's coordinates (editor handles, guides). */
  children?:ReactNode
}){
  const root = useRef<HTMLDivElement>(null)
  const [box,setBox] = useState({scale:0, left:0, top:0})
  useLayoutEffect(()=>{
    const el = root.current
    if (!el) return
    const fit = () => {
      const {width, height} = el.getBoundingClientRect()
      const scale = Math.min(width / CANVAS.width, height / CANVAS.height)
      setBox({scale, left:(width - CANVAS.width * scale) / 2, top:(height - CANVAS.height * scale) / 2})
    }
    fit()
    const observer = new ResizeObserver(fit)
    observer.observe(el)
    return ()=>observer.disconnect()
  },[])
  const shown = shownAt(scene.overlays, mediaId, positionMs)
  return <div ref={root} className={'ovr-root' + (className ? ' ' + className : '')}>
    <div className="ovr-canvas" style={{width:CANVAS.width, height:CANVAS.height, transform:`translate(${box.left}px, ${box.top}px) scale(${box.scale})`, visibility:box.scale ? 'visible' : 'hidden'}}>
      {shown.map(({overlay, index})=><OverlayBox key={overlay.id ?? 'i' + index} overlay={overlay} scene={scene} positionMs={positionMs} animate={animate} apiBase={apiBase} placeholders={placeholders}/>)}
    </div>
    {children}
  </div>
}

function OverlayBox({overlay:o, scene, positionMs, animate, apiBase, placeholders}:{overlay:Overlay; scene:OverlayScene; positionMs:number; animate:boolean; apiBase:string; placeholders:boolean}){
  const anim = animate ? animationState(o, positionMs) : {opacity:1, transform:'none'}
  const w = o.layout.width * CANVAS.width, h = o.layout.height * CANVAS.height
  const style:CSSProperties = {left:o.layout.x * CANVAS.width, top:o.layout.y * CANVAS.height, width:w, height:h,
    opacity:o.layout.opacity * anim.opacity, transform:anim.transform}
  return <div className="ovr-box" style={style} data-template={o.template || undefined}>
    {o.elements.map((e, i)=><Block key={i} element={e} overlay={o} scene={scene} boxWidth={w} boxHeight={h} apiBase={apiBase} placeholders={placeholders}/>)}
  </div>
}

function Block({element:e, overlay, scene, boxWidth, boxHeight, apiBase, placeholders}:{
  element:OverlayElement; overlay:Overlay; scene:OverlayScene; boxWidth:number; boxHeight:number; apiBase:string; placeholders:boolean
}){
  const w = e.frame.width * boxWidth, h = e.frame.height * boxHeight
  const frame:CSSProperties = {left:e.frame.x * boxWidth, top:e.frame.y * boxHeight, width:w, height:h, opacity:e.opacity}
  const corner = (radius:number) => radius * Math.min(w, h)
  if (e.kind === 'shape') return <div className="ovr-block ovr-shape" style={{...frame, background:e.fill ?? 'transparent',
    borderRadius:e.shape === 'ellipse' ? '50%' : corner(e.radius), boxShadow:e.stroke && e.stroke_width ? `inset 0 0 0 ${e.stroke_width}px ${e.stroke}` : undefined}}/>
  if (e.kind === 'image') return <ImageBlock element={e} url={imageUrl(e, overlay, scene, apiBase)} style={{...frame, borderRadius:corner(e.radius)}} placeholder={placeholders}/>
  const text = e.kind === 'text' ? e.text : fieldText(e, scene.products[String(productOf(overlay, e))])
  if (!text) return placeholders ? <div className="ovr-block ovr-missing" style={frame}>{e.kind === 'product_field' ? `[${e.field}]` : ''}</div> : null
  return <div className="ovr-block ovr-text" style={{...frame, ...textStyle(e.style)}}>
    <span style={e.style.max_lines ? {WebkitLineClamp:e.style.max_lines, display:'-webkit-box', WebkitBoxOrient:'vertical', overflow:'hidden'} : undefined}>{text}</span>
  </div>
}

function imageUrl(e:ImageElement, overlay:Overlay, scene:OverlayScene, apiBase:string){
  if (e.source === 'asset') return safeImageUrl(e.asset_id ? scene.assets[e.asset_id]?.url : null, apiBase)
  return safeImageUrl(scene.products[String(productOf(overlay, e))]?.image_url, apiBase)
}

function ImageBlock({element:e, url, style, placeholder}:{element:ImageElement; url:string|null; style:CSSProperties; placeholder:boolean}){
  const [failed,setFailed] = useState<string|null>(null)
  if (!url || failed === url) return placeholder ? <div className="ovr-block ovr-missing" style={style}>{e.source === 'asset' ? 'Chưa chọn ảnh' : 'Không có ảnh sản phẩm'}</div> : null
  return <img className="ovr-block ovr-image" style={{...style, objectFit:e.fit}} src={url} alt="" draggable={false} crossOrigin="anonymous" referrerPolicy="no-referrer" onError={()=>setFailed(url)}/>
}

/** Text style in canvas pixels; the font stack and line height are fixed so the output is the same everywhere. */
function textStyle(s:TextStyle):CSSProperties{
  return {fontSize:s.font_size, fontWeight:s.font_weight, color:s.color, background:s.background ?? undefined, textAlign:s.align,
    justifyContent:s.align === 'center' ? 'center' : s.align === 'right' ? 'flex-end' : 'flex-start',
    ...(s.shadow ? {textShadow:'0 3px 10px rgba(0,0,0,.55), 0 1px 2px rgba(0,0,0,.6)'} : {}),
    ...(s.font_family ? {fontFamily:`"${s.font_family}", var(--ovr-font)`} : {})}
}
