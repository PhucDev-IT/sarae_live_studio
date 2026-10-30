import { useMemo, useState } from 'react'
import { SearchInput } from './ui/core'
import { Check, Film, Package } from 'lucide-react'
import { assetUrl, durationLabel } from './mediaApi'
import type { CloudMedia } from './cloudMediaApi'
import './video-picker.css'

const fold = (text:string) => text.normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd').toLowerCase()

/** One video of the Media Library as a selectable cover tile (9:16), with its duration and first linked product. */
function Tile({video, selected, order, productName, onPick}:{video:CloudMedia; selected:boolean; order?:number; productName?:string; onPick:()=>void}){
  const [broken,setBroken] = useState(false)
  const duration = durationLabel(video.duration_seconds)
  return <button type="button" className={'vp-tile' + (selected ? ' is-selected' : '')} aria-pressed={selected} onClick={onPick} title={video.display_name || video.filename}>
    <span className="vp-cover">
      {video.thumb_url && !broken ? <img src={assetUrl(video.thumb_url) ?? undefined} alt="" loading="lazy" onError={()=>setBroken(true)}/> : <Film size={22}/>}
      {duration && <span className="vp-duration">{duration}</span>}
      {selected && <span className="vp-check">{order != null ? order : <Check size={14}/>}</span>}
    </span>
    <span className="vp-name">{video.display_name || video.filename}</span>
    {productName && <span className="vp-product"><Package size={11}/>{productName}</span>}
  </button>
}

/**
 * Pick Media Library videos by their cover (the file name rarely says what the video shows). Searchable by name, tags
 * and linked product names. `value` is one id, or a list (several videos: each tile shows its place in the list);
 * `onChange` gets the tile clicked, the caller toggles it.
 */
export default function VideoPicker({videos, value, onChange, productName = ()=>undefined, maxHeight = 360}:{
  videos:CloudMedia[]; value:string|string[]|null; onChange:(id:string)=>void; productName?:(id:number)=>string|undefined; maxHeight?:number
}){
  const order = (id:string) => Array.isArray(value) ? value.indexOf(id) + 1 || undefined : undefined
  const [query,setQuery] = useState('')
  const shown = useMemo(()=>{
    const q = fold(query.trim())
    return !q ? videos : videos.filter(v=>fold([v.display_name, v.filename, v.tags, ...v.product_ids.map(id=>productName(id) ?? '')].join(' ')).includes(q))
  },[videos, query, productName])
  if (!videos.length) return <div className="vp-empty"><Film size={24}/><span>Thư viện chưa có video. Tải video lên ở trang <a href="#/cloud-media">Thư viện video</a>.</span></div>
  return <div className="vp">
    {videos.length > 6 && <SearchInput value={query} onChange={e=>setQuery(e.target.value)} placeholder="Tìm theo tên video, tag, sản phẩm…" aria-label="Tìm video"/>}
    <div className="vp-grid" style={{maxHeight}}>
      {shown.map(v=><Tile key={v.id} video={v} selected={Array.isArray(value) ? value.includes(v.id) : v.id === value} order={order(v.id)} productName={v.product_ids[0] != null ? productName(v.product_ids[0]) : undefined} onPick={()=>onChange(v.id)}/>)}
      {!shown.length && <p className="vp-none">Không có video khớp “{query}”.</p>}
    </div>
  </div>
}
