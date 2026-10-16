import {useEffect,useRef,useState} from 'react'
import {apiRequest,errorText} from './mediaApi'
import {Volume2,VolumeX} from 'lucide-react'
type Options={enabled:boolean;window_seconds:number;max_comments:number}
type Batch={batch_id:number;product_name:string;product_id:number;comments:{id:number;username:string;comment:string}[];questions:{id:number;text:string;users:string[]}[];answer:{result:{reply:string;groups?:{intent:string;question_ids:number[];reply:string}[]};usage:{input_tokens:number;output_tokens:number}}|null;status:string;cache_hit:boolean;error?:string}
type Status={stats:{received:number;processed:number;cached:number;batches:number;skipped:number};last_id:number;pending:number;batches:Batch[]}
export default function LiveAIBatches(){
 const [options,setOptions]=useState<Options>({enabled:false,window_seconds:2.5,max_comments:8})
 const [status,setStatus]=useState<Status|null>(null)
 const [batches,setBatches]=useState<Batch[]>([])
 const [error,setError]=useState('')
 const [busy,setBusy]=useState(false)
 const [speaking,setSpeaking]=useState(false)
 const [obsSpeaking,setObsSpeaking]=useState(false)
 const last=useRef(0)
 useEffect(()=>{
  let live=true
  void apiRequest<Options>('/ai/live/config').then(v=>{if(live)setOptions(v)}).catch(e=>{if(live)setError(errorText(e,'Không tải được cấu hình batch'))})
  const poll=async()=>{try{const d=await apiRequest<Status>(`/ai/live/status?after=${last.current}`);if(!live)return;setStatus(d);if(d.last_id<last.current){setBatches(d.batches)}else if(d.batches.length){setBatches(old=>[...old,...d.batches.filter(x=>!old.some(y=>y.batch_id===x.batch_id))].slice(-50))}last.current=d.last_id}catch(e){if(live)setError(errorText(e,'Không tải được batch'))}}
  void poll();const id=window.setInterval(()=>void poll(),1700)
  return()=>{live=false;window.clearInterval(id)}
 },[])
 const save=async(next:Options)=>{setBusy(true);setError('');try{const v=await apiRequest<Options>('/ai/live/config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(next)});setOptions(v)}catch(e){setError(errorText(e,'Không lưu được cấu hình'))}finally{setBusy(false)}}
 const speakObs=async(value:string)=>{if(!window.confirm("Phát câu trả lời này vào nguồn Sarae Voice trong OBS? Nếu đang LIVE, người xem có thể nghe thấy."))return;setObsSpeaking(true);setError("");try{await apiRequest("/ai/voice/speak",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text:value})})}catch(e){setError(errorText(e,"Phát TTS lên OBS thất bại"))}finally{setObsSpeaking(false)}}
 const speak=(value:string)=>{if(!('speechSynthesis' in window)){setError('Trình duyệt này chưa hỗ trợ Speech Synthesis');return}window.speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(value);u.lang='vi-VN';u.rate=1;u.onstart=()=>setSpeaking(true);u.onend=()=>setSpeaking(false);u.onerror=()=>setSpeaking(false);window.speechSynthesis.speak(u)}
 return <section className="tc-panel">
  <div className="tc-chat-title"><h3>AI LIVE Batch & Voice Preview</h3><span>{status?.pending??0} bình luận đang chờ</span></div>
  <p className="tc-note">Chỉ xử lý khi AI Provider và Batch đều bật. Ngữ cảnh lấy từ sản phẩm đang phát trên OBS. Có thể nghe thử trên máy hoặc bấm phát thủ công vào nguồn âm thanh riêng Sarae Voice trong OBS. Không tự động đọc.</p>
  <div className="tc-meta" style={{marginTop:10}}>
   <label style={{display:'flex',alignItems:'center',gap:8}}><input type="checkbox" disabled={busy} checked={options.enabled} onChange={e=>void save({...options,enabled:e.target.checked})}/> Bật xử lý AI realtime</label>
   <label>Cửa sổ gom (giây) <input style={{width:62}} type="number" min={1} max={10} step={0.5} value={options.window_seconds} onChange={e=>setOptions(x=>({...x,window_seconds:Number(e.target.value)}))} onBlur={()=>void save(options)}/></label>
   <label>Tối đa mỗi batch <input style={{width:62}} type="number" min={1} max={30} value={options.max_comments} onChange={e=>setOptions(x=>({...x,max_comments:Number(e.target.value)}))} onBlur={()=>void save(options)}/></label>
  </div>
  <div className="tc-meta"><span>Đã nhận: <b>{status?.stats.received??0}</b></span><span>Đã xử lý: <b>{status?.stats.processed??0}</b></span><span>Batch: <b>{status?.stats.batches??0}</b></span><span>Cache hit: <b>{status?.stats.cached??0}</b></span></div>
  {error&&<p className="tc-error">{error}</p>}
  <div style={{display:'grid',gap:12,marginTop:14}}>
   {batches.length===0?<p className="tc-note">Chưa có batch. Bắt đầu phiên phát sản phẩm và kết nối TikTok Chat, sau đó bật AI realtime.</p>:batches.slice().reverse().map(b=><div key={b.batch_id} style={{border:'1px solid var(--border)',borderRadius:12,padding:14,display:'grid',gap:8}}>
    <div style={{display:'flex',justifyContent:'space-between',gap:8}}><strong>Batch #{b.batch_id} · {b.product_name}</strong><small>{b.status}{b.cache_hit?' · CACHE HIT':''}</small></div>
    <div className="tc-note">{b.comments.length} bình luận → {b.questions.length} nhóm câu giống nhau</div>
    {b.questions.map(q=><div key={q.id} style={{fontSize:13}}><b>Nhóm #{q.id}:</b> {q.text} <small>({q.users.join(', ')})</small></div>)}
    {b.answer?.result.groups?.map((g,i)=><div key={i} style={{fontSize:13}}><b>{g.intent}:</b> {g.reply}</div>)}
    {b.answer?.result.reply&&<div style={{padding:10,borderRadius:8,background:'var(--soft)'}}><strong>Đề xuất trả lời: </strong>{b.answer.result.reply}</div>}
    {b.answer?.result.reply&&<><button type="button" onClick={()=>speak(b.answer!.result.reply)} style={{justifySelf:'start',display:'flex',alignItems:'center',gap:7}}><Volume2 size={16}/> Nghe thử trên máy</button> <button type="button" disabled={obsSpeaking} onClick={()=>void speakObs(b.answer!.result.reply)} style={{justifySelf:"start"}}>Phát vào OBS (xác nhận)</button></>}
    {b.answer?.usage&&<small>{b.answer.usage.input_tokens} input · {b.answer.usage.output_tokens} output tokens</small>}
    {b.error&&<p className="tc-error">{b.error}</p>}
   </div>)}
  </div>
  {speaking&&<button type="button" onClick={()=>{window.speechSynthesis.cancel();setSpeaking(false)}}><VolumeX size={16}/> Dừng đọc</button>}
 </section>
}
