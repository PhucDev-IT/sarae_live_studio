import {useEffect,useRef,useState} from 'react'
import {apiRequest,errorText,onAgent,type OwnerAgent} from './mediaApi'
import {Copy,Mic,OctagonX,Volume2,X} from 'lucide-react'
type Options={enabled:boolean;window_seconds:number;max_comments:number}
type ReplyOptions={mode:'manual'|'semi_auto'|'auto';channel:'voice'|'comment'|'both';voice:string;rate:number;veto_seconds:number;max_age_seconds:number;topic_cooldown_seconds:number;min_confidence:number;call_names:boolean;duck_video:boolean;persona:string;auto_prepare:boolean}
type Reply={id:number;batch_id:number;product_name:string;question:string;comments:{id:number;username:string;nickname:string;comment:string}[];intent:string;slots:Record<string,string|number>;say:string;text:string;confidence:number;source:'rule'|'bank'|'cache'|'ai';review_reasons:string[];route:string|null;voice:string|null;comment:string|null;note:string;spoken_text:string;comment_text?:string;speak_at?:number;dismissed?:boolean;asked_at:string}
type Batch={batch_id:number;product_name:string;comments:number;questions:number;skipped:{username:string;comment:string;reason:string}[];sources:{rule:number;bank?:number;cache?:number;ai:number};usage:{input_tokens:number;output_tokens:number;latency_ms:number}[];error:string|null}
type AgentVoice={online:boolean;queue:number;speaking:string|null;ducked:boolean;stats:Record<string,number>;last:{status:string;code?:string;message?:string}|null;prepare_pending:number}
type Prepared={product_id:number;product_name:string;state:'none'|'running'|'ready'|'error';count:number;error?:string}
type Status={stats:Record<string,number>;pending:number;voice_queue:number;unanswered:number;batches:Batch[];replies:Reply[];agent_voice?:AgentVoice|null;prepared?:Prepared|null}
type Answer={id:number;question:string;intent:string;say:string[];text:string[];source:'ai'|'pregen'|'operator';approved:boolean;uses:number;last_used_at:string|null}
type Answers={product_id:number|null;product_name:string;answers:Answer[]}
const DEFAULT_REPLY:ReplyOptions={mode:'manual',channel:'voice',voice:'vi-VN-HoaiMyNeural',rate:5,veto_seconds:4,max_age_seconds:25,topic_cooldown_seconds:40,min_confidence:0.75,call_names:true,duck_video:true,persona:'',auto_prepare:true}
const ANSWER_SOURCE:Record<Answer['source'],string>={ai:'AI',pregen:'Chuẩn bị sẵn',operator:'Bạn sửa'}
const VOICE_LABEL:Record<string,string>={review:'Cần duyệt',ready:'Chờ bấm phát',scheduled:'Sắp nói',queued:'Chờ lượt nói',sent:'Đã gửi giọng',skipped:'Không nói'}
const COMMENT_LABEL:Record<string,string>={ready:'Comment chờ gửi',done:'Đã comment',dismissed:'Bỏ qua'}
const SOURCE_LABEL:Record<Reply['source'],string>={rule:'Luật · 0 token',bank:'Kho câu · 0 token',cache:'Cache · 0 token',ai:'AI'}
const chip=(tone:string)=>({fontSize:11,fontWeight:700,padding:'3px 8px',borderRadius:20,background:tone==='warn'?'#fff1d6':tone==='ok'?'#d8f8e5':tone==='bad'?'#ffe4e4':'var(--soft)',color:tone==='warn'?'#8a5a00':tone==='ok'?'#187347':tone==='bad'?'#bc3535':'var(--ink)'})
/** AI replies of one LIVE desk (the Agent): one answer per question, spoken on OBS or offered as a comment. */
export default function LiveAIBatches({agent}:{agent:OwnerAgent}){
 const [options,setOptions]=useState<Options>({enabled:false,window_seconds:2.5,max_comments:8})
 const [reply,setReply]=useState<ReplyOptions>(DEFAULT_REPLY)
 const [status,setStatus]=useState<Status|null>(null)
 const [batches,setBatches]=useState<Batch[]>([])
 const [error,setError]=useState('')
 const [busy,setBusy]=useState(false)
 const [editing,setEditing]=useState<Record<number,string>>({})
 const [now,setNow]=useState(()=>Date.now())
 const last=useRef(0)
 const [bankOpen,setBankOpen]=useState(false)
 const [bank,setBank]=useState<Answers|null>(null)
 const [bankBusy,setBankBusy]=useState(false)
 const [bankTick,setBankTick]=useState(0)
 const loadBank=()=>setBankTick(t=>t+1)
 useEffect(()=>{
  if(!bankOpen)return
  let live=true
  const load=async()=>{try{const d=await apiRequest<Answers>(onAgent('/ai/live/answers',agent.id));if(live)setBank(d)}catch(e){if(live)setError(errorText(e,'Không tải được kho câu trả lời'))}}
  void load();const id=window.setInterval(()=>void load(),10000)
  return()=>{live=false;window.clearInterval(id)}
 },[bankOpen,bankTick,agent.id])
 const removeAnswer=async(a:Answer)=>{if(!window.confirm('Xoá câu trả lời này khỏi kho?'))return;setError('');try{await apiRequest(onAgent(`/ai/live/answers/${a.id}`,agent.id),{method:'DELETE'});setBank(b=>b&&{...b,answers:b.answers.filter(x=>x.id!==a.id)})}catch(e){setError(errorText(e,'Không xoá được câu trả lời'))}}
 const prepare=async()=>{setBankBusy(true);setError('');try{const p=await apiRequest<Prepared>(onAgent('/ai/live/prepare',agent.id),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({force:true})});setStatus(s=>s&&{...s,prepared:p});loadBank()}catch(e){setError(errorText(e,'Không chuẩn bị được câu trả lời'))}finally{setBankBusy(false)}}
 useEffect(()=>{
  let live=true
  void apiRequest<Options>('/ai/live/config').then(v=>{if(live)setOptions(v)}).catch(e=>{if(live)setError(errorText(e,'Không tải được cấu hình batch'))})
  void apiRequest<ReplyOptions>('/ai/live/reply-config').then(v=>{if(live)setReply(v)}).catch(e=>{if(live)setError(errorText(e,'Không tải được cấu hình trả lời'))})
  const poll=async()=>{try{const d=await apiRequest<Status>(onAgent(`/ai/live/status?after=${last.current}`,agent.id));if(!live)return;setStatus(d);if(d.batches.length)setBatches(old=>[...old,...d.batches.filter(x=>!old.some(y=>y.batch_id===x.batch_id))].slice(-20));if(d.batches.length)last.current=d.batches[d.batches.length-1].batch_id}catch(e){if(live)setError(errorText(e,'Không tải được trạng thái AI'))}}
  void poll();const id=window.setInterval(()=>void poll(),1200);const tick=window.setInterval(()=>setNow(Date.now()),500)
  return()=>{live=false;window.clearInterval(id);window.clearInterval(tick)}
 },[agent.id])
 const save=async(next:Options)=>{setBusy(true);setError('');try{setOptions(await apiRequest<Options>('/ai/live/config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(next)}))}catch(e){setError(errorText(e,'Không lưu được cấu hình'))}finally{setBusy(false)}}
 const saveReply=async(next:ReplyOptions)=>{setBusy(true);setError('');try{setReply(await apiRequest<ReplyOptions>('/ai/live/reply-config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(next)}))}catch(e){setError(errorText(e,'Không lưu được cấu hình trả lời'))}finally{setBusy(false)}}
 const setMode=(mode:ReplyOptions['mode'])=>{if(mode==='auto'&&!window.confirm('Chế độ TỰ ĐỘNG sẽ đọc câu trả lời lên LIVE mà không chờ bạn duyệt (câu khiếu nại, câu AI không chắc vẫn luôn chờ duyệt). Bật?'))return;void saveReply({...reply,mode})}
 const act=async(r:Reply,action:string,body?:object)=>{setError('');try{const updated=await apiRequest<Reply>(onAgent(`/ai/live/replies/${r.id}/${action}`,agent.id),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body??{})});setStatus(s=>s&&{...s,replies:s.replies.map(x=>x.id===updated.id?updated:x)})}catch(e){setError(errorText(e,'Thao tác thất bại'))}}
 const speak=(r:Reply)=>{const edited=editing[r.id];if(r.voice==='review'&&!window.confirm('Câu này đang cần duyệt. Vẫn phát lên LIVE?'))return;void act(r,'speak',edited!==undefined&&edited!==r.say?{say:edited}:{}).then(()=>setEditing(x=>{const n={...x};delete n[r.id];return n}))}
 const toggleEdit=(r:Reply)=>setEditing(x=>{const n={...x};if(n[r.id]!==undefined)delete n[r.id];else n[r.id]=r.say;return n})
 const copy=async(r:Reply)=>{const text=r.comment_text||r.text;try{await navigator.clipboard.writeText(text);void act(r,'commented')}catch{setError('Không copy được, hãy chọn và copy thủ công: '+text)}}
 const preview=(text:string)=>{if(!('speechSynthesis' in window))return setError('Trình duyệt này chưa hỗ trợ Speech Synthesis');window.speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(text);u.lang='vi-VN';window.speechSynthesis.speak(u)}
 const stop=async()=>{setError('');try{await apiRequest(onAgent('/ai/live/voice/stop',agent.id),{method:'POST'});window.speechSynthesis?.cancel()}catch(e){setError(errorText(e,'Không dừng được giọng nói'))}}
 const s=status?.stats??{}
 const replies=(status?.replies??[]).filter(r=>!r.dismissed)
 const av=status?.agent_voice
 const prep=status?.prepared
 return <section className="tc-panel">
  <div className="tc-chat-title"><h3><Mic size={18}/> AI trả lời LIVE</h3>
   <div className="tc-meta"><span>Chờ gom: <b>{status?.pending??0}</b></span><span>Chờ nói: <b>{status?.voice_queue??0}</b></span><span>Chưa trả lời: <b style={{color:(status?.unanswered??0)>0?'#bc3535':undefined}}>{status?.unanswered??0}</b></span>
    <button type="button" onClick={()=>void stop()} style={{background:'#bc3535',borderColor:'#bc3535',color:'white'}}><OctagonX size={15}/> Dừng giọng nói</button></div></div>
  <div className="tc-meta" style={{marginTop:6,fontSize:12}}>
   <span>Agent giọng nói: <b style={{color:av?.online?'#187347':'#bc3535'}}>{av?.online?'online':'offline'}</b></span>
   {av?.online&&<><span>{av.speaking?<>đang đọc “<b>{av.speaking}</b>”</>:'rảnh'}</span><span>chờ <b>{av.queue}</b> câu</span>{av.prepare_pending>0&&<span>đang chuẩn bị âm thanh <b>{av.prepare_pending}</b></span>}</>}
   {av?.last?.status==='failed'&&<span style={{color:'#bc3535'}}>Lỗi phát: {av.last.message||av.last.code||'không rõ'}</span>}
   {prep&&prep.state!=='none'&&<span style={{color:prep.state==='error'?'#bc3535':undefined}}>{prep.state==='ready'?`Đã chuẩn bị ${prep.count} câu trả lời cho ${prep.product_name}`:prep.state==='running'?`Đang chuẩn bị câu trả lời cho ${prep.product_name}…`:`Chuẩn bị câu trả lời lỗi: ${prep.error||'không rõ'}`}</span>}
  </div>
  <p className="tc-note">Mỗi câu hỏi có câu trả lời riêng. Câu hỏi giá được trả lời từ dữ liệu sản phẩm (không tốn token); câu giống hệt đã hỏi dùng lại cache. Chỉ gộp nhiều người khi cùng ý và cùng chi tiết (vd cùng cân nặng). Giọng nói phát qua nguồn <b>Sarae Voice</b> trong OBS; comment chưa tự gửi lên TikTok, bấm Copy rồi dán.</p>
  <div className="tc-meta" style={{marginTop:10}}>
   <label style={{display:'flex',alignItems:'center',gap:8}}><input type="checkbox" disabled={busy} checked={options.enabled} onChange={e=>void save({...options,enabled:e.target.checked})}/> Bật AI realtime</label>
   <label>Gom (giây) <input style={{width:58}} type="number" min={1} max={10} step={0.5} value={options.window_seconds} onChange={e=>setOptions(x=>({...x,window_seconds:Number(e.target.value)}))} onBlur={()=>void save(options)}/></label>
   <label>Tối đa/lượt <input style={{width:58}} type="number" min={1} max={30} value={options.max_comments} onChange={e=>setOptions(x=>({...x,max_comments:Number(e.target.value)}))} onBlur={()=>void save(options)}/></label>
  </div>
  <div className="tc-meta" style={{marginTop:10}}>
   <label>Chế độ <select disabled={busy} value={reply.mode} onChange={e=>setMode(e.target.value as ReplyOptions['mode'])}><option value="manual">Thủ công (bấm phát)</option><option value="semi_auto">Bán tự động (tự nói nếu không huỷ)</option><option value="auto">Tự động</option></select></label>
   <label>Kênh <select disabled={busy} value={reply.channel} onChange={e=>void saveReply({...reply,channel:e.target.value as ReplyOptions['channel']})}><option value="voice">Giọng nói</option><option value="comment">Comment</option><option value="both">Cả hai (thông minh)</option></select></label>
   <label>Giọng <select disabled={busy} value={reply.voice} onChange={e=>void saveReply({...reply,voice:e.target.value})}><option value="vi-VN-HoaiMyNeural">Hoài My (nữ)</option><option value="vi-VN-NamMinhNeural">Nam Minh (nam)</option></select></label>
   <label>Tốc độ % <input style={{width:52}} type="number" min={-30} max={50} value={reply.rate} onChange={e=>setReply(x=>({...x,rate:Number(e.target.value)}))} onBlur={()=>void saveReply(reply)}/></label>
   {reply.mode==='semi_auto'&&<label>Thời gian huỷ (giây) <input style={{width:52}} type="number" min={1} max={15} value={reply.veto_seconds} onChange={e=>setReply(x=>({...x,veto_seconds:Number(e.target.value)}))} onBlur={()=>void saveReply(reply)}/></label>}
  </div>
  <details style={{marginTop:8}}><summary className="tc-note" style={{cursor:'pointer',margin:0}}>Tuỳ chỉnh nâng cao</summary>
   <div className="tc-meta" style={{marginTop:8}}>
    <label>Quá hạn nói (giây) <input style={{width:52}} type="number" min={5} max={120} value={reply.max_age_seconds} onChange={e=>setReply(x=>({...x,max_age_seconds:Number(e.target.value)}))} onBlur={()=>void saveReply(reply)}/></label>
    <label>Không lặp chủ đề trong (giây) <input style={{width:52}} type="number" min={0} max={600} value={reply.topic_cooldown_seconds} onChange={e=>setReply(x=>({...x,topic_cooldown_seconds:Number(e.target.value)}))} onBlur={()=>void saveReply(reply)}/></label>
    <label>Ngưỡng tự tin <input style={{width:52}} type="number" min={0.3} max={1} step={0.05} value={reply.min_confidence} onChange={e=>setReply(x=>({...x,min_confidence:Number(e.target.value)}))} onBlur={()=>void saveReply(reply)}/></label>
    <label style={{display:'flex',alignItems:'center',gap:6}}><input type="checkbox" checked={reply.call_names} onChange={e=>void saveReply({...reply,call_names:e.target.checked})}/> Gọi tên người hỏi</label>
    <label style={{display:'flex',alignItems:'center',gap:6}}><input type="checkbox" checked={reply.duck_video} onChange={e=>void saveReply({...reply,duck_video:e.target.checked})}/> Giảm âm video khi AI nói</label>
    <label style={{display:'flex',alignItems:'center',gap:6}}><input type="checkbox" checked={reply.auto_prepare} onChange={e=>void saveReply({...reply,auto_prepare:e.target.checked})}/> Tự chuẩn bị câu trả lời khi đổi sản phẩm</label>
   </div>
   <label className="tc-note" style={{display:'grid',gap:6}}>Phong cách của shop (AI sẽ nói theo)<textarea rows={2} maxLength={300} placeholder="vd: xưng em, gọi khách là chị, vui vẻ, hay dùng 'nha'" value={reply.persona} onChange={e=>setReply(x=>({...x,persona:e.target.value}))} onBlur={()=>void saveReply(reply)}/></label>
  </details>
  <div className="tc-meta" style={{marginTop:10}}><span>Nhận: <b>{s.received??0}</b></span><span>Bỏ qua (nhiễu): <b>{s.noise??0}</b></span><span>Luật: <b>{s.rule??0}</b></span><span>Kho câu: <b>{s.cached??0}</b></span><span>Gọi AI: <b>{s.ai_questions??0}</b> câu</span><span>Đã nói: <b>{s.spoken??0}</b></span><span>Cần duyệt: <b>{s.review??0}</b></span></div>
  <details style={{marginTop:8}} onToggle={e=>setBankOpen(e.currentTarget.open)}><summary className="tc-note" style={{cursor:'pointer',margin:0}}>Kho câu trả lời của sản phẩm đang phát</summary>
   <div className="tc-meta" style={{marginTop:8}}><span>{bank?.product_id?<>Sản phẩm: <b>{bank.product_name}</b> · {bank.answers.length} câu</>:bank?'Chưa có sản phẩm đang phát':'Đang tải…'}</span>
    <button type="button" disabled={bankBusy} onClick={()=>void prepare()}>{bankBusy?'Đang chuẩn bị…':'Chuẩn bị lại câu trả lời'}</button></div>
   <div style={{display:'grid',gap:8,marginTop:8}}>
    {bank?.answers.map(a=><div key={a.id} style={{border:'1px solid var(--border)',borderRadius:10,padding:10,display:'grid',gap:5}}>
     <div style={{display:'flex',gap:6,flexWrap:'wrap',alignItems:'center'}}><b style={{fontSize:13}}>{a.question}</b>
      <span style={chip(a.source==='ai'?'':'ok')}>{ANSWER_SOURCE[a.source]??a.source}</span>{a.approved&&<span style={chip('ok')}>✓ Đã duyệt</span>}<span style={chip('')}>Dùng {a.uses} lần</span>
      <button type="button" style={{marginLeft:'auto'}} onClick={()=>void removeAnswer(a)}><X size={14}/> Xoá</button></div>
     {a.say.map((v,i)=><div key={i} style={{fontSize:12.5,padding:'5px 8px',borderRadius:6,background:'var(--soft)'}}>🔊 {v}</div>)}
    </div>)}
    {bank&&bank.answers.length===0&&<p className="tc-note" style={{margin:0}}>Kho đang trống. Bấm “Chuẩn bị lại câu trả lời” để AI soạn sẵn.</p>}
   </div>
  </details>
  {error&&<p className="tc-error">{error}</p>}
  <div style={{display:'grid',gap:10,marginTop:14}}>
   {replies.length===0?<p className="tc-note">Chưa có câu trả lời. Bắt đầu phiên phát sản phẩm, kết nối TikTok Chat rồi bật AI realtime.</p>:replies.map(r=>{
    const left=r.voice==='scheduled'&&r.speak_at?Math.max(0,Math.ceil(r.speak_at-now/1000)):null
    return <div key={r.id} style={{border:'1px solid var(--border)',borderRadius:12,padding:12,display:'grid',gap:7,borderLeft:r.voice==='review'?'4px solid #e0a100':undefined}}>
     <div style={{display:'flex',gap:6,flexWrap:'wrap',alignItems:'center'}}>
      <span style={chip(r.source==='ai'?'':'ok')}>{SOURCE_LABEL[r.source]}</span><span style={chip('')}>{r.intent}</span><span style={chip(r.confidence<reply.min_confidence?'warn':'')}>{Math.round(r.confidence*100)}%</span>
      {r.voice&&<span style={chip(r.voice==='review'?'warn':r.voice==='sent'?'ok':r.voice==='skipped'?'bad':'')}>{VOICE_LABEL[r.voice]??r.voice}{left!==null?` · ${left}s`:''}</span>}
      {r.comment&&<span style={chip(r.comment==='done'?'ok':'')}>{COMMENT_LABEL[r.comment]??r.comment}</span>}
      <small style={{marginLeft:'auto',color:'var(--muted)'}}>#{r.id} · {r.product_name}</small>
     </div>
     <div style={{fontSize:13}}>{r.comments.map(c=><div key={c.id}><b>{c.nickname||c.username}</b>: {c.comment}</div>)}</div>
     {r.review_reasons.length>0&&<div style={{fontSize:12,color:'#8a5a00'}}>⚠ {r.review_reasons.join(' · ')}</div>}
     {r.note&&<div style={{fontSize:12,color:'var(--muted)'}}>{r.note}</div>}
     {editing[r.id]!==undefined?<textarea rows={2} maxLength={300} value={editing[r.id]} onChange={e=>setEditing(x=>({...x,[r.id]:e.target.value}))}/>:r.say&&<div style={{padding:9,borderRadius:8,background:'var(--soft)',fontSize:13}}><b>🔊 </b>{r.spoken_text||r.say}</div>}
     {(r.comment_text||r.text)&&<div style={{padding:9,borderRadius:8,border:'1px dashed var(--border)',fontSize:13}}><b>💬 </b>{r.comment_text||r.text}</div>}
     <div className="tc-meta">
      {left!==null&&<button type="button" onClick={()=>void act(r,'cancel')}><X size={14}/> Huỷ phát</button>}
      {r.say&&<button type="button" onClick={()=>speak(r)}><Mic size={14}/> {r.voice==='sent'?'Phát lại':'Phát giọng'}</button>}
      {r.say&&r.voice!=='sent'&&<button type="button" onClick={()=>toggleEdit(r)}>{editing[r.id]!==undefined?'Bỏ sửa':'Sửa câu'}</button>}
      {r.say&&<button type="button" onClick={()=>preview(editing[r.id]??r.say)}><Volume2 size={14}/> Nghe thử</button>}
      {(r.comment_text||r.text)&&r.comment!=='done'&&<button type="button" onClick={()=>void copy(r)}><Copy size={14}/> Copy comment</button>}
      {r.voice!=='sent'&&r.comment!=='done'&&<button type="button" onClick={()=>void act(r,'dismiss')}>Bỏ qua</button>}
     </div>
    </div>})}
  </div>
  {batches.length>0&&<details style={{marginTop:14}}><summary className="tc-note" style={{cursor:'pointer',margin:0}}>Lượt gọi gần đây (token)</summary>
   {batches.slice().reverse().map(b=><div key={b.batch_id} className="tc-note" style={{margin:'6px 0 0'}}>#{b.batch_id} · {b.product_name}: {b.comments} bình luận → {b.questions} câu hỏi (luật {b.sources.rule}, kho câu {b.sources.bank??b.sources.cache??0}, AI {b.sources.ai}){b.usage.map((u,i)=><span key={i}> · {u.input_tokens} in / {u.output_tokens} out · {u.latency_ms}ms</span>)}{b.skipped.length>0&&<span> · bỏ qua {b.skipped.length}: {b.skipped.map(x=>x.comment).join(', ')}</span>}{b.error&&<span style={{color:'#bc3535'}}> · {b.error}</span>}</div>)}
  </details>}
 </section>
}
