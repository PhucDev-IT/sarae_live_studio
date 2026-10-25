import {useEffect,useRef,useState} from 'react'
import {apiRequest,errorText,onAgent,type OwnerAgent} from './mediaApi'
import {Copy,Mic,OctagonX,Volume2,X} from 'lucide-react'
import './live-ai.css'
type Options={enabled:boolean;window_seconds:number;max_comments:number}
type ReplyOptions={mode:'manual'|'semi_auto'|'auto';channel:'voice'|'comment'|'both';voice:string;rate:number;veto_seconds:number;max_age_seconds:number;topic_cooldown_seconds:number;min_confidence:number;duck_video:boolean;persona:string;auto_prepare:boolean}
type Reply={id:number;batch_id:number;product_name:string;question:string;comments:{id:number;username:string;nickname:string;comment:string}[];intent:string;slots:Record<string,string|number>;say:string;text:string;confidence:number;source:'rule'|'bank'|'cache'|'ai';review_reasons:string[];route:string|null;voice:string|null;comment:string|null;note:string;spoken_text:string;comment_text?:string;speak_at?:number;dismissed?:boolean;asked_at:string}
type Batch={batch_id:number;product_name:string;comments:number;questions:number;skipped:{username:string;comment:string;reason:string}[];sources:{rule:number;bank?:number;cache?:number;ai:number};usage:{input_tokens:number;output_tokens:number;latency_ms:number}[];error:string|null}
type AgentVoice={online:boolean;queue:number;speaking:string|null;ducked:boolean;stats:Record<string,number>;last:{status:string;code?:string;message?:string;provider?:string;model?:string;voice?:string;duration_ms?:string}|null;prepare_pending:number}
type Prepared={product_id:number;product_name:string;state:'none'|'running'|'ready'|'error';count:number;error?:string}
type Status={stats:Record<string,number>;pending:number;voice_queue:number;unanswered:number;batches:Batch[];replies:Reply[];agent_voice?:AgentVoice|null;prepared?:Prepared|null}
type Answer={id:number;question:string;intent:string;say:string[];text:string[];source:'ai'|'pregen'|'operator';approved:boolean;uses:number;last_used_at:string|null}
type Answers={product_id:number|null;product_name:string;answers:Answer[]}
const DEFAULT_REPLY:ReplyOptions={mode:'manual',channel:'voice',voice:'vi-VN-HoaiMyNeural',rate:5,veto_seconds:4,max_age_seconds:25,topic_cooldown_seconds:40,min_confidence:0.75,duck_video:true,persona:'',auto_prepare:true}
const ANSWER_SOURCE:Record<Answer['source'],string>={ai:'AI',pregen:'Chuẩn bị sẵn',operator:'Bạn sửa'}
const VOICE_LABEL:Record<string,string>={review:'Cần duyệt',ready:'Chờ bấm phát',scheduled:'Sắp nói',queued:'Chờ lượt nói',sent:'Đã gửi giọng',skipped:'Không nói'}
const COMMENT_LABEL:Record<string,string>={ready:'Comment chờ gửi',done:'Đã comment',dismissed:'Bỏ qua'}
const SOURCE_LABEL:Record<Reply['source'],string>={rule:'Luật · 0 token',bank:'Kho câu · 0 token',cache:'Cache · 0 token',ai:'AI'}
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
 const modeLabel=reply.mode==='manual'?'Thủ công':reply.mode==='semi_auto'?'Bán tự động':'Tự động'
 const channelLabel=reply.channel==='voice'?'Giọng nói':reply.channel==='comment'?'Comment':'Giọng nói + comment'
 const voiceLabel='Voice Settings'
 const unanswered=status?.unanswered??0
 const stat=(label:string,value:number,tone?:string)=><div className={'lai-stat'+(tone?' is-'+tone:'')}><b>{value}</b><span>{label}</span></div>
 return <section className="lai">
  <div className="lai-top">
   <label className="lai-switch"><input type="checkbox" disabled={busy} checked={options.enabled} onChange={e=>void save({...options,enabled:e.target.checked})}/><span><b>{options.enabled?'AI đang trả lời':'AI đang tắt'}</b><small>{options.enabled?'Sarae soạn câu trả lời cho từng câu hỏi của người xem':'Bật để Sarae tự soạn câu trả lời'}</small></span></label>
   <button type="button" className="lai-stop" onClick={()=>void stop()}><OctagonX size={16}/> Dừng giọng nói</button>
  </div>
  <div className="lai-voice">
   <span className={'lai-dot'+(av?.online?' is-on':'')}/>
   <span>Giọng nói trên OBS: <b>{av?.online?(av.speaking?<>đang đọc “{av.speaking}”</>:'sẵn sàng'):'chưa kết nối'}</b></span>
   {av?.online&&av.queue>0&&<span>· chờ <b>{av.queue}</b> câu</span>}
   {av?.online&&av.prepare_pending>0&&<span>· đang chuẩn bị âm thanh <b>{av.prepare_pending}</b></span>}
   {av?.last?.status==='failed'&&<span className="is-bad">· Lỗi phát: {av.last.message||av.last.code||'không rõ'}</span>}
   {av?.last?.status==='spoken'&&<span>· vừa phát {av.last.provider}/{av.last.model}/{av.last.voice} · {av.last.duration_ms} ms</span>}
   {prep&&prep.state!=='none'&&<span className={prep.state==='error'?'is-bad':undefined}>· {prep.state==='ready'?`Đã chuẩn bị ${prep.count} câu trả lời cho ${prep.product_name}`:prep.state==='running'?`Đang chuẩn bị câu trả lời cho ${prep.product_name}…`:`Chuẩn bị câu trả lời lỗi: ${prep.error||'không rõ'}`}</span>}
  </div>
  <div className="lai-stats">
   {stat('Chờ gom',status?.pending??0)}{stat('Chờ nói',status?.voice_queue??0)}{stat('Chưa trả lời',unanswered,unanswered>0?'bad':undefined)}
   {stat('Đã nhận',s.received??0)}{stat('Bỏ qua',s.noise??0)}{stat('Luật',s.rule??0)}{stat('Kho câu',s.cached??0)}{stat('Hỏi AI',s.ai_questions??0)}{stat('Đã nói',s.spoken??0,'ok')}{stat('Cần duyệt',s.review??0,(s.review??0)>0?'warn':undefined)}
  </div>
  <details className="lai-block">
   <summary><span className="lai-block-title">Cài đặt AI</span><span className="lai-block-sum">{modeLabel} · {channelLabel} · {voiceLabel}</span></summary>
   <div className="lai-block-body">
    <div className="lai-fields">
     <label>Cách phát<select disabled={busy} value={reply.mode} onChange={e=>setMode(e.target.value as ReplyOptions['mode'])}><option value="manual">Thủ công (bấm phát)</option><option value="semi_auto">Bán tự động (tự nói nếu không huỷ)</option><option value="auto">Tự động</option></select></label>
     <label>Trả lời bằng<select disabled={busy} value={reply.channel} onChange={e=>void saveReply({...reply,channel:e.target.value as ReplyOptions['channel']})}><option value="voice">Giọng nói</option><option value="comment">Comment</option><option value="both">Cả hai (thông minh)</option></select></label>
     <p className="lai-hint">Provider, model, giọng và tốc độ được cấu hình tại <a href="#/ai">Voice Settings</a>.</p>
     {reply.mode==='semi_auto'&&<label>Thời gian huỷ (giây)<input type="number" min={1} max={15} value={reply.veto_seconds} onChange={e=>setReply(x=>({...x,veto_seconds:Number(e.target.value)}))} onBlur={()=>void saveReply(reply)}/></label>}
     <label>Gom bình luận (giây)<input type="number" min={1} max={10} step={0.5} value={options.window_seconds} onChange={e=>setOptions(x=>({...x,window_seconds:Number(e.target.value)}))} onBlur={()=>void save(options)}/></label>
     <label>Tối đa mỗi lượt<input type="number" min={1} max={30} value={options.max_comments} onChange={e=>setOptions(x=>({...x,max_comments:Number(e.target.value)}))} onBlur={()=>void save(options)}/></label>
    </div>
    <p className="lai-hint">Mỗi câu hỏi có câu trả lời riêng. Câu hỏi giá được trả lời từ dữ liệu sản phẩm (không tốn token); câu giống hệt đã hỏi dùng lại câu cũ. Chỉ gộp nhiều người khi cùng ý và cùng chi tiết (vd cùng cân nặng). Giọng nói phát qua nguồn <b>Sarae Voice</b> trong OBS; comment chưa tự gửi lên TikTok, bấm Copy rồi dán.</p>
    <details className="lai-sub"><summary>Tuỳ chỉnh nâng cao</summary>
     <div className="lai-fields">
      <label>Quá hạn nói (giây)<input type="number" min={5} max={120} value={reply.max_age_seconds} onChange={e=>setReply(x=>({...x,max_age_seconds:Number(e.target.value)}))} onBlur={()=>void saveReply(reply)}/></label>
      <label>Không lặp chủ đề trong (giây)<input type="number" min={0} max={600} value={reply.topic_cooldown_seconds} onChange={e=>setReply(x=>({...x,topic_cooldown_seconds:Number(e.target.value)}))} onBlur={()=>void saveReply(reply)}/></label>
      <label>Ngưỡng tự tin<input type="number" min={0.3} max={1} step={0.05} value={reply.min_confidence} onChange={e=>setReply(x=>({...x,min_confidence:Number(e.target.value)}))} onBlur={()=>void saveReply(reply)}/></label>
     </div>
     <div className="lai-checks">
      <label><input type="checkbox" checked={reply.duck_video} onChange={e=>void saveReply({...reply,duck_video:e.target.checked})}/> Giảm âm video khi AI nói</label>
      <label><input type="checkbox" checked={reply.auto_prepare} onChange={e=>void saveReply({...reply,auto_prepare:e.target.checked})}/> Tự chuẩn bị câu trả lời khi đổi sản phẩm</label>
     </div>
     <label className="lai-persona">Phong cách của shop (AI sẽ nói theo)<textarea rows={2} maxLength={300} placeholder="vd: xưng em, gọi khách là chị, vui vẻ, hay dùng 'nha'" value={reply.persona} onChange={e=>setReply(x=>({...x,persona:e.target.value}))} onBlur={()=>void saveReply(reply)}/></label>
    </details>
   </div>
  </details>
  <details className="lai-block" onToggle={e=>setBankOpen(e.currentTarget.open)}>
   <summary><span className="lai-block-title">Kho câu trả lời</span><span className="lai-block-sum">{bank?.product_id?`${bank.product_name} · ${bank.answers.length} câu`:'Của sản phẩm đang phát'}</span></summary>
   <div className="lai-block-body">
    <div className="lai-row"><span>{bank?.product_id?<>Sản phẩm: <b>{bank.product_name}</b> · {bank.answers.length} câu</>:bank?'Chưa có sản phẩm đang phát':'Đang tải…'}</span>
     <button type="button" className="lai-btn" disabled={bankBusy} onClick={()=>void prepare()}>{bankBusy?'Đang chuẩn bị…':'Chuẩn bị lại câu trả lời'}</button></div>
    <div className="lai-list">
     {bank?.answers.map(a=><div key={a.id} className="lai-card">
      <div className="lai-chips"><b className="lai-q">{a.question}</b>
       <span className={'lai-chip'+(a.source==='ai'?'':' is-ok')}>{ANSWER_SOURCE[a.source]??a.source}</span>{a.approved&&<span className="lai-chip is-ok">✓ Đã duyệt</span>}<span className="lai-chip">Dùng {a.uses} lần</span>
       <button type="button" className="lai-btn lai-push" onClick={()=>void removeAnswer(a)}><X size={14}/> Xoá</button></div>
      {a.say.map((v,i)=><div key={i} className="lai-say">🔊 {v}</div>)}
     </div>)}
     {bank&&bank.answers.length===0&&<p className="lai-hint">Kho đang trống. Bấm “Chuẩn bị lại câu trả lời” để AI soạn sẵn.</p>}
    </div>
   </div>
  </details>
  {error&&<p className="lai-error" role="alert">{error}</p>}
  <div className="lai-list">
   {replies.length===0?<div className="lai-empty"><strong>Chưa có câu trả lời</strong><span>{options.enabled?'Khi người xem hỏi, câu trả lời sẽ hiện ở đây để bạn phát hoặc copy.':'Bật công tắc “AI đang tắt” ở trên để Sarae bắt đầu soạn câu trả lời.'}</span></div>:replies.map(r=>{
    const left=r.voice==='scheduled'&&r.speak_at?Math.max(0,Math.ceil(r.speak_at-now/1000)):null
    return <div key={r.id} className={'lai-card lai-reply'+(r.voice==='review'?' is-review':'')}>
     <div className="lai-chips">
      <span className={'lai-chip'+(r.source==='ai'?'':' is-ok')}>{SOURCE_LABEL[r.source]}</span><span className="lai-chip">{r.intent}</span><span className={'lai-chip'+(r.confidence<reply.min_confidence?' is-warn':'')}>{Math.round(r.confidence*100)}%</span>
      {r.voice&&<span className={'lai-chip'+(r.voice==='review'?' is-warn':r.voice==='sent'?' is-ok':r.voice==='skipped'?' is-bad':'')}>{VOICE_LABEL[r.voice]??r.voice}{left!==null?` · ${left}s`:''}</span>}
      {r.comment&&<span className={'lai-chip'+(r.comment==='done'?' is-ok':'')}>{COMMENT_LABEL[r.comment]??r.comment}</span>}
      <small className="lai-push lai-muted">#{r.id} · {r.product_name}</small>
     </div>
     <div className="lai-asks">{r.comments.map(c=><div key={c.id}><b>{c.nickname||c.username}</b>: {c.comment}</div>)}</div>
     {r.review_reasons.length>0&&<div className="lai-warn">⚠ {r.review_reasons.join(' · ')}</div>}
     {r.note&&<div className="lai-muted">{r.note}</div>}
     {editing[r.id]!==undefined?<textarea rows={2} maxLength={300} value={editing[r.id]} onChange={e=>setEditing(x=>({...x,[r.id]:e.target.value}))}/>:r.say&&<div className="lai-say"><b>🔊 </b>{r.spoken_text||r.say}</div>}
     {(r.comment_text||r.text)&&<div className="lai-say is-comment"><b>💬 </b>{r.comment_text||r.text}</div>}
     <div className="lai-actions">
      {left!==null&&<button type="button" className="lai-btn" onClick={()=>void act(r,'cancel')}><X size={14}/> Huỷ phát</button>}
      {r.say&&<button type="button" className="lai-btn is-primary" onClick={()=>speak(r)}><Mic size={14}/> {r.voice==='sent'?'Phát lại':'Phát giọng'}</button>}
      {r.say&&r.voice!=='sent'&&<button type="button" className="lai-btn" onClick={()=>toggleEdit(r)}>{editing[r.id]!==undefined?'Bỏ sửa':'Sửa câu'}</button>}
      {r.say&&<button type="button" className="lai-btn" onClick={()=>preview(editing[r.id]??r.say)}><Volume2 size={14}/> Nghe thử</button>}
      {(r.comment_text||r.text)&&r.comment!=='done'&&<button type="button" className="lai-btn" onClick={()=>void copy(r)}><Copy size={14}/> Copy comment</button>}
      {r.voice!=='sent'&&r.comment!=='done'&&<button type="button" className="lai-btn is-ghost" onClick={()=>void act(r,'dismiss')}>Bỏ qua</button>}
     </div>
    </div>})}
  </div>
  {batches.length>0&&<details className="lai-block lai-quiet"><summary><span className="lai-block-title">Lượt gọi gần đây (token)</span></summary>
   <div className="lai-block-body">{batches.slice().reverse().map(b=><div key={b.batch_id} className="lai-hint">#{b.batch_id} · {b.product_name}: {b.comments} bình luận → {b.questions} câu hỏi (luật {b.sources.rule}, kho câu {b.sources.bank??b.sources.cache??0}, AI {b.sources.ai}){b.usage.map((u,i)=><span key={i}> · {u.input_tokens} in / {u.output_tokens} out · {u.latency_ms}ms</span>)}{b.skipped.length>0&&<span> · bỏ qua {b.skipped.length}: {b.skipped.map(x=>x.comment).join(', ')}</span>}{b.error&&<span className="is-bad"> · {b.error}</span>}</div>)}</div>
  </details>}
 </section>
}
