/**
 * Random overlay mode (docs/LIVE_OVERLAYS.md §13): when a timeline version's schedule is `random`, an overlay's
 * `start_ms`..`start_ms + duration_ms` is the window it MAY show in, and it shows `show_ms` at a time. Each loop of a
 * video draws its own plan:
 *
 * - the video is cut into slots of `every_ms` from 0; each slot holds at most one appearance;
 * - an appearance lies fully inside its slot and inside its overlay's window, and starts at least `min_gap_ms` after
 *   the previous appearance ended: one overlay on screen at a time, never two back to back;
 * - candidates of a slot: the overlays that fit; the least shown so far win, then those whose window ends before the next
 *   slot (last chance), and the one just shown is skipped when there is another; the pick and the start (100 ms steps) come from a seeded generator;
 * - a slot where nothing fits stays empty.
 *
 * The seed is `timeline_id:media_id:iteration` (iteration = the engine's loop count, 0 for the first play), so the same
 * loop gives the same plan in the web editor, the OBS renderer and the Agent (agent-core overlay/schedule.py is a port
 * of this file; both are checked against the same vectors). Integer arithmetic only, so both languages agree.
 */
import type { Overlay, OverlayScene } from './overlayModel'

export type ScheduleMode = 'fixed' | 'random'
export type OverlaySchedule = {mode:ScheduleMode; every_ms:number; min_gap_ms:number}
export const DEFAULT_SCHEDULE:OverlaySchedule = {mode:'fixed', every_ms:120_000, min_gap_ms:20_000}
export const DEFAULT_SHOW_MS = 6000
export const MIN_SHOW_MS = 1000, MAX_SHOW_MS = 600_000
export const MIN_EVERY_MS = 10_000, MAX_EVERY_MS = 3_600_000
export const MAX_GAP_MS = 600_000
const STEP_MS = 100

/** One appearance of overlay `index` (its position in the list given to `randomPlan`). */
export type Appearance = {index:number; start_ms:number; end_ms:number}

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

/**
 * The appearances of one loop of `mediaId`: enabled overlays of that video, in list order. Pure: same input, same plan.
 */
export function randomPlan(overlays:Overlay[], mediaId:string|null, schedule:OverlaySchedule, timelineId:number, iteration:number):Appearance[]{
  const own = overlays.map((o, index)=>({o, index})).filter(x=>x.o.enabled !== false && x.o.media_id === mediaId)
  if (!own.length || schedule.every_ms <= 0) return []
  const next = generator(seedOf(`${timelineId}:${mediaId}:${iteration}`))
  const end = Math.max(...own.map(x=>x.o.start_ms + x.o.duration_ms))
  const shown = new Map<number, number>()
  const plan:Appearance[] = []
  let last = -1, free = -Infinity
  for (let slot = 0; slot < end; slot += schedule.every_ms) {
    const fits = own.flatMap(({o, index})=>{
      const show = showMsOf(o)
      const lo = Math.max(slot, o.start_ms, free)
      const hi = Math.min(slot + schedule.every_ms, o.start_ms + o.duration_ms) - show
      return lo <= hi ? [{index, show, lo, hi}] : []
    })
    if (!fits.length) continue
    const fewest = Math.min(...fits.map(f=>shown.get(f.index) ?? 0))
    let pool = fits.filter(f=>(shown.get(f.index) ?? 0) === fewest)
    // Last chance first: an overlay whose window ends before the next slot could hold it.
    const urgent = pool.filter(f=>overlays[f.index].start_ms + overlays[f.index].duration_ms - f.show < slot + schedule.every_ms)
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

const plans = new WeakMap<Overlay[], Map<string, Appearance[]>>()
/** `randomPlan`, cached per overlay list (renderers ask every frame). */
export function planOf(scene:OverlayScene, mediaId:string|null, iteration:number):Appearance[]{
  const schedule = {...DEFAULT_SCHEDULE, ...scene.schedule}
  const key = `${scene.timeline_id ?? 0}:${mediaId}:${iteration}:${schedule.every_ms}:${schedule.min_gap_ms}`
  let byKey = plans.get(scene.overlays)
  if (!byKey) {byKey = new Map();plans.set(scene.overlays, byKey)}
  let plan = byKey.get(key)
  if (!plan) {
    plan = randomPlan(scene.overlays, mediaId, schedule, scene.timeline_id ?? 0, iteration)
    if (byKey.size > 64) byKey.clear()
    byKey.set(key, plan)
  }
  return plan
}

/**
 * The scene as drawn at `ms` of loop `iteration` of `mediaId`. Fixed mode: unchanged. Random mode: same list (indexes
 * kept, editors select by index); each overlay of that video becomes its appearance covering `ms` (start_ms/duration_ms
 * of the appearance, so the shared in/out animations play on it), or disabled when it is not on screen.
 */
export function sceneAt(scene:OverlayScene, mediaId:string|null, ms:number, iteration:number):OverlayScene{
  if (scene.schedule?.mode !== 'random') return scene
  const now = planOf(scene, mediaId, iteration).find(a=>ms >= a.start_ms && ms < a.end_ms)
  return {...scene, overlays:scene.overlays.map((o, index)=>{
    if (o.media_id !== mediaId) return o
    if (now?.index !== index) return {...o, enabled:false}
    return {...o, start_ms:now.start_ms, duration_ms:now.end_ms - now.start_ms}
  })}
}
