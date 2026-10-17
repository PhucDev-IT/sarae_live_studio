import {useEffect,useState} from 'react'
import {AlertTriangle,Camera,Play,Square,RefreshCw} from 'lucide-react'
import {apiRequest,errorText} from './mediaApi'
import './verification-monitor.css'
import AgentCloudStatus from './AgentCloudStatus'
type Event={id:number;at:string;kind:string;detail:string;image:string|null}
type Monitor={state:{running:boolean;window_found:boolean;window_title:string;window_visible:boolean;status:string;last_scan:string|null;error:string|null;events_count:number;match_score?:number;window_info?:{pid:number;hwnd:number;exe_name:string;exe_path:string;title:string;minimized:boolean}|null;monitors_detected?:number};config:{interval_seconds:number;threshold:number;telegram_enabled:boolean;telegram_chat_id:string;template_loaded:boolean};events:Event[]}
const payload=(obj:unknown,method='POST'):RequestInit=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(obj)})
export default function VerificationMonitor(){
 const [data,setData]=useState<Monitor|null>(null)
 const [config,setConfig]=useState<Monitor['config']|null>(null)
 const [busy,setBusy]=useState(false)
 const [templates,setTemplates]=useState<{name:string;bytes:number}[]>([])
 const [error,setError]=useState('')
 const [message,setMessage]=useState('')
 const refresh=async()=>{const v=await apiRequest<Monitor>('/verification-monitor/status');setData(v);setConfig(old=>old??v.config);apiRequest<{templates:{name:string;bytes:number}[]}>('/verification-monitor/templates').then(x=>setTemplates(x.templates)).catch(()=>{})}
 useEffect(()=>{void refresh().catch(()=>{});const timer=setInterval(()=>void refresh().catch(()=>{}),2500);return()=>clearInterval(timer)},[])
 const action=async(fn:()=>Promise<unknown>,success:string)=>{setBusy(true);setError('');try{await fn();setMessage(success);await refresh()}catch(e){setError(errorText(e,'Thao tác không thành công'))}finally{setBusy(false)}}
 const upload=async(file:File)=>{
  if(file.size>3_000_000){setError('Ảnh quá lớn, tối đa 3 MB');return}
  const image=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(new Error('Không đọc được file'));reader.readAsDataURL(file)})
  await action(()=>apiRequest('/verification-monitor/template',payload({png_base64:image})),'Đã lưu mẫu CAPTCHA')
 }
 const fmt=(v:string)=>new Date(v).toLocaleString('vi-VN')
 return <div className="vm-layout">
  <section className="vm-panel"><h3><Camera size={19}/> CAPTCHA Monitor V1</h3><p>Theo dõi sự xuất hiện của hộp thoại xác minh trên màn hình TikTok LIVE Studio. Không tự giải hoặc điều khiển chuột. Cửa sổ phải hiện trên màn hình và không bị che khuất.</p>
   <div className="vm-stats"><div><small>Monitor</small><strong>{data?.state.running?'Đang chạy':'Đã dừng'}</strong></div><div><small>TikTok LIVE Studio</small><strong>{data?.state.window_visible?'Đang hiển thị':data?.state.window_found?'Đã thu nhỏ':'Chưa tìm thấy'}</strong></div><div><small>CAPTCHA</small><strong>{data?.state.status==='captcha_detected'?'Nghi vấn phát hiện':data?.state.status==='watching'?'Chưa phát hiện':data?.state.status||'Chưa kết nối'}</strong></div><div><small>Độ khớp gần nhất</small><strong>{data?.state.match_score??'—'}</strong></div></div>
   {data?.state.window_info&&<div className="vm-event"><strong>Ứng dụng đang giám sát</strong><p>{data.state.window_info.exe_name} · PID {data.state.window_info.pid} · HWND {data.state.window_info.hwnd}</p><small>Đường dẫn: {data.state.window_info.exe_path}</small><small>Tiêu đề: {data.state.window_info.title || '(Không có tiêu đề)'}</small><small>Số màn hình: {data.state.monitors_detected??'Đang kiểm tra'}</small></div>}
   {data?.state.error&&<p className="vm-error">{data.state.error}</p>}
   <div className="vm-actions"><button disabled={busy||!!data?.state.running} onClick={()=>void action(()=>apiRequest('/verification-monitor/start',{method:'POST'}),'Đã bật giám sát')}><Play size={16}/> Bật Monitor</button><button disabled={busy||!data?.state.running} onClick={()=>void action(()=>apiRequest('/verification-monitor/stop',{method:'POST'}),'Đã dừng giám sát')}><Square size={16}/> Dừng</button><button onClick={()=>void refresh()}><RefreshCw size={16}/> Làm mới</button></div>
  </section>
  <section className="vm-panel"><h3>Cấu hình phát hiện</h3><p>Chụp và cắt một vùng nhỏ chứa tiêu đề/chi tiết nhận diện CAPTCHA làm mẫu. Tránh vùng ảnh ghép hoặc chữ thường xuyên thay đổi. Phát hiện dựa trên so khớp ảnh, nên có thể báo nhầm hoặc bỏ sót khi đổi tỷ lệ hiển thị.</p>
   <label>Thêm mẫu nhận diện CAPTCHA (PNG/JPG) <input type="file" accept="image/png,image/jpeg" disabled={busy} onChange={e=>{const f=e.target.files?.[0];if(f)void upload(f);e.target.value=''}}/></label><small>{templates.length} mẫu đang có. Có thể bổ sung thêm ảnh bất kỳ lúc nào; Monitor sẽ so khớp tất cả mẫu.</small><div className="vm-events">{templates.map(t=><div className="vm-event" key={t.name}><strong>{t.name}</strong><small>{Math.round(t.bytes/1024)} KB</small>{t.name.startsWith('custom_')&&<button disabled={busy} onClick={()=>void action(()=>apiRequest('/verification-monitor/templates/'+encodeURIComponent(t.name),{method:'DELETE'}),'Đã xoá mẫu')}>Xoá mẫu</button>}</div>)}</div>
   {config&&<><div className="vm-inputs"><label>Quét mỗi (giây)<input type="number" min={0.5} max={15} step={0.5} value={config.interval_seconds} onChange={e=>setConfig({...config,interval_seconds:Number(e.target.value)})}/></label><label>Ngưỡng khớp (0.65–0.99)<input type="number" min={0.65} max={0.99} step={0.01} value={config.threshold} onChange={e=>setConfig({...config,threshold:Number(e.target.value)})}/></label></div><label className="vm-check"><input type="checkbox" checked={config.telegram_enabled} onChange={e=>setConfig({...config,telegram_enabled:e.target.checked})}/> Thông báo Telegram khi phát hiện / biến mất</label><label>Telegram Chat ID<input placeholder="Chat ID nhận thông báo" value={config.telegram_chat_id} onChange={e=>setConfig({...config,telegram_chat_id:e.target.value})}/></label><small>Đặt SARAE_TELEGRAM_BOT_TOKEN trong backend/.env hoặc biến môi trường trước khi chạy. Không nhập token trên giao diện.</small><button disabled={busy} onClick={()=>void action(()=>apiRequest('/verification-monitor/config',payload(config,'PUT')),'Đã lưu cấu hình')} >Lưu cấu hình</button></>}
  </section>
  <section className="vm-panel"><h3><AlertTriangle size={19}/> Sự kiện gần đây</h3>{!data?.events.length?<p>Chưa có sự kiện.</p>:<div className="vm-events">{data.events.map(e=><div className="vm-event" key={e.id}><strong>{e.kind}</strong><span>{fmt(e.at)}</span><p>{e.detail}</p>{e.image&&<small>Ảnh lưu trên Backend: data/verification_monitor/{e.image}</small>}</div>)}</div>}</section>
  <AgentCloudStatus/>
  {(error||message)&&<p className={error?'vm-error':'vm-success'}>{error||message}</p>}
 </div>
}
