import {useEffect,useState} from 'react'
import {call} from '../../services/core'
import type {AutoPinStatus} from '../../types/ipc'
import {useApp} from '../../app/store'

const DEFAULT_URL='https://shop.tiktok.com/streamer/live/product/dashboard'
export function AutoPinPage(){
 const {run}=useApp()
 const [url,setUrl]=useState(DEFAULT_URL)
 const [products,setProducts]=useState('')
 const [interval,setIntervalSeconds]=useState(30)
 const [status,setStatus]=useState<AutoPinStatus|null>(null)
 const [busy,setBusy]=useState(false)
 const refresh=()=>call('autopin.status').then(setStatus).catch(()=>undefined)
 useEffect(()=>{void refresh();const timer=setInterval(()=>void refresh(),2000);return()=>clearInterval(timer)},[])
 const doAction=async(fn:()=>Promise<AutoPinStatus>,msg:string)=>{
  setBusy(true)
  try{const next=await run(fn,msg);if(next)setStatus(next)}finally{setBusy(false)}
 }
 return <div className="grid grid-2">
  <section className="card" style={{padding:22}}>
   <h2>TikTok LIVE Console · Tự động ghim</h2>
   <p className="muted small">Sarae mở Chrome riêng. Bạn đăng nhập TikTok và vào trang quản lý sản phẩm của phiên LIVE. Agent không đọc mật khẩu hoặc xử lý CAPTCHA.</p>
   <label className="field">LIVE Console URL
    <input className="input" type="url" value={url} onChange={e=>setUrl(e.target.value)}/>
   </label>
   <div className="row"><button className="btn" disabled={busy} onClick={()=>void doAction(()=>call('autopin.open',{url}),'Đã mở Chrome dành riêng cho Auto Pin')}>Mở Chrome LIVE Console</button></div>
   <label className="field" style={{display:'block',marginTop:18}}>Tên hoặc mã sản phẩm (mỗi dòng một sản phẩm)
    <textarea className="input" rows={7} value={products} onChange={e=>setProducts(e.target.value)} placeholder={'Sản phẩm 1\nSản phẩm 2'} style={{width:'100%',marginTop:8}}/>
   </label>
   <label className="field">Chu kỳ ghim (giây)
    <input className="input" type="number" min={5} max={3600} value={interval} onChange={e=>setIntervalSeconds(Number(e.target.value))}/>
   </label>
   <div className="row" style={{display:'flex',gap:8,flexWrap:'wrap',marginTop:18}}>
    <button className="btn" disabled={busy} onClick={()=>void doAction(()=>call('autopin.configure',{products:products.split('\n').map(s=>s.trim()).filter(Boolean),interval_seconds:interval}),'Đã lưu danh sách Auto Pin')}>Lưu danh sách</button>
    <button className="btn btn-primary" disabled={busy||!status?.browser_open} onClick={()=>void doAction(()=>call('autopin.start'),'Auto Pin đã bật')}>Bắt đầu ghim</button>
    <button className="btn" disabled={busy||!status?.enabled} onClick={()=>void doAction(()=>call('autopin.stop'),'Đã dừng Auto Pin')}>Dừng</button>
   </div>
  </section>
  <section className="card" style={{padding:22}}>
   <h2>Trạng thái Auto Pin</h2>
   <p><strong>Trạng thái:</strong> {status?.state??'Đang kiểm tra'}</p>
   <p><strong>Chrome:</strong> {status?.browser_open?'Đã mở':'Chưa mở'}</p>
   <p><strong>Sản phẩm:</strong> {status?.products.length??0}</p>
   <p><strong>Chu kỳ:</strong> {status?.interval_seconds??'—'} giây</p>
   <p><strong>Ghim gần nhất:</strong> {status?.last_pin||'Chưa có'}</p>
   {status?.last_error&&<p role="alert" style={{color:'#f87171'}}>Lỗi: {status.last_error}</p>}
   <p className="muted small">Phiên bản thử nghiệm: phụ thuộc cấu trúc DOM của TikTok LIVE Console và tên nút Ghim/Bỏ ghim. Nếu không tìm thấy đúng nút, Agent dừng để tránh ghim nhầm. Cần kiểm thử với phiên LIVE thực tế.</p>
  </section>
 </div>
}
