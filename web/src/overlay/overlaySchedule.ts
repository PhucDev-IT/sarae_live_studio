/**
 * Overlay schedules (docs/LIVE_OVERLAYS.md §13–14). A timeline version runs its overlays in one of three modes:
 *
 * - `fixed` (Chính xác): an overlay shows from `start_ms` for `duration_ms`, exactly;
 * - `window` (Theo khoảng): `start_ms`..`start_ms + duration_ms` is the window it MAY show in, `show_ms` at a time;
 * - `random` (Ngẫu nhiên): the overlays of a video are a list, no times: an overlay with a product may show while that
 *   product is pinned (the version's approved pin markers, `pinSpans`), one without a product anywhere in the video.
 *
 * `window` and `random` draw a plan per loop of a video with the same rules, only the windows differ:
 *
 * - the video is cut into slots of `every_ms` from 0; each slot holds at most one appearance;
 * - an appearance lies fully inside its slot and inside one window of its overlay, and starts at least `min_gap_ms`
 *   after the previous appearance ended: one overlay on screen at a time, never two back to back;
 * - candidates of a slot: the overlays that fit; the least shown so far win, then those on their last chance (no window
 *   lets them start in a later slot), and the one just shown is skipped when there is another; the pick and the start
 *   (100 ms steps, earliest fitting window) come from a seeded generator;
 * - a slot where nothing fits stays empty.
 *
 * The seed is `timeline_id:media_id:iteration` (iteration = the engine's loop count, 0 for the first play), so the same
 * loop gives the same plan in the web editor, the OBS renderer and the Agent (agent-core overlay/schedule.py and the
 * Backend's pin spans are ports of this file; all are checked against agent-core/tests/fixtures/overlay_schedule_vectors.json).
 * Integer arithmetic only, so every language agrees.
 */
import type { Overlay, OverlayScene } from './overlayModel'

export type ScheduleMode = 'fixed' | 'window' | 'random'
export type OverlaySchedule = {mode:ScheduleMode; every_ms:number; min_gap_ms:number}
export const SCHEDULE_MODES:ScheduleMode[] = ['fixed', 'window', 'random']
export const DEFAULT_SCHEDULE:OverlaySchedule = {mode:'fixed', every_ms:120_000, min_gap_ms:20_000}
export const DEFAULT_SHOW_MS = 6000
export const MIN_SHOW_MS = 1000, MAX_SHOW_MS = 600_000
export const MIN_EVERY_MS = 10_000, MAX_EVERY_MS = 3_600_000
export const MAX_GAP_MS = 600_000
const STEP_MS = 100

/** One appearance of overlay `index` (its position in the list given to `randomPlan`). */
export type Appearance = {index:number; start_ms:number; end_ms:number}
/** A stretch of one video where `product_id` is pinned. */
export type PinSpan = {start_ms:number; end_ms:number; product_id:number}
/** What a `random` schedule needs about a video besides its overlays. */
export type VideoContext = {pins?:PinSpan[]; duration_ms?:number|null}
type Window = [number, number]
type PinMarker = {at_ms:number; action:string; product_id:number|null; review_status?:string; seq?:number; id?:number}

/** FNV-1a 32-bit of a string (UTF-16 code units; seeds are ASCII). */
export function seedOf(key:string){
  let h = 0x811c9dc5
  for (let i = 0; i < key.length; i++) {h ^= key.charCodeAt(i);h = Math.imul(h, 0x01000193) >>> 0}
  return h >>> 0
}

/** mulberry32: a uint32 per call. */
export function generator(seed:number){
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1) >>> 0
    t = (t ^ (t + Math.imul(t ^ (t >>> 7), t | 61))) >>> 0
    return (t ^ (t >>> 14)) >>> 0
  }
}

export const showMsOf = (o:Pick<Overlay, 'show_ms'>) => o.show_ms ?? DEFAULT_SHOW_MS
export const isPlanned = (mode:ScheduleMode|undefined) => mode === 'window' || mode === 'random'

/**
 * Where each product is pinned in one video, from its markers (Backend: live_timeline.pin_at): approved PIN/UNPIN
 * markers by position, then seq, then id; a pin lasts until the next PIN of another product, an UNPIN or the end of the
 * video. Before the first marker nothing counts as pinned. Adjacent spans of the same product are merged.
 */
export function pinSpans(markers:PinMarker[], durationMs:number):PinSpan[]{
  const ordered = markers.filter(m=>(m.review_status ?? 'APPROVED') === 'APPROVED' && (m.action === 'PIN_PRODUCT' || m.action === 'UNPIN_PRODUCT'))
    .slice().sort((a, b)=>a.at_ms - b.at_ms || (a.seq ?? 0) - (b.seq ?? 0) || (a.id ?? 0) - (b.id ?? 0))
  const spans:PinSpan[] = []
  let product:number|null = null, from = 0
  const close = (at:number) => {
    const end = Math.min(at, durationMs)
    if (product == null || end <= from) return
    const prev = spans[spans.length - 1]
    if (prev && prev.product_id === product && prev.end_ms === from) prev.end_ms = end
    else spans.push({start_ms:from, end_ms:end, product_id:product})
  }
  for (const m of ordered) {
    if (m.at_ms >= durationMs) break
    const next = m.action === 'PIN_PRODUCT' ? m.product_id : null
    if (next === product) continue
    close(m.at_ms)
    product = next;from = m.at_ms
  }
  close(durationMs)
  return spans
}

/** The windows an overlay may show in under `mode` (sorted, non-overlapping). */
export function windowsOf(o:Overlay, mode:ScheduleMode, video:VideoContext):Window[]{
  if (mode !== 'random') return [[o.start_ms, o.start_ms + o.duration_ms]]
  const pins = video.pins ?? []
  const end = video.duration_ms ?? Math.max(0, ...pins.map(p=>p.end_ms))
  if (o.product_id == null) return end > 0 ? [[0, end]] : []
  return pins.filter(p=>p.product_id === o.product_id).map(p=>[p.start_ms, p.end_ms] as Window)
}

/**
 * The appearances of one loop of `mediaId` (`window` or `random` mode): enabled overlays of that video, in list order.
 * Pure: same input, same plan.
 */
export function randomPlan(overlays:Overlay[], mediaId:string|null, schedule:OverlaySchedule, timelineId:number, iteration:number, video:VideoContext = {}):Appearance[]{
  const own = overlays.map((o, index)=>({o, index, show:showMsOf(o), windows:windowsOf(o, schedule.mode, video)}))
    .filter(x=>x.o.enabled !== false && x.o.media_id === mediaId && x.windows.length)
  if (!own.length || schedule.every_ms <= 0) return []
  const next = generator(seedOf(`${timelineId}:${mediaId}:${iteration}`))
  const end = Math.max(...own.flatMap(x=>x.windows.map(w=>w[1])))
  const shown = new Map<number, number>()
  const plan:Appearance[] = []
  let last = -1, free = -Infinity
  for (let slot = 0; slot < end; slot += schedule.every_ms) {
    const after = slot + schedule.every_ms
    const fits = own.flatMap(({index, show, windows})=>{
      for (const [w0, w1] of windows) {
        const lo = Math.max(slot, w0, free), hi = Math.min(after, w1) - show
        // Last chance: no window lets this overlay start in a later slot.
        if (lo <= hi) return [{index, show, lo, hi, urgent:windows.every(w=>w[1] - show < after)}]
      }
      return []
    })
    if (!fits.length) continue
    const fewest = Math.min(...fits.map(f=>shown.get(f.index) ?? 0))
    let pool = fits.filter(f=>(shown.get(f.index) ?? 0) === fewest)
    const urgent = pool.filter(f=>f.urgent)
    if (urgent.length) pool = urgent
    if (pool.length > 1) pool = pool.filter(f=>f.index !== last)
    const pick = pool[next() % pool.length]
    const start = pick.lo + (next() % (Math.floor((pick.hi - pick.lo) / STEP_MS) + 1)) * STEP_MS
    plan.push({index:pick.index, start_ms:start, end_ms:start + pick.show})
    shown.set(pick.index, (shown.get(pick.index) ?? 0) + 1)
    last = pick.index
    free = start + pick.show + schedule.min_gap_ms
  }
  return plan
}

export const videoOf = (scene:OverlayScene, mediaId:string|null):VideoContext =>
  mediaId == null ? {} : {pins:scene.pins?.[mediaId], duration_ms:scene.durations?.[mediaId] ?? null}

const plans = new WeakMap<OverlayScene, Map<string, Appearance[]>>()
/** `randomPlan` of a scene, cached per scene object (renderers ask every frame: keep the scene object stable). */
export function planOf(scene:OverlayScene, mediaId:string|null, iteration:number):Appearance[]{
  const schedule = {...DEFAULT_SCHEDULE, ...scene.schedule}
  if (!isPlanned(schedule.mode)) return []
  const key = `${mediaId}:${iteration}`
  let byKey = plans.get(scene)
  if (!byKey) {byKey = new Map();plans.set(scene, byKey)}
  let plan = byKey.get(key)
  if (!plan) {
    plan = randomPlan(scene.overlays, mediaId, schedule, scene.timeline_id ?? 0, iteration, videoOf(scene, mediaId))
    if (byKey.size > 64) byKey.clear()
    byKey.set(key, plan)
  }
  return plan
}

/**
 * The scene as drawn at `ms` of loop `iteration` of `mediaId`. Fixed mode: unchanged. Planned modes: same list (indexes
 * kept, editors select by index); each overlay of that video becomes its appearance covering `ms` (start_ms/duration_ms
 * of the appearance, so the shared in/out animations play on it), or disabled when it is not on screen.
 */
export function sceneAt(scene:OverlayScene, mediaId:string|null, ms:number, iteration:number):OverlayScene{
  if (!isPlanned(scene.schedule?.mode)) return scene
  const now = planOf(scene, mediaId, iteration).find(a=>ms >= a.start_ms && ms < a.end_ms)
  return {...scene, overlays:scene.overlays.map((o, index)=>{
    if (o.media_id !== mediaId) return o
    if (now?.index !== index) return {...o, enabled:false}
    return {...o, start_ms:now.start_ms, duration_ms:now.end_ms - now.start_ms}
  })}
}
