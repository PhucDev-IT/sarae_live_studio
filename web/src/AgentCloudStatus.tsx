import {useEffect,useState} from 'react'
import {api,errorText} from './mediaApi'
type Agent={agent_id:string;hostname:string;last_seen:string;online:boolean;seconds_ago:number;verification?:{state?:{status?:string;running?:boolean;window_found?:boolean};config?:{interval_seconds:number;threshold:number;telegram_enabled:boolean;telegram_chat_id:string};events?:{at:string;kind:string;detail:string}[]}}
type Job={command_id:string;kind:string;state:string;result:string|null;created_at:string}
const labels:Record<string,string>={idle:'Chưa bật',watching:'Đang quét, chưa thấy CAPTCHA',captcha_detected:'Phát hiện CAPTCHA',window_unavailable:'Không thấy cửa sổ TikTok',template_missing_or_invalid:'Ảnh mẫu không hợp lệ',starting:'Đang bật',stopped:'Đã dừng',error:'Có lỗi'}
const commands:Record<string,string>={monitor_start:'Bật Monitor',monitor_stop:'Dừng Monitor',monitor_config:'Lưu cấu hình',obs_status:'Kiểm tra OBS',tts_speak:'Phát giọng nói vào OBS'}
export default function AgentCloudStatus(){
 const [key,setKey]=useState(()=>sessionStorage.getItem('sarae-admin-key')||'')
 const [agents,setAgents]=useState<Agent[]>([])
 const [selected,setSelected]=useState('')
 const [period,setPeriod]=useState(2)
 const [threshold,setThreshold]=useState(.82)
 const [ttsText,setTtsText]=useState('')
 const [voice,setVoice]=useState('vi-VN-HoaiMyNeural')
 const [jobs,setJobs]=useState<Job[]>([])
 const [error,setError]=useState('')
 const [note,setNote]=useState('')
 const [busy,setBusy]=useState(false)
 const apiCall=async<T,>(url:string,options?:RequestInit):Promise<T>=>{
  const response=await fetch(api+'/api/agents'+url,{...options,headers:{'X-Sarae-Admin-Key':key,'Content-Type':'application/json',...options?.headers}})
  if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(body.detail||'HTTP '+response.status)}
  return response.json() as Promise<T>
 }
 const refresh=async()=>{
  if(!key.trim())return
  try{
   const data=await apiCall<{agents:Agent[]}>('/status')
   setAgents(data.agents);setError('');sessionStorage.setItem('sarae-admin-key',key)
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
 const selectedAgent=agents.find(a=>a.agent_id===selected)
 return <section className="vm-panel"><h3>Windows Agent · Điều khiển từ xa</h3><p>Truy cập Web bằng điện thoại hoặc máy tính khác, gửi lệnh tới Backend VPS. Windows Agent xử lý tại máy có TikTok LIVE Studio/OBS. Trạng thái được cập nhật khoảng 5 giây một lần.</p>
  <label>Admin Key<input type="password" value={key} onChange={e=>setKey(e.target.value)} placeholder="SARAE_ADMIN_KEY trên Cloud Backend"/></label>
  <div className="vm-actions"><button disabled={busy||!key} onClick={()=>void refresh()}>Làm mới Agent</button></div>
  {!!agents.length&&<><label>Máy Windows<select value={selected} onChange={e=>{setSelected(e.target.value);const c=agents.find(a=>a.agent_id===e.target.value)?.verification?.config;if(c){setPeriod(c.interval_seconds);setThreshold(c.threshold)}}}>{agents.map(a=><option key={a.agent_id} value={a.agent_id}>{a.hostname} — {a.online?'Online':'Offline'}</option>)}</select></label>
  {selectedAgent&&<div className="vm-stats"><div><small>Kết nối</small><strong>{selectedAgent.online?'Đang kết nối':'Mất kết nối'}</strong></div><div><small>Monitor</small><strong>{labels[selectedAgent.verification?.state?.status||'']||selectedAgent.verification?.state?.status||'Chưa có dữ liệu'}</strong></div><div><small>Lần cập nhật</small><strong>{new Date(selectedAgent.last_seen).toLocaleTimeString('vi-VN')}</strong></div></div>}
  <div className="vm-actions"><button disabled={busy||!selectedAgent?.online} onClick={()=>void command('monitor_start')}>Bật Monitor</button><button disabled={busy||!selectedAgent?.online} onClick={()=>void command('monitor_stop')}>Dừng Monitor</button><button disabled={busy||!selectedAgent?.online} onClick={()=>void command('obs_status')}>Kiểm tra OBS</button></div>
  <div className="vm-inputs"><label>Chu kỳ kiểm tra CAPTCHA (giây)<input type="number" min={.5} max={15} step={.5} value={period} onChange={e=>setPeriod(Number(e.target.value))}/></label><label>Ngưỡng nhận diện<input type="number" min={.65} max={.99} step={.01} value={threshold} onChange={e=>setThreshold(Number(e.target.value))}/></label></div>
  <div className="vm-actions"><button disabled={busy||!selectedAgent?.online||period<.5||period>15||threshold<.65||threshold>.99} onClick={()=>void command('monitor_config',{interval_seconds:period,threshold,telegram_enabled:selectedAgent?.verification?.config?.telegram_enabled||false,telegram_chat_id:selectedAgent?.verification?.config?.telegram_chat_id||''})}>Lưu cấu hình trên Windows Agent</button></div>
  <label>Đọc thử nội dung qua OBS (cần nguồn Sarae Voice)<textarea rows={2} value={ttsText} onChange={e=>setTtsText(e.target.value)} maxLength={450} placeholder="Dạ em chào các chị ạ..."/></label><label>Giọng đọc<select value={voice} onChange={e=>setVoice(e.target.value)}><option value="vi-VN-HoaiMyNeural">Hoài My (nữ)</option><option value="vi-VN-NamMinhNeural">Nam Minh (nam)</option></select></label>
  <div className="vm-actions"><button disabled={busy||!selectedAgent?.online||!ttsText.trim()} onClick={()=>void command('tts_speak',{text:ttsText,voice})}>Phát TTS vào OBS trên Windows</button></div>
  <h4>Lịch sử lệnh</h4><div className="vm-events">{jobs.map(j=><div className="vm-event" key={j.command_id}><strong>{commands[j.kind]||j.kind} — {j.state==='completed'?'Thành công':j.state==='failed'?'Thất bại':j.state==='pending'?'Đang chờ':'Đang xử lý'}</strong><small>{new Date(j.created_at).toLocaleString('vi-VN')}</small>{j.result&&<p>{j.result.slice(0,500)}</p>}</div>)}</div></>}
  {error&&<p className="vm-error">{error}</p>}{note&&<p className="vm-success">{note}</p>}
 </section>
}
