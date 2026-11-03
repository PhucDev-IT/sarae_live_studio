import { useCallback, useRef, useState } from 'react'
import {
  MAX_DURATION_MS, MIN_DURATION_MS, busiest, coveredBy, endOf, normalizeOverlay, productOf,
  type Overlay, type OverlayIssue, type OverlayPreset,
} from './overlayModel'
import { DEFAULT_SCHEDULE, MAX_GAP_MS, MAX_EVERY_MS, MAX_SHOW_MS, MIN_EVERY_MS, MIN_SHOW_MS, showMsOf, type OverlaySchedule } from './overlaySchedule'

const HISTORY = 100
let gestures = 0
/** A key for one undo step that later edits never merge into (a click, a drag, a list change). */
export const newStep = (prefix:string) => `${prefix}#${++gestures}`

/** What one undo step holds: the overlays and the version's schedule (switching to random is undone like any edit). */
type Draft = {overlays:Overlay[]; schedule:OverlaySchedule}

/**
 * The overlays of the draft being edited (every video of the session) and its schedule, with undo / redo. `set(next,
 * key)` / `setSchedule(patch, key)` record one step; consecutive calls with the same `key` (one drag, typing in one
 * field) are merged into that step.
 */
export function useOverlayDraft(){
  const [state,setState] = useState<{past:Draft[]; present:Draft; future:Draft[]; dirty:boolean}>({past:[], present:{overlays:[], schedule:DEFAULT_SCHEDULE}, future:[], dirty:false})
  const lastKey = useRef<string|null>(null)
  const record = useCallback((next:(current:Draft)=>Draft, key?:string)=>{
    setState(s=>{
      const value = next(s.present)
      if (value === s.present) return s
      const merge = key != null && key === lastKey.current
      lastKey.current = key ?? null
      return {past:merge ? s.past : [...s.past, s.present].slice(-HISTORY), present:value, future:[], dirty:true}
    })
  },[])
  const set = useCallback((next:Overlay[]|((current:Overlay[])=>Overlay[]), key?:string)=>record(d=>{
    const overlays = typeof next === 'function' ? next(d.overlays) : next
    return overlays === d.overlays ? d : {...d, overlays}
  }, key),[record])
  const setSchedule = useCallback((patch:Partial<OverlaySchedule>, key?:string)=>record(d=>{
    const schedule = {...d.schedule, ...patch}
    return (Object.keys(schedule) as (keyof OverlaySchedule)[]).every(k=>schedule[k] === d.schedule[k]) ? d : {...d, schedule}
  }, key),[record])
  const undo = useCallback(()=>{lastKey.current = null;setState(s=>s.past.length ? {past:s.past.slice(0, -1), present:s.past[s.past.length - 1], future:[s.present, ...s.future], dirty:true} : s)},[])
  const redo = useCallback(()=>{lastKey.current = null;setState(s=>s.future.length ? {past:[...s.past, s.present], present:s.future[0], future:s.future.slice(1), dirty:true} : s)},[])
  /** A fresh start (loaded or saved): no history, not dirty. */
  const reset = useCallback((list:Overlay[], schedule:OverlaySchedule = DEFAULT_SCHEDULE)=>{lastKey.current = null;setState({past:[], present:{overlays:list, schedule}, future:[], dirty:false})},[])
  /** Ends the current merge group (pointer released, field left). */
  const seal = useCallback(()=>{lastKey.current = null},[])
  return {overlays:state.present.overlays, schedule:state.present.schedule, dirty:state.dirty, canUndo:state.past.length > 0, canRedo:state.future.length > 0,
    set, setSchedule, undo, redo, reset, seal}
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
  /** The version's schedule (absent = fixed). */
  schedule?:OverlaySchedule
}):OverlayIssue[]{
  const found:OverlayIssue[] = []
  const random = ctx.schedule?.mode === 'random' ? ctx.schedule : null
  overlays.forEach((o, index)=>{
    const add = (level:'error'|'warning', code:string, message:string) => found.push({level, code, overlay:index, message})
    if (!o.elements.length) add('error', 'NO_BLOCKS', 'Overlay chưa có khối nào')
    if (o.duration_ms < MIN_DURATION_MS || o.duration_ms > MAX_DURATION_MS) add('error', 'DURATION', random ? 'Khoảng cho phép phải từ 0,5 giây tới 1 giờ' : 'Thời lượng phải từ 0,5 giây tới 1 giờ')
    const video = ctx.durationOf(o.media_id)
    if (video != null && endOf(o) > video) add('error', 'AFTER_END', random ? 'Khoảng cho phép kéo dài quá cuối video' : 'Overlay kéo dài quá cuối video')
    const animations = o.animation_in.duration_ms + o.animation_out.duration_ms
    const show = showMsOf(o)
    if (random) {
      // Random schedule: the bar is the window, each appearance lasts show_ms (overlaySchedule.ts).
      if (show < MIN_SHOW_MS || show > MAX_SHOW_MS) add('error', 'SHOW_MS', 'Thời lượng mỗi lần hiện phải từ 1 giây tới 10 phút')
      if (show > o.duration_ms) add('error', 'SHOW_LONGER_THAN_WINDOW', `Mỗi lần hiện (${secondsText(show)}) dài hơn khoảng cho phép (${secondsText(o.duration_ms)}): không bao giờ hiện`)
      if (show > random.every_ms) add('error', 'SHOW_LONGER_THAN_SLOT', `Mỗi lần hiện (${secondsText(show)}) dài hơn “1 lần mỗi ${secondsText(random.every_ms)}”: không bao giờ hiện`)
    }
    if (animations > o.duration_ms) add('error', 'ANIMATION_TOO_LONG', random ? 'Hiệu ứng vào + ra dài hơn khoảng cho phép' : 'Hiệu ứng vào + ra dài hơn thời lượng overlay')
    else if (random && animations > show) add('error', 'ANIMATION_LONGER_THAN_SHOW', 'Hiệu ứng vào + ra dài hơn thời lượng mỗi lần hiện')
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
  const enabled = overlays.filter(o=>o.enabled !== false)
  if (random && enabled.length) {
    const longest = Math.max(...enabled.map(showMsOf))
    if (random.every_ms < longest + random.min_gap_ms) found.push({level:'warning', code:'DENSITY_TOO_HIGH',
      message:`“1 lần mỗi ${secondsText(random.every_ms)}” ngắn hơn lần hiện dài nhất (${secondsText(longest)}) + nghỉ tối thiểu (${secondsText(random.min_gap_ms)}): một số khoảng sẽ trống`})
  }
  for (const mediaId of new Set(overlays.map(o=>o.media_id))) {
    const own = enabled.filter(o=>o.media_id === mediaId)
    if (random) {
      // One at a time: no "too many at once"; instead, can the slots be filled at all?
      if (!own.length) continue
      const end = Math.max(...own.map(endOf)), empty:number[] = []
      for (let slot = 0; slot < end; slot += random.every_ms) {
        const fits = own.some(o=>Math.max(slot, o.start_ms) + showMsOf(o) <= Math.min(slot + random.every_ms, endOf(o)))
        if (!fits) empty.push(slot)
      }
      if (empty.length) found.push({level:'warning', code:'NO_OVERLAY_IN_SLOTS', media_id:mediaId, at_ms:empty[0],
        message:`${empty.length} khoảng “1 lần mỗi ${secondsText(random.every_ms)}” không có overlay nào hiện được (khoảng cho phép không phủ tới), từ ${Math.floor(empty[0] / 1000)}s`})
      continue
    }
    const {peak, at} = busiest(own)
    if (peak > ctx.maxConcurrent) found.push({level:'error', code:'TOO_MANY_AT_ONCE', media_id:mediaId, at_ms:at,
      message:`${peak} overlay hiện cùng lúc ở ${Math.floor(at / 1000)}s (tối đa ${ctx.maxConcurrent}); lưu nháp được nhưng chưa duyệt được`})
  }
  return found
}
/** Errors that only block approval (saving a draft is allowed): too many at once, and the random-schedule checks. */
const APPROVAL_ONLY = new Set(['TOO_MANY_AT_ONCE', 'SHOW_LONGER_THAN_WINDOW', 'SHOW_LONGER_THAN_SLOT', 'ANIMATION_LONGER_THAN_SHOW'])
/** Errors that make the Backend refuse the save (422). */
export const blocksSave = (i:OverlayIssue) => i.level === 'error' && !APPROVAL_ONLY.has(i.code)

/** "6 giây", "6,5 giây", "2 phút", "1 phút 30 giây". */
export function secondsText(ms:number){
  const s = Math.round(ms / 100) / 10
  if (s < 60) return `${String(s).replace('.', ',')} giây`
  const m = Math.floor(s / 60), rest = Math.round(s - m * 60)
  return rest ? `${m} phút ${rest} giây` : `${m} phút`
}
/** "2:00" / "1:30" / "90" / "90s" → ms (seconds when there is no colon); null when it is not a duration. */
export function parseSpan(text:string){
  const t = text.trim().replace(',', '.').replace(/\s*(s|giây)$/i, '')
  const parts = t.split(':')
  if (!t || parts.length > 2 || parts.some(x=>!/^\d+(\.\d+)?$/.test(x))) return null
  const seconds = parts.length === 2 ? Number(parts[0]) * 60 + Number(parts[1]) : Number(parts[0])
  return Math.round(seconds * 1000)
}
export const spanText = (ms:number) => {const s = Math.round(ms / 1000);return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`}
/** Schedule values the Backend accepts. */
export const clampSchedule = (s:OverlaySchedule):OverlaySchedule => ({mode:s.mode,
  every_ms:Math.max(MIN_EVERY_MS, Math.min(MAX_EVERY_MS, Math.round(s.every_ms))), min_gap_ms:Math.max(0, Math.min(MAX_GAP_MS, Math.round(s.min_gap_ms)))})
