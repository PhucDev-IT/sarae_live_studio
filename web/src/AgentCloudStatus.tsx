import {useEffect,useState} from 'react'
import {api,errorText} from './mediaApi'
import {getAuthToken} from './AccountPortal'
type Agent={agent_id:string;hostname:string;last_seen:string;online:boolean;seconds_ago:number;obs?:{connected?:boolean;running?:boolean;media_state?:string;error?:string};cloud_playback?:{active:boolean;product_id:number|null;index:number;count:number};verification?:{state?:{status?:string;running?:boolean;window_found?:boolean;window_info?:{pid:number;hwnd:number;exe_name:string;exe_path:string;title:string;minimized:boolean}|null;monitors_detected?:number};config?:{interval_seconds:number;threshold:number;telegram_enabled:boolean;telegram_chat_id:string};events?:{at:string;kind:string;detail:string}[]}}
type Job={command_id:string;kind:string;state:string;result:string|null;created_at:string}
const labels:Record<string,string>={idle:'Chưa bật',watching:'Đang quét, chưa thấy CAPTCHA',captcha_detected:'Phát hiện CAPTCHA',window_unavailable:'Không thấy cửa sổ TikTok',template_missing_or_invalid:'Ảnh mẫu không hợp lệ',starting:'Đang bật',stopped:'Đã dừng',error:'Có lỗi'}
const commands:Record<string,string>={monitor_start:'Bật Monitor',monitor_stop:'Dừng Monitor',monitor_config:'Lưu cấu hình',obs_status:'Kiểm tra OBS',obs_pause:'Tạm dừng video',obs_resume:'Tiếp tục video',obs_next:'Video tiếp',obs_previous:'Video trước',obs_stop:'Dừng video',tts_speak:'Phát giọng nói vào OBS',local_media_play:'Phát video local',local_media_stop:'Dừng video local'}
export default function AgentCloudStatus(){
 const [key,setKey]=useState(getAuthToken())
 const [agents,setAgents]=useState<Agent[]>([])
 const [selected,setSelected]=useState('')
 const [period,setPeriod]=useState(2)
 const [threshold,setThreshold]=useState(.82)
 const [ttsText,setTtsText]=useState('')
 const [mediaItems,setMediaItems]=useState<{media_id:string;filename:string;size_bytes:number;available:number}[]>([])
 const [voice,setVoice]=useState('vi-VN-HoaiMyNeural')
 const [jobs,setJobs]=useState<Job[]>([])
 const [error,setError]=useState('')
 const [note,setNote]=useState('')
 const [busy,setBusy]=useState(false)
 const apiCall=async<T,>(url:string,options?:RequestInit):Promise<T>=>{
  const response=await fetch(api+'/api/agents'+url,{...options,headers:{'Authorization':'Bearer '+key,'Content-Type':'application/json',...options?.headers}})
  if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(body.detail||'HTTP '+response.status)}
  return response.json() as Promise<T>
 }
 const refresh=async()=>{
  if(!key.trim())return
  try{
   const data=await apiCall<{agents:Agent[]}>('/status')
   setAgents(data.agents);setError('');
   const current=selected||data.agents[0]?.agent_id||''
   if(!selected&&current){setSelected(current);const c=data.agents.find(a=>a.agent_id===current)?.verification?.config;if(c){setPeriod(c.interval_seconds);setThreshold(c.threshold)}}
   if(current){const j=await apiCall<{commands:Job[]}>('/commands?agent_id='+encodeURIComponent(current));setJobs(j.commands)}
  }catch(e){setError(errorText(e,'Không tải được trạng thái Agent'))}
 }
 useEffect(()=>{if(!key)return;void refresh();const id=setInterval(()=>void refresh(),5000);return()=>clearInterval(id)},[key,selected])
 const command=async(kind:string,payload:Record<string,unknown>={})=>{
  if(!selected)return
  setBusy(true);setError('');setNote('')
  try{
   const r=await apiCall<{command_id:string;state:string}>('/commands',{method:'POST',body:JSON.stringify({agent_id:selected,kind,payload})})
   setNote('Đã gửi lệnh '+(commands[kind]||kind)+'; đang chờ Agent xử lý ('+r.command_id.slice(0,8)+')')
   await refresh()
  }catch(e){setError(errorText(e,'Gửi lệnh thất bại'))}finally{setBusy(false)}
 }
 const performMedia=async()=>{
  try{
   const res=await fetch(api+'/api/agent-library/'+encodeURIComponent(selected),{
    headers:{Authorization:'Bearer '+key}})
   const data=await res.json()
   if(!res.ok)throw new Error(data.detail||'Không tải được thư viện')
   setMediaItems(data.videos||[])
   setError('')
  }catch(e){setError(errorText(e,'Không lấy được danh mục video'))}
 }
 const selectedAgent=agents.find(a=>a.agent_id===selected)
 return <section className="vm-panel"><h3>Windows Agent · Điều khiển từ xa</h3><p>Truy cập Web bằng điện thoại hoặc máy tính khác, gửi lệnh tới Backend VPS. Windows Agent xử lý tại máy có TikTok LIVE Studio/OBS. Trạng thái được cập nhật khoảng 5 giây một lần.</p>
  <label>Phiên đăng nhập từ trang Tài khoản & Agent<input type="password" value={key} onChange={e=>setKey(e.target.value)} placeholder="Đăng nhập để lấy access token"/></label>
  <div className="vm-actions"><button disabled={busy||!key} onClick={()=>void refresh()}>Làm mới Agent</button></div>
  {!!agents.length&&<><label>Máy Windows<select value={selected} onChange={e=>{setSelected(e.target.value);const c=agents.find(a=>a.agent_id===e.target.value)?.verification?.config;if(c){setPeriod(c.interval_seconds);setThreshold(c.threshold)}}}>{agents.map(a=><option key={a.agent_id} value={a.agent_id}>{a.hostname} — {a.online?'Online':'Offline'}</option>)}</select></label>
  {selectedAgent&&<div className="vm-stats"><div><small>Kết nối</small><strong>{selectedAgent.online?'Đang kết nối':'Mất kết nối'}</strong></div><div><small>Monitor</small><strong>{labels[selectedAgent.verification?.state?.status||'']||selectedAgent.verification?.state?.status||'Chưa có dữ liệu'}</strong></div><div><small>OBS trên Agent</small><strong>{selectedAgent.obs?.connected? "Đã kết nối" : "Chưa kết nối"}</strong></div><div><small>Lần cập nhật</small><strong>{new Date(selectedAgent.last_seen).toLocaleTimeString('vi-VN')}</strong></div></div>}
  {selectedAgent?.verification?.state?.window_info&&<div className="vm-event"><strong>Ứng dụng giám sát trên Windows</strong><p>{selectedAgent.verification.state.window_info.exe_name} · PID {selectedAgent.verification.state.window_info.pid} · HWND {selectedAgent.verification.state.window_info.hwnd}</p><small>{selectedAgent.verification.state.window_info.exe_path}</small><small>Số màn hình: {selectedAgent.verification.state.monitors_detected??'—'}</small></div>}
  <div className="vm-actions"><button disabled={busy||!selectedAgent?.online} onClick={()=>void command('monitor_start')}>Bật Monitor</button><button disabled={busy||!selectedAgent?.online} onClick={()=>void command('monitor_stop')}>Dừng Monitor</button><button disabled={busy||!selectedAgent?.online} onClick={()=>void command('obs_status')}>Kiểm tra OBS</button></div>
  <div className="vm-actions">{([['obs_previous','Video trước'],['obs_pause','Tạm dừng'],['obs_resume','Tiếp tục'],['obs_next','Video tiếp'],['obs_stop','Dừng phát']] as const).map(([kind,label])=><button key={kind} disabled={busy||!selectedAgent?.online} onClick={()=>void command(kind)}>{label}</button>)}</div>
  <div className="vm-inputs"><label>Chu kỳ kiểm tra CAPTCHA (giây)<input type="number" min={.5} max={15} step={.5} value={period} onChange={e=>setPeriod(Number(e.target.value))}/></label><label>Ngưỡng nhận diện<input type="number" min={.65} max={.99} step={.01} value={threshold} onChange={e=>setThreshold(Number(e.target.value))}/></label></div>
  <div className="vm-actions"><button disabled={busy||!selectedAgent?.online||period<.5||period>15||threshold<.65||threshold>.99} onClick={()=>void command('monitor_config',{interval_seconds:period,threshold,telegram_enabled:selectedAgent?.verification?.config?.telegram_enabled||false,telegram_chat_id:selectedAgent?.verification?.config?.telegram_chat_id||''})}>Lưu cấu hình trên Windows Agent</button></div>
  <h4>Thư viện video của Agent</h4>
  <p>Video vẫn ở máy Windows. Cloud chỉ giữ tên, kích thước và định danh video được Agent đồng bộ.</p>
  <button disabled={busy||!selected} onClick={()=>void performMedia()}>Tải danh mục video</button>
  <div className="vm-events">{mediaItems.filter(x=>x.available).slice(0,200).map(v=><div className="vm-event" key={v.media_id}>
   <strong>{v.filename}</strong><small>{(v.size_bytes/1024/1024).toFixed(1)} MB</small>
   <button disabled={busy||!selectedAgent?.online} onClick={()=>void command('local_media_play',{media_id:v.media_id})}>Phát trong OBS</button>
  </div>)}</div>
  <button disabled={busy||!selectedAgent?.online} onClick={()=>void command('local_media_stop')}>Dừng video local</button>
  <label>Đọc thử nội dung qua OBS (cần nguồn Sarae Voice)<textarea rows={2} value={ttsText} onChange={e=>setTtsText(e.target.value)} maxLength={450} placeholder="Dạ em chào các chị ạ..."/></label><label>Giọng đọc<select value={voice} onChange={e=>setVoice(e.target.value)}><option value="vi-VN-HoaiMyNeural">Hoài My (nữ)</option><option value="vi-VN-NamMinhNeural">Nam Minh (nam)</option></select></label>
  <div className="vm-actions"><button disabled={busy||!selectedAgent?.online||!ttsText.trim()} onClick={()=>void command('tts_speak',{text:ttsText,voice})}>Phát TTS vào OBS trên Windows</button></div>
  <h4>Lịch sử lệnh</h4><div className="vm-events">{jobs.map(j=><div className="vm-event" key={j.command_id}><strong>{commands[j.kind]||j.kind} — {j.state==='completed'?'Thành công':j.state==='failed'?'Thất bại':j.state==='pending'?'Đang chờ':'Đang xử lý'}</strong><small>{new Date(j.created_at).toLocaleString('vi-VN')}</small>{j.result&&<p>{j.result.slice(0,500)}</p>}</div>)}</div></>}
  {error&&<p className="vm-error">{error}</p>}{note&&<p className="vm-success">{note}</p>}
 </section>
}
