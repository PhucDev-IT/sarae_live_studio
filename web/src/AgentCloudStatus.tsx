import {useEffect,useState} from 'react'
import {api,errorText} from './mediaApi'
type Agent={agent_id:string;hostname:string;last_seen:string;online:boolean;seconds_ago:number;verification?:{state?:{status?:string;running?:boolean;window_found?:boolean};events?:{at:string;kind:string;detail:string}[]}}
export default function AgentCloudStatus(){
 const [key,setKey]=useState(()=>sessionStorage.getItem('sarae-admin-key')||'')
 const [agents,setAgents]=useState<Agent[]>([])
 const [error,setError]=useState('')
 const [busy,setBusy]=useState(false)
 const fetchStatus=async()=>{
  if(!key.trim())return
  setBusy(true)
  try{
   const res=await fetch(api+'/api/agents/status',{headers:{'X-Sarae-Admin-Key':key}})
   if(!res.ok)throw new Error('HTTP '+res.status+' — kiểm tra Admin Key hoặc Cloud API')
   const json=await res.json() as {agents:Agent[]}
   setAgents(json.agents);setError('');sessionStorage.setItem('sarae-admin-key',key)
  }catch(e){setError(errorText(e,'Không thể đọc Agent từ Backend'))}finally{setBusy(false)}
 }
 useEffect(()=>{const id=setInterval(()=>{if(key)void fetchStatus()},10000);return()=>clearInterval(id)},[key])
 return <section className="vm-panel"><h3>Windows Agent · Remote Status</h3><p>Backend có thể chạy trên VPS. Agent trên máy Windows gửi trạng thái qua HTTPS; bạn có thể kiểm tra từ trình duyệt khác hoặc điện thoại. Giai đoạn này chỉ đồng bộ trạng thái, chưa nhận lệnh điều khiển OBS từ xa.</p><label>Admin Key (chỉ lưu trong session của trình duyệt)<input type="password" value={key} onChange={e=>setKey(e.target.value)} placeholder="SARAE_ADMIN_KEY trên Backend"/></label><div className="vm-actions"><button disabled={busy||!key} onClick={()=>void fetchStatus()}>{busy?'Đang tải…':'Kiểm tra Agent'}</button></div>{error&&<p className="vm-error">{error}</p>}<div className="vm-events">{agents.map(a=><div className="vm-event" key={a.agent_id}><strong>{a.hostname} · {a.online?'Online':'Offline'}</strong><small>{a.agent_id} · cập nhật {new Date(a.last_seen).toLocaleString('vi-VN')}</small><p>CAPTCHA Monitor: {a.verification?.state?.status||'—'} · {a.verification?.state?.running?'Đang giám sát':'Chưa giám sát'}</p></div>)}</div></section>
}
