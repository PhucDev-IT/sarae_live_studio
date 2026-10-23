import { useState } from 'react'
import { ChevronDown, ChevronUp, CircleAlert, CircleCheck, Film, Loader2, Scissors, WifiOff, X } from 'lucide-react'
import { clipJobs, isFinal, useClipJobs } from './clipJobStore'
import './upload-dock.css'

/** Floating progress of video cuts running on the server, on every page (also over the video detail panel). */
export default function ClipDock({onOpenLibrary}:{onOpenLibrary:()=>void}){
  const jobs = useClipJobs()
  const [open,setOpen] = useState(true)
  if (!jobs.length) return null
  const active = jobs.filter(t=>!isFinal(t.job) && !t.lost)
  const failed = jobs.filter(t=>t.job.status === 'FAILED' || t.lost).length
  const percent = active.length ? Math.round(active.reduce((s, t)=>s + t.job.progress, 0) / active.length * 100) : 100
  const title = active.length ? `Đang cắt ${active.length} video · ${percent}%`
    : failed ? `${failed} lần cắt video bị lỗi` : `Đã cắt xong ${jobs.reduce((s, t)=>s + t.job.done, 0)} đoạn video`

  return <aside className={'ud' + (open ? ' is-open' : '')} aria-label="Tiến trình cắt video" aria-live="polite">
    <header className="ud-head">
      {active.length ? <Loader2 size={16} className="ud-spin"/> : failed ? <CircleAlert size={16} className="ud-bad"/> : <CircleCheck size={16} className="ud-ok"/>}
      <button type="button" className="ud-title" onClick={()=>setOpen(o=>!o)}>{title}</button>
      <button type="button" className="ud-icon" aria-label={open ? 'Thu gọn' : 'Mở rộng'} onClick={()=>setOpen(o=>!o)}>{open ? <ChevronDown size={16}/> : <ChevronUp size={16}/>}</button>
      {!active.length && <button type="button" className="ud-icon" aria-label="Đóng" onClick={()=>clipJobs.clearFinished()}><X size={16}/></button>}
    </header>
    {active.length > 0 && <div className="ud-total"><span style={{width:`${Math.max(3, percent)}%`}}/></div>}
    {open && <ul className="ud-list">{jobs.map(({job, name, lost})=>{
      const done = job.status === 'DONE', bad = job.status === 'FAILED'
      return <li key={job.id} className={'ud-row' + (bad || lost ? ' is-error' : '')}>
        <Scissors size={15} className="ud-kind"/>
        <div className="ud-row-main">
          <strong title={name}>{name}</strong>
          <small>{lost ? 'Mất kết nối, máy chủ có thể vẫn đang cắt'
            : bad ? (job.error || 'Cắt video thất bại') + (job.done ? ` · ${job.done} đoạn đã lưu` : '')
            : done ? `Đã tạo ${job.done} video mới`
            : `${job.phase} · ${Math.round(job.progress * 100)}%`}</small>
          {!done && !bad && !lost && <div className="ud-bar"><span style={{width:`${Math.max(3, job.progress * 100)}%`}}/></div>}
        </div>
        {lost ? <button type="button" className="ud-icon" aria-label="Kiểm tra lại" title="Kiểm tra lại" onClick={()=>clipJobs.resume(job.id)}><WifiOff size={15}/></button>
          : bad ? <CircleAlert size={16} className="ud-bad"/>
          : done ? <CircleCheck size={16} className="ud-ok"/>
          : <Loader2 size={16} className="ud-spin"/>}
        {(done || bad) && <button type="button" className="ud-icon" aria-label="Ẩn" title="Ẩn" onClick={()=>clipJobs.dismiss(job.id)}><X size={14}/></button>}
      </li>
    })}</ul>}
    {open && jobs.some(t=>t.job.status === 'DONE') && <button type="button" className="ud-link" onClick={onOpenLibrary}><Film size={14}/> Xem video mới trong thư viện</button>}
  </aside>
}
