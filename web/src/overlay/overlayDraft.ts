import { useCallback, useRef, useState } from 'react'
import {
  MAX_DURATION_MS, MIN_DURATION_MS, busiest, coveredBy, endOf, normalizeOverlay, productOf,
  type Overlay, type OverlayIssue, type OverlayPreset,
} from './overlayModel'

const HISTORY = 100
let gestures = 0
/** A key for one undo step that later edits never merge into (a click, a drag, a list change). */
export const newStep = (prefix:string) => `${prefix}#${++gestures}`

/**
 * The overlays of the draft being edited (every video of the session) with undo / redo. `set(next, key)` records one
 * step; consecutive calls with the same `key` (one drag, typing in one field) are merged into that step.
 */
export function useOverlayDraft(){
  const [state,setState] = useState<{past:Overlay[][]; present:Overlay[]; future:Overlay[][]; dirty:boolean}>({past:[], present:[], future:[], dirty:false})
  const lastKey = useRef<string|null>(null)
  const set = useCallback((next:Overlay[]|((current:Overlay[])=>Overlay[]), key?:string)=>{
    setState(s=>{
      const value = typeof next === 'function' ? next(s.present) : next
      if (value === s.present) return s
      const merge = key != null && key === lastKey.current
      lastKey.current = key ?? null
      return {past:merge ? s.past : [...s.past, s.present].slice(-HISTORY), present:value, future:[], dirty:true}
    })
  },[])
  const undo = useCallback(()=>{lastKey.current = null;setState(s=>s.past.length ? {past:s.past.slice(0, -1), present:s.past[s.past.length - 1], future:[s.present, ...s.future], dirty:true} : s)},[])
  const redo = useCallback(()=>{lastKey.current = null;setState(s=>s.future.length ? {past:[...s.past, s.present], present:s.future[0], future:s.future.slice(1), dirty:true} : s)},[])
  /** A fresh start (loaded or saved): no history, not dirty. */
  const reset = useCallback((list:Overlay[])=>{lastKey.current = null;setState({past:[], present:list, future:[], dirty:false})},[])
  /** Ends the current merge group (pointer released, field left). */
  const seal = useCallback(()=>{lastKey.current = null},[])
  return {overlays:state.present, dirty:state.dirty, canUndo:state.past.length > 0, canRedo:state.future.length > 0, set, undo, redo, reset, seal}
}

// ----- copy / paste (kept for the tab, so an overlay can be pasted into another video or version) -----

const CLIPBOARD_KEY = 'sarae.overlay-clipboard'
let clipboard:Overlay|null = null
export function copyOverlay(o:Overlay){
  const {id:_id, ...rest} = o
  clipboard = structuredClone(rest)
  try {sessionStorage.setItem(CLIPBOARD_KEY, JSON.stringify(clipboard))} catch {/* storage blocked: memory only */}
}
export function pastedOverlay(mediaId:string, startMs:number, videoMs:number|null):Overlay|null{
  let source = clipboard
  if (!source) {try {source = JSON.parse(sessionStorage.getItem(CLIPBOARD_KEY) ?? 'null') as Overlay|null} catch {source = null}}
  if (!source) return null
  const duration = videoMs != null ? Math.max(MIN_DURATION_MS, Math.min(source.duration_ms, videoMs - startMs)) : source.duration_ms
  const start = videoMs != null ? Math.max(0, Math.min(startMs, videoMs - duration)) : startMs
  return normalizeOverlay({...structuredClone(source), media_id:mediaId, start_ms:start, duration_ms:duration})
}
export const hasClipboard = () => {
  if (clipboard) return true
  try {return !!sessionStorage.getItem(CLIPBOARD_KEY)} catch {return false}
}

// ----- names -----

/** Name of a track (the overlay's free `template` label): the preset's own name when it is one, the label otherwise. */
export function trackName(template:string, presets:OverlayPreset[]){
  if (!template) return 'Không nhãn'
  return presets.find(p=>p.template === template)?.label ?? template
}
/** Short name of one overlay: its label, else its first text, else its track. */
export function overlayName(o:Overlay, presets:OverlayPreset[]){
  if (o.label) return o.label
  const text = o.elements.find(e=>e.kind === 'text')
  return text && text.kind === 'text' ? text.text : trackName(o.template, presets)
}

// ----- checks the editor can do before saving (the Backend repeats them and adds its own) -----

export function localIssues(overlays:Overlay[], ctx:{
  durationOf:(mediaId:string)=>number|null; hasProduct:(id:number)=>boolean; hasAsset:(id:string)=>boolean|null; maxConcurrent:number
}):OverlayIssue[]{
  const found:OverlayIssue[] = []
  overlays.forEach((o, index)=>{
    const add = (level:'error'|'warning', code:string, message:string) => found.push({level, code, overlay:index, message})
    if (!o.elements.length) add('error', 'NO_BLOCKS', 'Overlay chưa có khối nào')
    if (o.duration_ms < MIN_DURATION_MS || o.duration_ms > MAX_DURATION_MS) add('error', 'DURATION', 'Thời lượng phải từ 0,5 giây tới 1 giờ')
    const video = ctx.durationOf(o.media_id)
    if (video != null && endOf(o) > video) add('error', 'AFTER_END', 'Overlay kéo dài quá cuối video')
    if (o.animation_in.duration_ms + o.animation_out.duration_ms > o.duration_ms) add('error', 'ANIMATION_TOO_LONG', 'Hiệu ứng vào + ra dài hơn thời lượng overlay')
    o.elements.forEach((e, n)=>{
      const block = `Khối #${n + 1}`
      if (e.kind === 'text' && !e.text.trim()) add('error', 'TEXT_EMPTY', `${block}: chữ đang trống`)
      if (e.kind === 'image' && e.source === 'asset' && !e.asset_id) add('error', 'NO_ASSET', `${block}: chưa chọn ảnh`)
      if (e.kind === 'image' && e.source === 'asset' && e.asset_id && ctx.hasAsset(e.asset_id) === false) add('error', 'ASSET_MISSING', `${block}: ảnh đã bị xóa khỏi kho`)
      if ((e.kind === 'product_field' || (e.kind === 'image' && e.source === 'product'))) {
        const pid = productOf(o, e)
        if (pid == null) add('error', 'NO_PRODUCT', `${block}: chưa chọn sản phẩm (của khối hoặc của overlay)`)
        else if (!ctx.hasProduct(pid)) add('error', 'PRODUCT_MISSING', `${block}: sản phẩm #${pid} không có trong catalog`)
      }
    })
    const zones = o.enabled !== false ? coveredBy(o.layout) : []
    if (zones.length) add('warning', 'MAYBE_COVERED', `Có thể bị che trên TikTok (ước lượng): ${zones.map(z=>z.label).join(', ')}`)
  })
  for (const mediaId of new Set(overlays.map(o=>o.media_id))) {
    const {peak, at} = busiest(overlays.filter(o=>o.media_id === mediaId && o.enabled !== false))
    if (peak > ctx.maxConcurrent) found.push({level:'error', code:'TOO_MANY_AT_ONCE', media_id:mediaId, at_ms:at,
      message:`${peak} overlay hiện cùng lúc ở ${Math.floor(at / 1000)}s (tối đa ${ctx.maxConcurrent}); lưu nháp được nhưng chưa duyệt được`})
  }
  return found
}
/** Errors that make the Backend refuse the save (422); too many at once only blocks approval. */
export const blocksSave = (i:OverlayIssue) => i.level === 'error' && i.code !== 'TOO_MANY_AT_ONCE'
