/** Building blocks shared by the catalog pages (products, presenters). Styles live in products.css. */
import { useRef, useState, type DragEvent, type ReactNode } from 'react'
import { Button, Input, useToast } from './ui/core'
import { ImagePlus, Link2, Upload, X } from 'lucide-react'
import { apiRequest, assetUrl, errorText } from './mediaApi'
import './products.css'

export const MAX_IMAGE_MB = 5

export function Stat({icon,label,value,hint,tone,onClick,actionTitle}:{icon:ReactNode;label:string;value:number;hint?:string;tone:string;onClick?:()=>void;actionTitle?:string}){
  const body = <><span className={'pp-stat-icon ' + tone}>{icon}</span><span className="pp-stat-text"><small>{label}</small><strong>{value.toLocaleString('vi-VN')}</strong>{hint && <em>{hint}</em>}</span></>
  return onClick ? <button type="button" className="pp-stat is-link" onClick={onClick} title={actionTitle}>{body}</button> : <div className="pp-stat">{body}</div>
}

export function IconButton({title,danger,onClick,children}:{title:string;danger?:boolean;onClick:()=>void;children:ReactNode}){
  return <button type="button" className={'pp-icon' + (danger ? ' is-danger' : '')} title={title} aria-label={title} onClick={onClick}>{children}</button>
}

/** Photo, falling back to the name's initials (or `fallback`) when missing or broken. Our photos come as signed paths. */
export function Thumb({src,name,fallback}:{src:string;name:string;fallback:ReactNode}){
  const [failedSrc,setFailedSrc] = useState('')
  const url = assetUrl(src)
  if (url && url !== failedSrc) return <img src={url} alt="" loading="lazy" onError={()=>setFailedSrc(url)}/>
  const initials = name.trim().split(/\s+/).slice(0, 2).map(w=>w[0]?.toUpperCase() ?? '').join('')
  return <span className="pp-initials" aria-hidden="true">{initials || fallback}</span>
}

/** Photo picker: upload (click, drop or paste) to `uploadPath`, or paste an image URL. `round` renders an avatar. */
export function ImageField({value,name,uploadPath,label,fallback,round,onChange}:{
  value:string; name:string; uploadPath:string; label:string; fallback:ReactNode; round?:boolean; onChange:(url:string)=>void
}){
  const toast = useToast()
  const input = useRef<HTMLInputElement>(null)
  const [uploading,setUploading] = useState(false)
  const [over,setOver] = useState(false)
  const [urlMode,setUrlMode] = useState(false)
  const [url,setUrl] = useState('')
  const upload = async (file?:File) => {
    if (!file) return
    if (!file.type.startsWith('image/')) {toast.push({tone:'danger', title:'File không phải ảnh'});return}
    if (file.size > MAX_IMAGE_MB * 1024 * 1024) {toast.push({tone:'danger', title:`Ảnh tối đa ${MAX_IMAGE_MB} MB`});return}
    setUploading(true)
    try{
      const body = new FormData()
      body.append('file', file)
      onChange((await apiRequest<{url:string}>(uploadPath, {method:'POST', body})).url)
    }catch(e){toast.push({tone:'danger', title:'Không tải được ảnh lên', description:errorText(e, '')})}
    finally{setUploading(false)}
  }
  const onDrop = (e:DragEvent) => {e.preventDefault();setOver(false);void upload(e.dataTransfer.files[0])}
  const pick = () => input.current?.click()
  return <div className={'pp-image-field' + (round ? ' is-round' : '')}>
    <div className={'pp-drop' + (over ? ' is-over' : '') + (value ? ' has-image' : '')} role="button" tabIndex={0} aria-label={label}
      onClick={pick} onKeyDown={e=>{if (e.key === 'Enter' || e.key === ' ') {e.preventDefault();pick()}}}
      onDragOver={e=>{e.preventDefault();setOver(true)}} onDragLeave={()=>setOver(false)} onDrop={onDrop}
      onPaste={e=>void upload(Array.from(e.clipboardData.files)[0])}>
      {value ? <Thumb src={value} name={name} fallback={fallback}/> : <span className="pp-drop-hint">
        <ImagePlus size={round ? 22 : 26}/>{!round && <><strong>{uploading ? 'Đang tải ảnh…' : 'Kéo thả, dán hoặc bấm để chọn ảnh'}</strong><small>JPG, PNG, WEBP · tối đa {MAX_IMAGE_MB} MB</small></>}
      </span>}
      {uploading && (value || round) && <span className="pp-drop-busy">Đang tải…</span>}
    </div>
    <input ref={input} type="file" accept="image/jpeg,image/png,image/webp,image/gif" hidden onChange={e=>{void upload(e.target.files?.[0]);e.target.value = ''}}/>
    <div className="pp-image-side">
      {round && <small className="pp-image-note">Kéo thả, dán hoặc bấm vào ảnh · JPG, PNG, WEBP · tối đa {MAX_IMAGE_MB} MB</small>}
      <div className="pp-image-actions">
        <Button type="button" size="sm" variant="outline" leftIcon={<Upload size={14}/>} disabled={uploading} onClick={pick}>{value ? 'Đổi ảnh' : 'Tải ảnh lên'}</Button>
        <Button type="button" size="sm" variant="ghost" leftIcon={<Link2 size={14}/>} onClick={()=>{setUrl(value);setUrlMode(m=>!m)}}>Dùng URL</Button>
        {value && <Button type="button" size="sm" variant="ghost" leftIcon={<X size={14}/>} onClick={()=>onChange('')}>Bỏ ảnh</Button>}
      </div>
      {urlMode && <div className="pp-url-row">
        <Input aria-label="URL ảnh" type="url" value={url} placeholder="https://…" onChange={e=>setUrl(e.target.value)}
          onKeyDown={e=>{if (e.key === 'Enter') {e.preventDefault();onChange(url.trim());setUrlMode(false)}}}/>
        <Button type="button" size="sm" onClick={()=>{onChange(url.trim());setUrlMode(false)}}>Áp dụng</Button>
      </div>}
    </div>
  </div>
}
