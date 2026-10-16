import {useCallback,useEffect,useState} from 'react'
import {ArrowDown,ArrowUp,Clock3, ListPlus, Play, RefreshCw, Square, Trash2, Zap} from 'lucide-react'
import {apiRequest, errorText, type Product} from './mediaApi'
import './live-sessions.css'

type Session={id:number;name:string;status:'DRAFT'|'RUNNING'|'ENDED';product_ids:number[];current_product_id:number|null;created_at:string;started_at:string|null;ended_at:string|null}
type Event={id:number;event_type:string;product_id:number|null;detail:string;created_at:string}
type LiveStatus={running:boolean;session_id?:number|null;product_id?:number|null;pending_product_id?:number|null;last_status?:string}
const json=(data:unknown,method='POST'):RequestInit=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
const fmt=(s:string|null)=>s?new Date(s+'Z').toLocaleString('vi-VN'):'—'
export default function LiveSessions(){
 const [sessions,setSessions]=useState<Session[]>([])
 const [products,setProducts]=useState<Product[]>([])
 const [counts,setCounts]=useState<Record<number,number>>({})
 const [selected,setSelected]=useState<number|null>(null)
 const [name,setName]=useState('')
 const [queue,setQueue]=useState<number[]>([])
 const [pick,setPick]=useState('')
 const [events,setEvents]=useState<Event[]>([])
 const [status,setStatus]=useState<LiveStatus|null>(null)
 const [switchMode,setSwitchMode]=useState<'immediate'|'after_current'>('immediate')
 const [error,setError]=useState('')
 const [notice,setNotice]=useState('')
 const [busy,setBusy]=useState(false)
 const [dirty,setDirty]=useState(false)
 const refresh=useCallback(async()=>{
   const [s,p,c,st]=await Promise.all([
     apiRequest<Session[]>('/live/sessions'),
     apiRequest<Product[]>('/products?limit=500'),
     apiRequest<{id:number;video_count:number}[]>('/live/products'),
     apiRequest<LiveStatus>('/live/status'),
   ])
   setSessions(s);setProducts(p);setCounts(Object.fromEntries(c.map(x=>[x.id,x.video_count])));setStatus(st)
 },[])
 useEffect(()=>{void refresh().catch(e=>setError(errorText(e,'Không tải được dữ liệu')))},[refresh])
 useEffect(()=>{const t=window.setInterval(()=>{void apiRequest<LiveStatus>('/live/status').then(setStatus).catch(()=>{})},3000);return()=>window.clearInterval(t)},[])
 useEffect(()=>{if(selected===null){setEvents([]);return}void apiRequest<Event[]>(`/live/sessions/${selected}/events`).then(setEvents).catch(()=>setEvents([]))},[selected,sessions])
 const chosen=sessions.find(s=>s.id===selected)
 const choose=(id:number|null)=>{
   if(dirty&&!window.confirm('Bạn có thay đổi chưa lưu. Chuyển phiên và bỏ thay đổi?'))return
   const s=sessions.find(x=>x.id===id);setSelected(id);setQueue(s?[...s.product_ids]:[]);setDirty(false);setError('');setNotice('')
 }
 const run=async(action:()=>Promise<unknown>,message:string)=>{
   setBusy(true);setError('');setNotice('')
   try{await action();await refresh();setNotice(message)}catch(e){setError(errorText(e,'Có lỗi xảy ra'))}finally{setBusy(false)}
 }
 const title=(id:number)=>products.find(p=>p.id===id)?.name||'Sản phẩm #'+id
 const move=(i:number,delta:number)=>{const arr=[...queue];const j=i+delta;if(j<0||j>=arr.length)return;[arr[i],arr[j]]=[arr[j],arr[i]];setQueue(arr);setDirty(true)}
 const active=chosen?.status==='RUNNING'
 const owner=active&&status?.running&&status?.session_id===chosen?.id
 return <div className="ls-page">
   <div className="ls-toolbar"><div><strong>Quản lý phiên LIVE</strong><p>Chuẩn bị danh sách sản phẩm, điều khiển OBS và lưu lịch sử. Ghim TikTok thủ công trong Phase 1.</p></div><button className="ls-button" onClick={()=>void refresh().catch(e=>setError(errorText(e,'Không thể làm mới')))}><RefreshCw size={16}/> Làm mới</button></div>
   {error&&<div className="ls-alert ls-error" role="alert">{error}</div>}{notice&&<div className="ls-alert" role="status">{notice}</div>}
   <div className="ls-grid">
     <section className="ls-panel">
       <h3>Phiên LIVE</h3>
       <form className="ls-create" onSubmit={e=>{e.preventDefault();if(!name.trim()||!queue.length)return;void run(async()=>{const s=await apiRequest<Session>('/live/sessions',json({name:name.trim(),product_ids:queue}));setSelected(s.id);setDirty(false);setName('')},'Đã tạo phiên LIVE')}}>
         <label>Tên phiên mới<input value={name} onChange={e=>setName(e.target.value)} placeholder="LIVE thời trang tối nay"/></label>
         <p className="ls-muted">Để tạo phiên, thêm sản phẩm vào danh sách bên phải trước.</p>
         <button className="ls-button ls-primary" disabled={busy||!name.trim()||!queue.length}>Tạo phiên từ danh sách ({queue.length})</button>
       </form>
       <div className="ls-session-list">
         {sessions.length===0&&<p className="ls-muted">Chưa có phiên LIVE nào.</p>}
         {sessions.map(s=><button key={s.id} className={'ls-session '+(selected===s.id?'selected':'')} onClick={()=>choose(s.id)}>
           <strong>{s.name}</strong><small>{s.product_ids.length} sản phẩm · {fmt(s.created_at)}</small>
           <span className={'ls-chip '+s.status}>{s.status==='DRAFT'?'Chuẩn bị':s.status==='RUNNING'?'Đang LIVE':'Đã kết thúc'}</span>
         </button>)}
       </div>
     </section>
     <section className="ls-panel">
       <div className="ls-panel-header"><div><h3>Danh sách sản phẩm</h3><p className="ls-muted">{chosen?chosen.name:'Chuẩn bị danh sách để tạo phiên mới hoặc chọn phiên đã lưu'}</p></div><span className="ls-chip">{queue.length} sản phẩm</span></div>
       <div className="ls-add"><select value={pick} onChange={e=>setPick(e.target.value)} aria-label="Chọn sản phẩm để thêm"><option value="">Chọn sản phẩm để thêm…</option>{products.filter(p=>!queue.includes(p.id)).map(p=><option key={p.id} value={p.id}>{p.name} — {counts[p.id]||0} video</option>)}</select><button className="ls-button" disabled={!pick||busy||chosen?.status==='ENDED'} onClick={()=>{setQueue([...queue,Number(pick)]);setPick('');setDirty(true)}}><ListPlus size={16}/> Thêm</button></div>
       {queue.length===0&&<div className="ls-empty">Danh sách trống. Hãy chọn những sản phẩm dự kiến bán trong phiên.</div>}
       <div className="ls-products">{queue.map((pid,i)=><div className="ls-product" key={pid}>
         <span className="ls-number">{i+1}</span><div className="ls-product-name"><strong>{title(pid)}</strong><small>{counts[pid]||0} video {counts[pid]?'đã cấu hình':'— chưa sẵn sàng LIVE'}</small></div>
         {chosen?.current_product_id===pid&&active&&<span className="ls-chip RUNNING">Đang phát</span>}
         <div className="ls-icon-actions"><button title="Lên" disabled={busy||i===0||chosen?.status==='ENDED'} onClick={()=>move(i,-1)}><ArrowUp size={16}/></button><button title="Xuống" disabled={busy||i===queue.length-1||chosen?.status==='ENDED'} onClick={()=>move(i,1)}><ArrowDown size={16}/></button><button title="Loại khỏi phiên" disabled={busy||chosen?.status==='ENDED'||(owner&&status?.product_id===pid)} onClick={()=>{setQueue(queue.filter(id=>id!==pid));setDirty(true)}}><Trash2 size={16}/></button></div>
       </div>)}</div>
       {chosen&&chosen.status!=='ENDED'&&<div className="ls-actions"><button className="ls-button ls-primary" disabled={busy||!dirty||!queue.length} onClick={()=>void run(async()=>{await apiRequest(`/live/sessions/${chosen.id}/products`,json({product_ids:queue},'PUT'));setDirty(false)},'Đã lưu danh sách sản phẩm')}>Lưu danh sách</button>{dirty&&<span className="ls-muted">Có thay đổi chưa lưu</span>}</div>}
       {chosen&&<div className="ls-runtime">
         <h3>Điều khiển phiên</h3>
         <p className="ls-muted">Ghim sản phẩm trên TikTok trước, sau đó chọn sản phẩm tương ứng để OBS chuyển video.</p>
         <div className="ls-state">Trạng thái: <b>{chosen.status}</b> · OBS: <b>{status?.running?'Đang chạy':'Đã dừng'}</b>{status?.pending_product_id!=null&&<> · Chờ chuyển: <b>{title(status.pending_product_id)}</b></>}</div>
         {chosen.status==='DRAFT'&&<button className="ls-button ls-primary" disabled={busy||!queue.length||dirty||!!status?.running||!counts[queue[0]]} onClick={()=>void run(()=>apiRequest(`/live/sessions/${chosen.id}/start`,json({product_id:queue[0]})),'Đã bắt đầu phát trên OBS')}><Play size={16}/> Bắt đầu phiên với {title(queue[0])}</button>}
         {owner&&<><div className="ls-mode"><label><input type="radio" checked={switchMode==='immediate'} onChange={()=>setSwitchMode('immediate')}/> Chuyển ngay</label><label><input type="radio" checked={switchMode==='after_current'} onChange={()=>setSwitchMode('after_current')}/> Sau video hiện tại</label></div>
           <div className="ls-switch">{chosen.product_ids.map(pid=><button key={pid} className="ls-button" disabled={busy||!counts[pid]||pid===status?.product_id||dirty} onClick={()=>void run(()=>apiRequest(`/live/sessions/${chosen.id}/switch`,json({product_id:pid,mode:switchMode})),'Đã gửi yêu cầu chuyển sang '+title(pid))}><Zap size={15}/> {title(pid)}</button>)}</div>
           <button className="ls-button ls-danger" disabled={busy} onClick={()=>{if(window.confirm('Kết thúc phiên LIVE và dừng OBS?'))void run(()=>apiRequest(`/live/sessions/${chosen.id}/finish`,{method:'POST'}),'Đã kết thúc phiên LIVE')}}><Square size={16}/> Kết thúc phiên</button>
         </>}
         {active&&!owner&&<p className="ls-muted">Phiên đang được đánh dấu RUNNING nhưng không còn gắn với playback hiện tại. Kiểm tra Backend trước khi thao tác.</p>}
       </div>}
     </section>
   </div>
   {chosen&&<section className="ls-panel ls-history"><h3><Clock3 size={18}/> Lịch sử phiên #{chosen.id}</h3>{events.length===0?<p className="ls-muted">Chưa có sự kiện.</p>:<div className="ls-events">{events.slice().reverse().map(ev=><div key={ev.id}><time>{fmt(ev.created_at)}</time><strong>{ev.event_type}</strong><span>{ev.product_id!=null?title(ev.product_id):''} {ev.detail}</span></div>)}</div>}</section>}
 </div>
}
