import {useEffect,useState} from 'react'
import {Bot,Save,AlertCircle,KeyRound,MessageSquareText,Trash2} from 'lucide-react'
import {apiRequest,errorText} from './mediaApi'
import './ai-settings.css'
import VoiceSettings from './VoiceSettings'
type Settings={provider:'gemini'|'openai';model:string;enabled:boolean;max_output_tokens:number;max_requests_per_session:number;has_api_key:boolean;key_source?:'owner'|'platform'|null}
/** What the server tells about a stored key: never the key itself, only whether it exists and its last 4 characters. */
type Credential={provider:'gemini'|'openai';configured:boolean;last4:string|null;updated_at:string|null}
type Usage={requests:number;input_tokens:number;output_tokens:number}
const defaults:Settings={provider:'gemini',model:'gemini-3.5-flash-lite',enabled:false,max_output_tokens:220,max_requests_per_session:100,has_api_key:false,key_source:null}
const PROVIDER_NAME:Record<Credential['provider'],string>={gemini:'Google Gemini',openai:'OpenAI'}
const JSONbody=(value:unknown,method='POST'):RequestInit=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(value)})
export default function AISettings(){
 const [settings,setSettings]=useState<Settings>(defaults)
 const [usage,setUsage]=useState<Usage|null>(null)
 const [loading,setLoading]=useState(false)
 const [notice,setNotice]=useState('')
 const [credentials,setCredentials]=useState<Credential[]>([])
 const [keyInput,setKeyInput]=useState('')
 const [error,setError]=useState('')
 const reload=async()=>{const [s,u,c]=await Promise.all([apiRequest<Settings>('/ai/config'),apiRequest<Usage>('/ai/usage'),apiRequest<{credentials:Credential[]}>('/ai/credentials')]);setSettings(s);setUsage(u);setCredentials(c.credentials)}
 useEffect(()=>{void reload().catch(e=>setError(errorText(e,'Không tải được cấu hình AI')))},[])
 const exec=async(job:()=>Promise<void>)=>{setLoading(true);setError('');setNotice('');try{await job()}catch(e){setError(errorText(e,'Thao tác lỗi'))}finally{setLoading(false)}}
 const update=(v:Partial<Settings>)=>setSettings(old=>({...old,...v}))
 return <div className="ais-layout">
 <VoiceSettings/>
 <section className="ais-panel">
 <h3><Bot size={19}/> AI Provider Configuration</h3>
 <p>Chọn provider/model theo nhu cầu. Gemini Free Tier chỉ áp dụng nếu dự án và model của bạn đủ điều kiện; phần mềm không thể tự xác định hạn mức tài khoản.</p>
 <div className="ais-fields">
 <label>Provider<select value={settings.provider} onChange={e=>{const provider=e.target.value as Settings['provider'];update({provider,model:provider==='gemini'?'gemini-3.5-flash-lite':'gpt-5-nano',has_api_key:false})}}><option value="gemini">Google Gemini</option><option value="openai">OpenAI</option></select></label>
 <label>Model ID<input value={settings.model} onChange={e=>update({model:e.target.value})} placeholder="Nhập ID model của provider"/></label>
 <label>Giới hạn output tokens<input type="number" min={60} max={1000} value={settings.max_output_tokens} onChange={e=>update({max_output_tokens:Number(e.target.value)})}/></label>
 <label>Giới hạn lượt gọi mỗi lần chạy Backend<input type="number" min={1} max={10000} value={settings.max_requests_per_session} onChange={e=>update({max_requests_per_session:Number(e.target.value)})}/></label>
 </div>
 <label className="ais-toggle"><input type="checkbox" checked={settings.enabled} onChange={e=>update({enabled:e.target.checked})}/> Bật AI (dùng cho trả lời LIVE và trang Hỏi đáp AI)</label>
 <p className="ais-key">API key đang dùng: <strong>{settings.key_source==='owner'?'Key của tài khoản bạn':settings.key_source==='platform'?'Key chung của nền tảng':'Chưa có'}</strong>{settings.key_source==='platform'?' · nhập key riêng ở khung “API key” để dùng hạn mức của bạn.':''}</p>
 <button disabled={loading} onClick={()=>void exec(async()=>{const s=await apiRequest<Settings>('/ai/config',JSONbody({provider:settings.provider,model:settings.model,enabled:settings.enabled,max_output_tokens:settings.max_output_tokens,max_requests_per_session:settings.max_requests_per_session},'PUT'));setSettings(s);setNotice('Đã lưu cấu hình')})}><Save size={16}/> Lưu cấu hình</button>
 </section>
 <section className="ais-panel">
 <h3><KeyRound size={19}/> API key</h3>
 <p>Key được mã hóa và lưu riêng cho tài khoản của bạn. Sau khi lưu, giao diện chỉ hiện 4 ký tự cuối; không ai xem lại được key đầy đủ (kể cả quản trị nền tảng).</p>
 <div className="ais-creds">{credentials.map(c=><div key={c.provider} className={'ais-cred'+(c.provider===settings.provider?' is-current':'')}>
  <span><strong>{PROVIDER_NAME[c.provider]}</strong><small>{c.configured?`Đã lưu · ••••${c.last4}`:'Chưa có key'}{c.provider===settings.provider?' · đang dùng':''}</small></span>
  {c.configured&&<button type="button" title="Xóa key" aria-label={'Xóa key '+PROVIDER_NAME[c.provider]} disabled={loading} onClick={()=>{if(window.confirm(`Xóa API key ${PROVIDER_NAME[c.provider]}?`))void exec(async()=>{const r=await apiRequest<{credentials:Credential[]}>('/ai/credentials/'+c.provider,{method:'DELETE'});setCredentials(r.credentials);await reload();setNotice('Đã xóa key')})}}><Trash2 size={15}/></button>}
 </div>)}</div>
 <form className="ais-key-form" onSubmit={e=>{e.preventDefault();const key=keyInput.trim();if(key.length<8)return;void exec(async()=>{const r=await apiRequest<{credentials:Credential[]}>('/ai/credentials',JSONbody({provider:settings.provider,api_key:key},'PUT'));setKeyInput('');setCredentials(r.credentials);await reload();setNotice('Đã lưu key '+PROVIDER_NAME[settings.provider])})}}>
  <label>Key {PROVIDER_NAME[settings.provider]}<input type="password" autoComplete="off" spellCheck={false} value={keyInput} onChange={e=>setKeyInput(e.target.value)} placeholder={settings.provider==='gemini'?'AIza…':'sk-…'}/></label>
  <button disabled={loading||keyInput.trim().length<8}><Save size={16}/> Lưu key</button>
 </form>
 </section>
 <section className="ais-panel">
 <h3><MessageSquareText size={19}/> Thử AI trả lời</h3>
 <p>Thử bình luận theo từng sản phẩm, xem câu comment và nghe giọng đọc ở trang <strong>Hỏi đáp AI</strong> (không cần LIVE).</p>
 <button type="button" onClick={()=>{window.location.hash='/ai-answers'}}><MessageSquareText size={16}/> Mở Hỏi đáp AI</button>
 {usage&&<div className="ais-usage">Đã gọi: {usage.requests} · Input: {usage.input_tokens} tokens · Output: {usage.output_tokens} tokens</div>}
 </section>
 {(error||notice)&&<div className={'ais-alert '+(error?'is-error':'')}><AlertCircle size={17}/>{error||notice}</div>}
 </div>
}
