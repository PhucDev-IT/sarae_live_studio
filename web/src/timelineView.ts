/** Tick marks of the editor's timeline (ruler). */

/** Tick marks every 10 s … 1 h depending on how much of the video is visible (about 12 on screen). */
export function ticksOf(total:number, zoom:number):number[]{
  if (!total) return []
  const visible = total / zoom
  const step = [10_000, 30_000, 60_000, 300_000, 600_000, 1_800_000, 3_600_000].find(s=>visible / s <= 12) ?? 3_600_000
  return Array.from({length:Math.floor(total / step) + 1}, (_, i)=>i * step)
}
