// Ported from web/src/overlay (dev branch) for the local Studio: product ids are UUID strings.
export function hms(ms?:number|null){
  if (ms == null || !Number.isFinite(ms) || ms < 0) return '--:--'
  const total = Math.floor(ms / 1000)
  const h = Math.floor(total / 3600), m = Math.floor(total % 3600 / 60), s = total % 60
  return (h ? [h, m, s] : [m, s]).map(n=>String(n).padStart(2, '0')).join(':')
}
