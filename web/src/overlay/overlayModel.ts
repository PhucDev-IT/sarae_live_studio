/**
 * LIVE overlays (docs/LIVE_OVERLAYS.md): the data an overlay is made of and the pure rules every renderer shares —
 * which overlays are shown at a position of a video, their animation state, how a product field reads, which image URLs
 * may be loaded. No React, no DOM: the web editor and the OBS browser source (Phase 3) use exactly these.
 *
 * There is no fixed list of overlay types: an overlay is a box on the 1080×1920 canvas made of building blocks
 * (text, product_field, image, shape). `template` is only a free label naming the arrangement.
 */

export const CANVAS = {width:1080, height:1920} as const
export const MIN_DURATION_MS = 500
export const MAX_DURATION_MS = 3_600_000
export const MAX_ELEMENTS = 20
export const ANIMATIONS = ['none', 'fade', 'slide_up', 'slide_down', 'slide_left', 'slide_right', 'zoom'] as const
export const PRODUCT_FIELDS = ['name', 'price', 'origin_price', 'sku'] as const
export type AnimationName = typeof ANIMATIONS[number]
export type ProductField = typeof PRODUCT_FIELDS[number]

/** A rectangle as fractions (0–1) of its container (the canvas for an overlay, the overlay box for a block). */
export type Frame = {x:number; y:number; width:number; height:number}
export type TextStyle = {
  font_size:number         // px of the 1080×1920 canvas
  font_weight:number; font_family:string; color:string; background:string|null
  align:'left'|'center'|'right'; max_lines:number  // 0 = no limit
}
type BlockBase = {frame:Frame; opacity:number}
export type TextElement = BlockBase & {kind:'text'; text:string; style:TextStyle}
export type ProductFieldElement = BlockBase & {kind:'product_field'; field:ProductField; product_id:number|null; prefix:string; suffix:string; style:TextStyle}
export type ImageElement = BlockBase & {kind:'image'; source:'asset'|'product'; asset_id:string|null; product_id:number|null; fit:'contain'|'cover'; radius:number}
export type ShapeElement = BlockBase & {kind:'shape'; shape:'rect'|'ellipse'; fill:string|null; stroke:string|null; stroke_width:number; radius:number}
export type OverlayElement = TextElement | ProductFieldElement | ImageElement | ShapeElement
export type BlockKind = OverlayElement['kind']
export type Animation = {name:AnimationName; duration_ms:number}
export type OverlayLayout = Frame & {z_index:number; opacity:number}
export type Overlay = {
  id?:number; media_id:string; template:string; label:string; start_ms:number; duration_ms:number; product_id:number|null
  layout:OverlayLayout; animation_in:Animation; animation_out:Animation; elements:OverlayElement[]
  /** Missing in a snapshot (it only holds enabled overlays). */
  enabled?:boolean
}
export type SceneProduct = {name:string; sku?:string; price?:number|null; origin_price?:number|null; image_url?:string}
export type SceneAsset = {url:string; mime_type?:string; sha256?:string; width?:number; height?:number; size_bytes?:number}
/** Snapshot-shaped input of a renderer: overlays plus the products and images they read, keyed by id. */
export type OverlayScene = {
  canvas?:{width:number; height:number}; overlays:Overlay[]
  products:Record<string, SceneProduct>; assets:Record<string, SceneAsset>
}
/** A preset of GET /api/live/overlay-templates: a starting point, never a type. */
export type OverlayPreset = {template:string; label:string; layout:Partial<OverlayLayout>; elements:Partial<OverlayElement>[]}
export type OverlayIssue = {level:'error'|'warning'; code:string; message:string; overlay?:number; media_id?:string; at_ms?:number}

// ----- defaults (same as the Backend's schemas, so a preset or snapshot block always has every field) -----

const clamp = (v:number, lo:number, hi:number) => Math.max(lo, Math.min(hi, v))
const num = (v:unknown, fallback:number) => typeof v === 'number' && Number.isFinite(v) ? v : fallback
export const FULL_FRAME:Frame = {x:0, y:0, width:1, height:1}
export const defaultStyle = ():TextStyle => ({font_size:48, font_weight:600, font_family:'', color:'#FFFFFF', background:null, align:'left', max_lines:0})

export function normalizeFrame(raw?:Partial<Frame>|null):Frame{
  const f = {...FULL_FRAME, ...(raw ?? {})}
  const width = clamp(num(f.width, 1), 0.001, 1), height = clamp(num(f.height, 1), 0.001, 1)
  return {x:clamp(num(f.x, 0), 0, 1 - width), y:clamp(num(f.y, 0), 0, 1 - height), width, height}
}

export function normalizeElement(raw:Partial<OverlayElement>):OverlayElement{
  const base = {frame:normalizeFrame(raw.frame), opacity:num(raw.opacity, 1)}
  const r = raw as Record<string, unknown>
  const style = {...defaultStyle(), ...((r.style as Partial<TextStyle>|undefined) ?? {})}
  switch (raw.kind){
    case 'text': return {...base, kind:'text', text:String(r.text ?? 'Chữ'), style}
    case 'product_field': return {...base, kind:'product_field', field:(r.field as ProductField) ?? 'name', product_id:(r.product_id as number|null) ?? null,
      prefix:String(r.prefix ?? ''), suffix:String(r.suffix ?? ''), style}
    case 'image': return {...base, kind:'image', source:r.source === 'product' ? 'product' : 'asset', asset_id:(r.asset_id as string|null) ?? null,
      product_id:(r.product_id as number|null) ?? null, fit:r.fit === 'cover' ? 'cover' : 'contain', radius:num(r.radius, 0)}
    default: return {...base, kind:'shape', shape:r.shape === 'ellipse' ? 'ellipse' : 'rect', fill:r.fill === undefined ? '#000000A0' : (r.fill as string|null),
      stroke:(r.stroke as string|null) ?? null, stroke_width:num(r.stroke_width, 0), radius:num(r.radius, 0)}
  }
}

export function normalizeOverlay(raw:Partial<Overlay> & {media_id:string}):Overlay{
  const layout = {...normalizeFrame(raw.layout), z_index:num(raw.layout?.z_index, 0), opacity:num(raw.layout?.opacity, 1)}
  return {
    ...(raw.id != null ? {id:raw.id} : {}), media_id:raw.media_id, template:raw.template ?? '', label:raw.label ?? '',
    start_ms:Math.round(num(raw.start_ms, 0)), duration_ms:Math.round(num(raw.duration_ms, 5000)), product_id:raw.product_id ?? null, layout,
    animation_in:{name:raw.animation_in?.name ?? 'fade', duration_ms:num(raw.animation_in?.duration_ms, 300)},
    animation_out:{name:raw.animation_out?.name ?? 'fade', duration_ms:num(raw.animation_out?.duration_ms, 300)},
    elements:(raw.elements ?? []).map(e=>normalizeElement(e)), enabled:raw.enabled !== false,
  }
}

/** A new overlay from a preset (or a blank one): its own copy, editable freely, at `start_ms` of `media_id`. */
export function fromPreset(preset:OverlayPreset|null, media_id:string, start_ms:number, product_id:number|null):Overlay{
  const blank:OverlayPreset = {template:'', label:'', layout:{x:0.1, y:0.25, width:0.8, height:0.1},
    elements:[{kind:'shape', fill:'#000000A0', radius:0.2}, {kind:'text', text:'Nội dung', frame:{x:0.05, y:0.1, width:0.9, height:0.8}, style:{...defaultStyle(), align:'center'}}]}
  const p = preset ?? blank
  return normalizeOverlay({media_id, template:p.template, label:p.label, start_ms, duration_ms:5000, product_id, layout:{z_index:1, opacity:1, ...p.layout} as OverlayLayout,
    elements:structuredClone(p.elements) as OverlayElement[], enabled:true})
}

/**
 * Presets the web adds as data when the Backend list lacks them (still free labels, never types): a "Product Spotlight"
 * card is a product photo + name + price on a panel.
 */
export const EXTRA_PRESETS:OverlayPreset[] = [
  {template:'PRODUCT_SPOTLIGHT', label:'Product Spotlight', layout:{x:0.05, y:0.16, width:0.6, height:0.2},
    elements:[
      {kind:'shape', fill:'#FFFFFFF0', radius:0.12},
      {kind:'image', source:'product', fit:'cover', radius:0.1, frame:{x:0.04, y:0.08, width:0.34, height:0.84}},
      {kind:'product_field', field:'name', frame:{x:0.42, y:0.1, width:0.55, height:0.5}, style:{...defaultStyle(), font_size:38, color:'#111111', max_lines:2}},
      {kind:'product_field', field:'price', suffix:'đ', frame:{x:0.42, y:0.62, width:0.55, height:0.3}, style:{...defaultStyle(), font_size:48, font_weight:800, color:'#E5004F'}},
    ] as Partial<OverlayElement>[]},
]

// ----- what is shown, when -----

export const endOf = (o:Pick<Overlay, 'start_ms'|'duration_ms'>) => o.start_ms + o.duration_ms
/** Shown at `ms` of `mediaId`: enabled, started, not ended (an overlay ending where another starts is already gone). */
export const isShown = (o:Overlay, mediaId:string|null, ms:number) => o.enabled !== false && o.media_id === mediaId && ms >= o.start_ms && ms < endOf(o)

/** Drawing order: z_index, then the order of the list. */
export function shownAt(overlays:Overlay[], mediaId:string|null, ms:number){
  return overlays.map((overlay, index)=>({overlay, index})).filter(x=>isShown(x.overlay, mediaId, ms))
    .sort((a, b)=>a.overlay.layout.z_index - b.overlay.layout.z_index || a.index - b.index)
}

const SLIDE_PX = 80
const easeOut = (p:number) => 1 - Math.pow(1 - clamp(p, 0, 1), 3)
/**
 * Opacity factor and CSS transform of an overlay at `ms`, from the clock only (no CSS keyframes): the same position
 * always looks the same, after a seek too. Entering: the "in" animation over its first `duration_ms`; leaving: the
 * "out" animation over its last. Slides move by 80 canvas px and fade with it; zoom scales from 85 %.
 */
export function animationState(o:Overlay, ms:number):{opacity:number; transform:string}{
  const t = ms - o.start_ms
  const inMs = o.animation_in.name === 'none' ? 0 : o.animation_in.duration_ms
  const outMs = o.animation_out.name === 'none' ? 0 : o.animation_out.duration_ms
  let name:AnimationName = 'none', p = 1, leaving = false
  if (inMs > 0 && t < inMs) {name = o.animation_in.name;p = t / inMs}
  else if (outMs > 0 && t > o.duration_ms - outMs) {name = o.animation_out.name;p = (o.duration_ms - t) / outMs;leaving = true}
  const e = easeOut(p), away = (1 - e) * SLIDE_PX * (leaving ? -1 : 1)
  switch (name){
    case 'fade': return {opacity:e, transform:'none'}
    case 'slide_up': return {opacity:e, transform:`translateY(${away}px)`}
    case 'slide_down': return {opacity:e, transform:`translateY(${-away}px)`}
    case 'slide_left': return {opacity:e, transform:`translateX(${away}px)`}
    case 'slide_right': return {opacity:e, transform:`translateX(${-away}px)`}
    case 'zoom': return {opacity:e, transform:`scale(${0.85 + 0.15 * e})`}
    default: return {opacity:1, transform:'none'}
  }
}

// ----- what a block reads -----

const priceFormat = new Intl.NumberFormat('vi-VN', {maximumFractionDigits:0})
/** Text of a product field: prices with thousands separators (vi-VN), empty when the product has no value. */
export function fieldText(e:ProductFieldElement, product?:SceneProduct){
  const value = product?.[e.field]
  if (value == null || value === '') return ''
  return e.prefix + (typeof value === 'number' ? priceFormat.format(value) : String(value)) + e.suffix
}
export const productOf = (o:Overlay, e:{product_id:number|null}) => e.product_id ?? o.product_id

/**
 * The only image URLs a renderer loads: our own signed URLs (`/api/…`, resolved against the API base) or https photos
 * from the catalog sync — never anything else (an overlay holds asset/product ids, never a URL).
 */
export function safeImageUrl(url:string|undefined|null, apiBase:string){
  if (!url) return null
  if (url.startsWith('/api/')) return apiBase.replace(/\/$/, '') + url
  return url.startsWith('https://') ? url : null
}

// ----- geometry helpers for editors -----

/** Box clamped inside its container, with a minimum size. */
export function clampFrame(f:Frame, min = 0.02):Frame{
  const width = clamp(f.width, min, 1), height = clamp(f.height, min, 1)
  return {x:clamp(f.x, 0, 1 - width), y:clamp(f.y, 0, 1 - height), width, height}
}
export const round4 = (v:number) => Math.round(v * 10000) / 10000

/**
 * Zones of the TikTok LIVE viewer app covering the stream on a phone (fractions of the 9:16 frame). An approximation
 * from typical layouts: it changes with the app version, phone, language and what the host turns on.
 */
export const TIKTOK_ZONES = [
  {key:'header', label:'Thanh trên (avatar, người xem, nút đóng)', x:0, y:0, width:1, height:0.11},
  {key:'product', label:'Thẻ sản phẩm đang ghim', x:0.02, y:0.47, width:0.64, height:0.12},
  {key:'comments', label:'Bình luận', x:0, y:0.6, width:0.74, height:0.3},
  {key:'actions', label:'Nút quà / thích / chia sẻ', x:0.84, y:0.52, width:0.16, height:0.38},
  {key:'toolbar', label:'Thanh công cụ dưới', x:0, y:0.9, width:1, height:0.1},
] as const
/** The part of the frame that stays visible in the TikTok viewer most of the time (also approximate). */
export const SAFE_AREA:Frame = {x:0.04, y:0.12, width:0.78, height:0.34}
const COVERED_SHARE = 0.2

/** TikTok zones covering at least 20 % of the overlay box. */
export function coveredBy(layout:Frame){
  const area = layout.width * layout.height
  return TIKTOK_ZONES.filter(z=>{
    const w = Math.min(layout.x + layout.width, z.x + z.width) - Math.max(layout.x, z.x)
    const h = Math.min(layout.y + layout.height, z.y + z.height) - Math.max(layout.y, z.y)
    return w > 0 && h > 0 && w * h / area >= COVERED_SHARE
  })
}

/** Most overlays of one video shown at once (an overlay ending where another starts does not overlap it). */
export function busiest(list:Overlay[]){
  const edges = list.flatMap(o=>[[o.start_ms, 1], [endOf(o), -1]] as const).sort((a, b)=>a[0] - b[0] || a[1] - b[1])
  let peak = 0, at = 0, shown = 0
  for (const [position, step] of edges) {shown += step;if (shown > peak) {peak = shown;at = position}}
  return {peak, at}
}
