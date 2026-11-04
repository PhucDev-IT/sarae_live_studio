import {useEffect,useState} from 'react'
import {AlertTriangle,Camera,Play,Square,RefreshCw} from 'lucide-react'
import {agentAction,apiRequest,errorText,onAgent,type OwnerAgent} from './mediaApi'
import {WithAgent} from './agentContext'
import './verification-monitor.css'
type Event={id:number;at:string;kind:string;detail:string;image:string|null}
type Monitor={state:{running:boolean;window_found:boolean;window_title:string;window_visible:boolean;status:string;last_scan:string|null;error:string|null;events_count:number;match_score?:number;window_info?:{pid:number;hwnd:number;exe_name:string;exe_path:string;title:string;minimized:boolean}|null;monitors_detected?:number};config:{interval_seconds:number;threshold:number;template_loaded:boolean};events:Event[];agent?:{id:string;hostname:string;online:boolean;last_seen:string|null}|null;telegram_status?:string;telegram?:{enabled:boolean;chat_id:string;token_configured:boolean};template_status?:{name:string;source:string;status:string}[];builtins_ready?:boolean}
const payload=(obj:unknown,method='POST'):RequestInit=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(obj)})
/** CAPTCHA monitor running on the tab's Windows Agent (one monitor per PC). */
export default function VerificationMonitor(){
 return <WithAgent purpose="CAPTCHA Monitor">{agent=><AgentMonitor key={agent.id} agent={agent}/>}</WithAgent>
}

function AgentMonitor({agent}:{agent:OwnerAgent}){
 const on=(path:string)=>onAgent(path,agent.id)
 const [templatesManaged,setTemplatesManaged]=useState(false)
 const [data,setData]=useState<Monitor|null>(null)
 const [config,setConfig]=useState<Monitor['config']|null>(null)
 const [telegram,setTelegram]=useState<{enabled:boolean;chat_id:string;token_configured:boolean}|null>(null)
 const [botToken,setBotToken]=useState('')
 const [busy,setBusy]=useState(false)
 const [templates,setTemplates]=useState<{name:string;bytes:number}[]>([])
 const [error,setError]=useState('')
 const [message,setMessage]=useState('')
 const [testResult,setTestResult]=useState('')
 const refresh=async()=>{const v=await apiRequest<Monitor>(on('/verification-monitor/status'));setData(v);setConfig(old=>old??v.config);setTelegram(old=>old??v.telegram??null);apiRequest<{templates:{name:string;bytes:number}[]}>('/verification-monitor/templates').then(x=>{setTemplates(x.templates);setTemplatesManaged(true)}).catch(()=>setTemplatesManaged(false))}
 useEffect(()=>{void refresh().catch(()=>{});const timer=setInterval(()=>void refresh().catch(()=>{}),2500);return()=>clearInterval(timer)},[])
 const action=async(fn:()=>Promise<unknown>,success:string)=>{setBusy(true);setError('');try{await fn();setMessage(success);await refresh()}catch(e){setError(errorText(e,'Thao tác không thành công'))}finally{setBusy(false)}}
 const upload=async(file:File)=>{
  if(file.size>3_000_000){setError('Ảnh quá lớn, tối đa 3 MB');return}
  const image=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(new Error('Không đọc được file'));reader.readAsDataURL(file)})
  await action(()=>apiRequest('/verification-monitor/template',payload({png_base64:image})),'Đã lưu mẫu xác minh')
 }
 const fmt=(v:string)=>new Date(v).toLocaleString('vi-VN')
 const builtinsReady=data?.builtins_ready===true
 const saveTelegram=async()=>{if(!telegram)return;setBusy(true);setError('');try{await apiRequest(on('/verification-monitor/telegram/config'),payload({enabled:telegram.enabled,chat_id:telegram.chat_id,bot_token:botToken.trim()||null},'PUT'));setBotToken('');setMessage('Đã lưu cấu hình Telegram');const v=await apiRequest<Monitor>(on('/verification-monitor/status'));setData(v);setTelegram(v.telegram??null)}catch(e){setError(errorText(e,'Không lưu được Telegram'))}finally{setBusy(false)}}
 const sendTest=async()=>{setBusy(true);setTestResult('');try{const result=await apiRequest<{message:string}>(on('/verification-monitor/telegram/test'),{method:'POST'});setTestResult(result.message)}catch(e){setTestResult(errorText(e,'Gửi thử thất bại'))}finally{setBusy(false)}}
 return <div className="vm-layout">
  <section className="vm-panel"><h3><Camera size={19}/> CAPTCHA Monitor V1</h3><p>Theo dõi sự xuất hiện của hộp thoại xác minh trên màn hình TikTok LIVE Studio. Không tự giải hoặc điều khiển chuột. Cửa sổ phải hiện trên màn hình và không bị che khuất.</p>
   <div className="vm-stats"><div><small>Monitor</small><strong>{data?.state.running?'Đang chạy':'Đã dừng'}</strong></div><div><small>TikTok LIVE Studio</small><strong>{data?.state.window_visible?'Đang hiển thị':data?.state.window_found?'Đã thu nhỏ':'Chưa tìm thấy'}</strong></div><div><small>Loại phát hiện</small><strong>{({inactivity_warning:'Cảnh báo inactivity',captcha_challenge:'Hộp CAPTCHA',unknown_verification:'Xác minh chưa phân loại',captcha_detected:'Hộp CAPTCHA',watching:'Chưa phát hiện'} as Record<string,string>)[data?.state.status||'']||data?.state.status||'Chưa kết nối'}</strong></div><div><small>Độ khớp gần nhất</small><strong>{data?.state.match_score??'—'}</strong></div><div><small>Lần chụp gần nhất</small><strong>{data?.state.last_scan?fmt(data.state.last_scan):'Chưa chụp'}</strong></div><div><small>Số màn hình</small><strong>{data?.state.monitors_detected??'Chưa kiểm tra'}</strong></div><div><small>Built-in Templates</small><strong>{builtinsReady?'Ready':'Missing'}</strong></div></div>
   {data?.state.window_info&&<div className="vm-event"><strong>Ứng dụng đang giám sát</strong><p>{data.state.window_info.exe_name} · PID {data.state.window_info.pid} · HWND {data.state.window_info.hwnd}</p><small>Chương trình: {data.state.window_info.exe_path}</small><small>Tiêu đề: {data.state.window_info.title || '(Không có tiêu đề)'}</small><small>Số màn hình: {data.state.monitors_detected??'Đang kiểm tra'}</small></div>}
   <p className="vm-agent">{!data?<>Đang tải…</>:!data.agent?<>Chưa có Windows Agent. Kích hoạt Agent trên máy chạy TikTok LIVE Studio (trang <a href="#/agents">Windows Agents</a>) để bật giám sát.</>
    :<>Chạy trên Windows Agent <b>{data.agent.hostname}</b> · {data.agent.online?'Online':'Offline — hãy mở Sarae Agent trên máy đó'}</>}</p>
   {data?.state.error&&<p className="vm-error">{data.state.error}</p>}
   <div className="vm-actions"><button disabled={busy||!!data?.state.running||!data?.agent?.online} onClick={()=>void action(()=>agentAction(agent.id,on('/verification-monitor/start'),{method:'POST'}),'Agent đã bật giám sát')}><Play size={16}/> Bật Monitor</button><button disabled={busy||!data?.state.running} onClick={()=>void action(()=>agentAction(agent.id,on('/verification-monitor/stop'),{method:'POST'}),'Đã dừng giám sát')}><Square size={16}/> Dừng</button><button onClick={()=>void refresh()}><RefreshCw size={16}/> Làm mới</button></div>
  </section>
  <section className="vm-panel"><h3>Cấu hình phát hiện</h3>{templatesManaged&&<p>Ảnh mẫu cần chứa đủ ngữ cảnh của từng dạng giao diện. Ảnh chỉ có câu cảnh báo inactivity không phải mẫu CAPTCHA. Phát hiện dựa trên so khớp ảnh, nên có thể báo nhầm hoặc bỏ sót khi đổi tỷ lệ hiển thị.</p>}
   {!templatesManaged?null:<><label>Thêm mẫu xác minh chưa phân loại (PNG/JPG) <input type="file" accept="image/png,image/jpeg" disabled={busy} onChange={e=>{const f=e.target.files?.[0];if(f)void upload(f);e.target.value=''}}/></label><small>{templates.length} mẫu đang có. Có thể bổ sung thêm ảnh bất kỳ lúc nào; Mẫu chung được cảnh báo là chưa phân loại cho đến khi có bằng chứng đầy đủ.</small><div className="vm-events">{templates.map(t=><div className="vm-event" key={t.name}><strong>{t.name}</strong><small>{Math.round(t.bytes/1024)} KB</small>{t.name.startsWith('custom_')&&<button disabled={busy} onClick={()=>void action(()=>apiRequest('/verification-monitor/templates/'+encodeURIComponent(t.name),{method:'DELETE'}),'Đã xoá mẫu')}>Xoá mẫu</button>}</div>)}</div></>}
   {config&&<><label>Quét mỗi (giây, 180–240)<input type="number" min={180} max={240} step={1} value={config.interval_seconds} onChange={e=>setConfig({...config,interval_seconds:Number(e.target.value)})}/></label><button disabled={busy||config.interval_seconds<180||config.interval_seconds>240} onClick={()=>void action(()=>agentAction(agent.id,on('/verification-monitor/config'),payload(config,'PUT')),'Đã lưu chu kỳ trên Agent')} >Lưu chu kỳ</button><details><summary>Advanced Settings</summary><label>Ngưỡng khớp (0.65–0.99)<input type="number" min={0.65} max={0.99} step={0.01} value={config.threshold} onChange={e=>setConfig({...config,threshold:Number(e.target.value)})}/></label><div className="vm-events">{data?.template_status?.map(t=><small key={t.name}>{t.name}: {t.status} ({t.source})</small>)}</div></details></>}
   {telegram&&<><h3>Telegram cho {agent.hostname}</h3><label className="vm-check"><input type="checkbox" checked={telegram.enabled} onChange={e=>setTelegram({...telegram,enabled:e.target.checked})}/> Bật thông báo xác minh</label><label>Bot Token<input type="password" autoComplete="new-password" placeholder={telegram.token_configured?'Đã lưu — để trống nếu giữ token':'Nhập Bot Token'} value={botToken} onChange={e=>setBotToken(e.target.value)}/></label><label>Chat ID<input value={telegram.chat_id} onChange={e=>setTelegram({...telegram,chat_id:e.target.value})}/></label><small>Trạng thái: {data?.telegram_status??'Đang kiểm tra'}. Token được mã hóa và lưu tại Cloud; Agent không nhận token.</small><div className="vm-actions"><button disabled={busy} onClick={()=>void saveTelegram()}>Lưu Telegram</button><button disabled={busy||data?.telegram_status!=='Sẵn sàng'} onClick={()=>void sendTest()}>Gửi thông báo thử</button></div>{testResult&&<p>{testResult}</p>}</>}
  </section>
  <section className="vm-panel"><h3><AlertTriangle size={19}/> Sự kiện gần đây</h3>{!data?.events.length?<p>Chưa có sự kiện.</p>:<div className="vm-events">{data.events.map(e=><div className="vm-event" key={e.id}><strong>{e.kind}</strong><span>{fmt(e.at)}</span><p>{e.detail}</p>{e.image&&<small>Ảnh chụp lưu trên máy {agent.hostname}</small>}</div>)}</div>}</section>
  {(error||message)&&<p className={error?'vm-error':'vm-success'}>{error||message}</p>}
 </div>
}
