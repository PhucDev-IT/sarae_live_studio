import {useEffect,useState} from 'react'
import {Bot,Play,Save,AlertCircle} from 'lucide-react'
import {apiRequest,errorText} from './mediaApi'
import './ai-settings.css'
type Settings={provider:'gemini'|'openai';model:string;enabled:boolean;max_output_tokens:number;max_requests_per_session:number;has_api_key:boolean}
type Usage={requests:number;input_tokens:number;output_tokens:number}
type Answer={provider:string;model:string;result:{intent:string;confidence:number;reply:string;needs_review:boolean;reason:string};usage:{input_tokens:number;output_tokens:number;latency_ms:number};action_executed:boolean}
const defaults:Settings={provider:'gemini',model:'gemini-3.5-flash-lite',enabled:false,max_output_tokens:220,max_requests_per_session:100,has_api_key:false}
const JSONbody=(value:unknown,method='POST'):RequestInit=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(value)})
export default function AISettings(){
 const [settings,setSettings]=useState<Settings>(defaults)
 const [usage,setUsage]=useState<Usage|null>(null)
 const [comment,setComment]=useState('Shop ơi áo này mặc có nóng không?')
 const [product,setProduct]=useState('')
 const [details,setDetails]=useState('')
 const [answer,setAnswer]=useState<Answer|null>(null)
 const [loading,setLoading]=useState(false)
 const [notice,setNotice]=useState('')
 const [error,setError]=useState('')
 const reload=async()=>{const [s,u]=await Promise.all([apiRequest<Settings>('/ai/config'),apiRequest<Usage>('/ai/usage')]);setSettings(s);setUsage(u)}
 useEffect(()=>{void reload().catch(e=>setError(errorText(e,'Không tải được cấu hình AI')))},[])
 const exec=async(job:()=>Promise<void>)=>{setLoading(true);setError('');setNotice('');try{await job()}catch(e){setError(errorText(e,'Thao tác lỗi'))}finally{setLoading(false)}}
 const update=(v:Partial<Settings>)=>setSettings(old=>({...old,...v}))
 return <div className="ais-layout">
 <section className="ais-panel">
 <h3><Bot size={19}/> AI Provider Configuration</h3>
 <p>Chọn provider/model theo nhu cầu. Gemini Free Tier chỉ áp dụng nếu dự án và model của bạn đủ điều kiện; phần mềm không thể tự xác định hạn mức tài khoản.</p>
 <div className="ais-fields">
 <label>Provider<select value={settings.provider} onChange={e=>{const provider=e.target.value as Settings['provider'];update({provider,model:provider==='gemini'?'gemini-3.5-flash-lite':'gpt-5-nano',has_api_key:false})}}><option value="gemini">Google Gemini</option><option value="openai">OpenAI</option></select></label>
 <label>Model ID<input value={settings.model} onChange={e=>update({model:e.target.value})} placeholder="Nhập ID model của provider"/></label>
 <label>Giới hạn output tokens<input type="number" min={60} max={1000} value={settings.max_output_tokens} onChange={e=>update({max_output_tokens:Number(e.target.value)})}/></label>
 <label>Giới hạn lượt gọi mỗi lần chạy Backend<input type="number" min={1} max={10000} value={settings.max_requests_per_session} onChange={e=>update({max_requests_per_session:Number(e.target.value)})}/></label>
 </div>
 <label className="ais-toggle"><input type="checkbox" checked={settings.enabled} onChange={e=>update({enabled:e.target.checked})}/> Bật AI (chỉ xử lý khi bấm Test AI; chưa tự động đọc toàn bộ comment)</label>
 <p className="ais-key">API key: <strong>{settings.has_api_key?'Đã có trong Backend':'Chưa phát hiện'}</strong>. Thiết lập <code>{settings.provider==='gemini'?'GEMINI_API_KEY':'OPENAI_API_KEY'}</code> trong môi trường Backend trước khi khởi động. Key không nhập hoặc trả về từ giao diện.</p>
 <button disabled={loading} onClick={()=>void exec(async()=>{const s=await apiRequest<Settings>('/ai/config',JSONbody({provider:settings.provider,model:settings.model,enabled:settings.enabled,max_output_tokens:settings.max_output_tokens,max_requests_per_session:settings.max_requests_per_session},'PUT'));setSettings(s);setNotice('Đã lưu cấu hình')})}><Save size={16}/> Lưu cấu hình</button>
 </section>
 <section className="ais-panel">
 <h3>AI Playground</h3><p>Thử từng bình luận thực tế. Không gửi trả lời lên TikTok hoặc điều khiển OBS.</p>
 <label>Bình luận<textarea value={comment} onChange={e=>setComment(e.target.value)} rows={3}/></label>
 <label>Tên sản phẩm (không bắt buộc)<input value={product} onChange={e=>setProduct(e.target.value)} placeholder="Áo trễ vai trắng"/></label>
 <label>Thông tin sản phẩm (không bắt buộc)<textarea value={details} onChange={e=>setDetails(e.target.value)} rows={2} placeholder="Chất liệu, giá, size thực tế..."/></label>
 <button disabled={loading||!comment.trim()} onClick={()=>void exec(async()=>{const result=await apiRequest<Answer>('/ai/analyze',JSONbody({comment,username:'playground',product_name:product||null,product_details:details||null}));setAnswer(result);setNotice('AI đã phân tích xong');await reload()})}><Play size={16}/> {loading?'Đang xử lý…':'Test AI'}</button>
 {answer&&<div className="ais-answer"><strong>{answer.result.intent} · {Math.round(answer.result.confidence*100)}%</strong><p>{answer.result.reply}</p><small>{answer.result.reason} · {answer.result.needs_review?'Cần xác nhận':'Không yêu cầu xác nhận'}</small><small>{answer.usage.input_tokens} input / {answer.usage.output_tokens} output tokens · {answer.usage.latency_ms}ms · Không thực hiện hành động LIVE</small></div>}
 {usage&&<div className="ais-usage">Đã gọi: {usage.requests} · Input: {usage.input_tokens} tokens · Output: {usage.output_tokens} tokens</div>}
 </section>
 {(error||notice)&&<div className={'ais-alert '+(error?'is-error':'')}><AlertCircle size={17}/>{error||notice}</div>}
 </div>
}
