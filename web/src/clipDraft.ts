import type { ClipMode } from './cloudMediaApi'

export type ClipDraft = { cuts:number[]; dropped:string[]; names:Record<string,string>; mode:ClipMode; keepLinks:boolean }
const key = (mediaId:string) => `sarae-clip-draft:${mediaId}`
const empty = ():ClipDraft => ({cuts:[], dropped:[], names:{}, mode:'COPY', keepLinks:true})

/** Keep only editor choices; the source video remains on the server. */
export function readClipDraft(mediaId:string):ClipDraft{
  try{
    const value = JSON.parse(localStorage.getItem(key(mediaId)) || 'null') as Partial<ClipDraft>|null
    if (!value) return empty()
    return {
      cuts:Array.isArray(value.cuts) ? value.cuts.filter((n):n is number=>typeof n === 'number' && Number.isFinite(n) && n > 0).slice(0, 99).sort((a,b)=>a-b) : [],
      dropped:Array.isArray(value.dropped) ? value.dropped.filter((s):s is string=>typeof s === 'string').slice(0, 100) : [],
      names:value.names && typeof value.names === 'object' ? Object.fromEntries(Object.entries(value.names).filter(([k,v])=>typeof k === 'string' && typeof v === 'string')) : {},
      mode:value.mode === 'PRECISE' ? 'PRECISE' : 'COPY',
      keepLinks:value.keepLinks !== false,
    }
  }catch{return empty()}
}

export function writeClipDraft(mediaId:string, draft:ClipDraft){
  try{localStorage.setItem(key(mediaId), JSON.stringify(draft))}catch{/* storage may be unavailable */}
}

export function clearClipDraft(mediaId:string){
  try{localStorage.removeItem(key(mediaId))}catch{/* storage may be unavailable */}
}
