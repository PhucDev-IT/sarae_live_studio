/** What the pin timeline and the overlay tracks share, so the two always line up: zoom levels, tick spacing and one
 * horizontal scroll position. */
import { useCallback, useRef } from 'react'

export const ZOOMS = [1, 2, 4, 8, 16, 32]

/** Tick marks every 10 s … 1 h depending on how much of the video is visible (about 12 on screen). */
export function ticksOf(total:number, zoom:number):number[]{
  if (!total) return []
  const visible = total / zoom
  const step = [10_000, 30_000, 60_000, 300_000, 600_000, 1_800_000, 3_600_000].find(s=>visible / s <= 12) ?? 3_600_000
  return Array.from({length:Math.floor(total / step) + 1}, (_, i)=>i * step)
}

/**
 * Scroll containers that move together: `link(key)` is a ref callback; scrolling one scrolls the others to the same
 * position (they have the same width and the same zoom, so the same time is at the same x).
 */
export function useLinkedScroll(){
  const boxes = useRef(new Map<string, HTMLDivElement>())
  const refs = useRef(new Map<string, (el:HTMLDivElement|null)=>void>())
  // No lock needed: a box is only moved when its position differs, so the echo of a scroll ends at once.
  const follow = useCallback((e:Event)=>{
    const source = e.currentTarget as HTMLDivElement
    boxes.current.forEach(box=>{if (box !== source && Math.abs(box.scrollLeft - source.scrollLeft) >= 1) box.scrollLeft = source.scrollLeft})
  },[])
  return useCallback((key:string)=>{
    let ref = refs.current.get(key)
    if (!ref) {
      ref = (el:HTMLDivElement|null) => {
        const old = boxes.current.get(key)
        if (old === el) return
        old?.removeEventListener('scroll', follow)
        if (el) {
          el.addEventListener('scroll', follow, {passive:true})
          boxes.current.set(key, el)
          const other = [...boxes.current.values()].find(b=>b !== el)
          if (other) el.scrollLeft = other.scrollLeft
        } else boxes.current.delete(key)
      }
      refs.current.set(key, ref)
    }
    return ref
  },[follow])
}
