import { useState } from 'react'
import { CheckCircle2, ChevronDown, ChevronUp, CircleAlert, CloudUpload, Loader2, RotateCcw, X } from 'lucide-react'
import { sizeLabel } from './mediaApi'
import { uploads, useUploads, type UploadTask } from './uploadStore'
import './upload-dock.css'

const percent = (t:UploadTask) => t.size ? Math.min(100, Math.round(t.sent / t.size * 100)) : 0

/** Floating progress of Media Library uploads, visible on every page while files upload in the background. */
export default function UploadDock({onOpenLibrary}:{onOpenLibrary:()=>void}){
  const tasks = useUploads()
  const [open,setOpen] = useState(true)
  if (!tasks.length) return null
  const active = tasks.filter(t=>t.state === 'queued' || t.state === 'uploading' || t.state === 'finishing')
  const failed = tasks.filter(t=>t.state === 'error').length
  const sent = active.reduce((s, t)=>s + t.sent, 0), total = active.reduce((s, t)=>s + t.size, 0)
  const title = active.length ? `Đang tải lên ${active.length} video · ${total ? Math.round(sent / total * 100) : 0}%`
    : failed ? `${failed} video tải lên lỗi` : `Đã tải lên ${tasks.filter(t=>t.state === 'done').length} video`

  return <aside className={'ud' + (open ? ' is-open' : '')} aria-label="Tiến trình tải video">
    <header className="ud-head">
      {active.length ? <Loader2 size={16} className="ud-spin"/> : failed ? <CircleAlert size={16} className="ud-bad"/> : <CheckCircle2 size={16} className="ud-ok"/>}
      <button type="button" className="ud-title" onClick={()=>setOpen(o=>!o)}>{title}</button>
      <button type="button" className="ud-icon" aria-label={open ? 'Thu gọn' : 'Mở rộng'} onClick={()=>setOpen(o=>!o)}>{open ? <ChevronDown size={16}/> : <ChevronUp size={16}/>}</button>
      {!active.length && <button type="button" className="ud-icon" aria-label="Đóng" onClick={()=>uploads.clearFinished()}><X size={16}/></button>}
    </header>
    {active.length > 0 && <div className="ud-total"><span style={{width:`${total ? sent / total * 100 : 0}%`}}/></div>}
    {open && <ul className="ud-list">{tasks.map(t=><li key={t.key} className={'ud-row is-' + t.state}>
      <div className="ud-row-main">
        <strong title={t.name}>{t.name}</strong>
        <small>{t.state === 'queued' ? 'Đang chờ' : t.state === 'uploading' ? `${sizeLabel(t.sent)} / ${sizeLabel(t.size)}${t.resumed ? ' · tiếp tục' : ''}`
          : t.state === 'finishing' ? 'Đang tạo ảnh bìa…' : t.state === 'done' ? 'Hoàn tất' : t.state === 'cancelled' ? 'Đã hủy' : t.error}</small>
        {(t.state === 'uploading' || t.state === 'queued') && <div className="ud-bar"><span style={{width:`${percent(t)}%`}}/></div>}
      </div>
      {(t.state === 'uploading' || t.state === 'queued') && <button type="button" className="ud-icon" aria-label="Hủy" title="Hủy" onClick={()=>uploads.cancel(t.key)}><X size={15}/></button>}
      {t.state === 'error' && <button type="button" className="ud-icon" aria-label="Thử lại" title="Thử lại" onClick={()=>uploads.retry(t.key)}><RotateCcw size={15}/></button>}
      {t.state === 'done' && <CheckCircle2 size={16} className="ud-ok"/>}
    </li>)}</ul>}
    {open && !active.length && tasks.some(t=>t.state === 'done') && <button type="button" className="ud-link" onClick={onOpenLibrary}><CloudUpload size={14}/> Mở thư viện video</button>}
  </aside>
}
