import VoiceUtterances from './VoiceUtterances'
import './voice-settings.css'
import {useEffect,useRef,useState,type ChangeEvent} from 'react'
import {apiRequest,errorText,onAgent,type OwnerAgent} from './mediaApi'
import {Copy,Mic,OctagonX,Send,Volume2,X} from 'lucide-react'
import './live-ai.css'
type Options={enabled:boolean;window_seconds:number;max_comments:number}
/** Owner reply settings (docs/AI_AUTO_REPLY.md). Voice and TikTok comment are two independent automatic branches. */
type ReplyOptions={auto_voice_reply:boolean;auto_comment_reply:boolean;confirm_seconds:number;comment_min_interval_seconds:number;comment_max_per_minute:number;comment_max_age_seconds:number;voice:string;rate:number;max_age_seconds:number;topic_cooldown_seconds:number;min_confidence:number;duck_video:boolean;persona:string;auto_prepare:boolean;ignored_usernames:string[]}
type CommentState='scheduled'|'queued'|'sending'|'sent'|'failed'|'expired'|'ready'|'done'|'dismissed'
type DeliveryStatus='QUEUED'|'TYPING'|'SUBMITTED'|'CONFIRMED'|'FAILED'|'EXPIRED'|'DUPLICATE'|'RATE_LIMITED'
type DeliveryMode='DIRECT_REPLY'|'MENTION'|'NORMAL'
type CommentDelivery={status:DeliveryStatus;mode:DeliveryMode|null;error:string|null;steps:{step:string;ms:number;action?:string}[];sent_at?:string|null}
type Reply={id:number;batch_id:number;product_name:string;question:string;comments:{id:number;username:string;nickname:string;comment:string}[];intent:string;slots:Record<string,string|number>;say:string;text:string;confidence:number;source:'rule'|'bank'|'cache'|'ai';review_reasons:string[];route:string|null;voice:string|null;comment:CommentState|null;comment_delivery?:CommentDelivery|null;comment_results?:{msg_id:string;username:string;nickname:string;status:DeliveryStatus;mode:DeliveryMode|null;error:string|null}[];comment_send_at?:number;note:string;spoken_text:string;comment_text?:string;speak_at?:number;dismissed?:boolean;asked_at:string}
type Batch={batch_id:number;product_name:string;comments:number;questions:number;skipped:{username:string;comment:string;reason:string}[];sources:{rule:number;bank?:number;cache?:number;ai:number};usage:{input_tokens:number;output_tokens:number;latency_ms:number}[];error:string|null}
type AgentVoice={online:boolean;queue:number;speaking:string|null;ducked:boolean;stats:Record<string,number>;last:{status:string;code?:string;message?:string;provider?:string;model?:string;voice?:string;duration_ms?:string}|null;prepare_pending:number}
type Prepared={product_id:number;product_name:string;state:'none'|'running'|'ready'|'error';count:number;error?:string}
type Status={stats:Record<string,number>;pending:number;voice_queue:number;unanswered:number;batches:Batch[];replies:Reply[];agent_voice?:AgentVoice|null;prepared?:Prepared|null}
type Answer={id:number;question:string;intent:string;say:string[];text:string[];source:'ai'|'pregen'|'operator';approved:boolean;uses:number;last_used_at:string|null}
type Answers={product_id:number|null;product_name:string;answers:Answer[]}
const DEFAULT_REPLY:ReplyOptions={auto_voice_reply:false,auto_comment_reply:false,confirm_seconds:4,comment_min_interval_seconds:8,comment_max_per_minute:5,comment_max_age_seconds:60,voice:'vi-VN-HoaiMyNeural',rate:5,max_age_seconds:25,topic_cooldown_seconds:40,min_confidence:0.75,duck_video:true,persona:'',auto_prepare:true,ignored_usernames:[]}
const ANSWER_SOURCE:Record<Answer['source'],string>={ai:'AI',pregen:'Chuẩn bị sẵn',operator:'Bạn sửa'}
const VOICE_LABEL:Record<string,string>={review:'Cần duyệt',ready:'Chờ bấm phát',scheduled:'Sắp nói',queued:'Chờ lượt nói',sent:'Đã gửi giọng',skipped:'Không nói'}
const COMMENT_LABEL:Record<CommentState,string>={scheduled:'Chờ gửi comment',queued:'Chờ gửi comment',sending:'Đang gửi',sent:'Đã gửi',failed:'Gửi lỗi',expired:'Quá hạn',ready:'Comment chờ gửi',done:'Đã comment',dismissed:'Bỏ qua'}
const DELIVERY_LABEL:Record<DeliveryStatus,{label:string;tone:string}>={QUEUED:{label:'Chờ gửi comment',tone:''},TYPING:{label:'Đang gửi',tone:''},SUBMITTED:{label:'Đã gửi (chưa xác nhận)',tone:'is-warn'},CONFIRMED:{label:'Đã gửi ✓ (đã thấy trên LIVE)',tone:'is-ok'},FAILED:{label:'Gửi lỗi',tone:'is-bad'},EXPIRED:{label:'Quá hạn',tone:'is-bad'},DUPLICATE:{label:'Bỏ qua trùng',tone:''},RATE_LIMITED:{label:'Đang chờ lượt (giới hạn tần suất)',tone:'is-warn'}}
const DELIVERY_MODE:Record<DeliveryMode,string>={DIRECT_REPLY:'Trả lời trực tiếp',MENTION:'Nhắc @tên',NORMAL:'Chat thường'}
/** Chip of the comment branch: the countdown while scheduled, then the Agent's delivery result when there is one. */
function commentChip(r:Reply,now:number):{label:string;tone:string}|null{
 if(!r.comment)return null
 if(r.comment==='scheduled'){const left=r.comment_send_at?Math.max(0,Math.ceil(r.comment_send_at-now/1000)):null;return {label:'Chờ gửi comment'+(left!==null?` · ${left}s`:''),tone:''}}
 if(r.comment==='sending')return {label:'Đang gửi',tone:''}
 if(r.comment==='done')return {label:COMMENT_LABEL.done,tone:'is-ok'}
 if(r.comment==='dismissed')return {label:COMMENT_LABEL.dismissed,tone:''}
 if(r.comment_delivery)return DELIVERY_LABEL[r.comment_delivery.status]??{label:r.comment_delivery.status,tone:''}
 return {label:COMMENT_LABEL[r.comment]??r.comment,tone:r.comment==='failed'||r.comment==='expired'?'is-bad':''}
}
/** Short voice name for the summary line: 'vi-VN-HoaiMyNeural' → 'Hoài My'. */
const VOICE_NAME:Record<string,string>={HoaiMy:'Hoài My',NamMinh:'Nam Minh'}
const voiceName=(v:string)=>{const k=v.replace(/^[a-z]{2}-[A-Z]{2}-/,'').replace(/Neural$/,'');return VOICE_NAME[k]??(k.replace(/([a-z])([A-Z])/g,'$1 $2')||'Voice Settings')}
const clamp=(v:number,min:number,max:number)=>Number.isFinite(v)?Math.min(max,Math.max(min,v)):min
/** Keeps the numbers inside the ranges the backend accepts before saving. */
const fixReply=(r:ReplyOptions):ReplyOptions=>({...r,confirm_seconds:clamp(r.confirm_seconds,0,15),comment_min_interval_seconds:clamp(r.comment_min_interval_seconds,4,120),comment_max_per_minute:clamp(Math.round(r.comment_max_per_minute),1,20),comment_max_age_seconds:clamp(r.comment_max_age_seconds,10,300)})
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
 const [commentEdit,setCommentEdit]=useState<Record<number,string>>({})
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
 const setAuto=(key:'auto_voice_reply'|'auto_comment_reply',on:boolean)=>{
  if(on&&!window.confirm(key==='auto_voice_reply'?'Sarae sẽ tự đọc câu trả lời lên LIVE (câu khiếu nại, câu AI không chắc vẫn chờ bạn duyệt). Bật?':'Sarae sẽ tự gửi câu trả lời thành bình luận trên LIVE bằng tài khoản TikTok đang đăng nhập ở máy phát (câu cần duyệt vẫn chờ bạn). Bật?'))return
  void saveReply(fixReply({...reply,[key]:on}))
 }
 const saveNumbers=()=>void saveReply(fixReply(reply))
 const sendComment=(r:Reply)=>{const edited=commentEdit[r.id];if(r.voice==='review'&&!window.confirm('Câu này đang cần duyệt. Vẫn gửi bình luận lên LIVE?'))return;void act(r,'comment',edited!==undefined&&edited.trim()&&edited!==(r.comment_text||r.text)?{text:edited.trim()}:{}).then(()=>setCommentEdit(x=>{const n={...x};delete n[r.id];return n}))}
 const toggleCommentEdit=(r:Reply)=>setCommentEdit(x=>{const n={...x};if(n[r.id]!==undefined)delete n[r.id];else n[r.id]=r.comment_text||r.text;return n})
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
 const summary=`Giọng: ${reply.auto_voice_reply?'bật':'tắt'} · Bình luận: ${reply.auto_comment_reply?'bật':'tắt'} · ${voiceName(reply.voice||'')}`
 const num=(key:keyof ReplyOptions)=>(e:ChangeEvent<HTMLInputElement>)=>setReply(x=>({...x,[key]:Number(e.target.value)}))
 const unanswered=status?.unanswered??0
 const stat=(label:string,value:number,tone?:string)=><div className={'lai-stat'+(tone?' is-'+tone:'')}><b>{value}</b><span>{label}</span></div>
 return <section className="lai">
  <VoiceUtterances agentId={agent.id}/>
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
   <summary><span className="lai-block-title">Cài đặt AI</span><span className="lai-block-sum">{summary}</span></summary>
   <div className="lai-block-body">
    <div className="lai-auto">
     <label className="lai-switch"><input type="checkbox" disabled={busy} checked={reply.auto_voice_reply} onChange={e=>setAuto('auto_voice_reply',e.target.checked)}/><span><b>🔊 Tự động trả lời bằng giọng nói</b><small>Sarae tự đọc câu trả lời qua nguồn Sarae Voice trong OBS.</small></span></label>
     <label className="lai-switch"><input type="checkbox" disabled={busy} checked={reply.auto_comment_reply} onChange={e=>setAuto('auto_comment_reply',e.target.checked)}/><span><b>💬 Tự động trả lời bằng bình luận TikTok</b><small>Sarae tự gửi câu trả lời thành bình luận trên LIVE.</small></span></label>
     {!reply.auto_voice_reply&&!reply.auto_comment_reply&&<p className="lai-hint">Không tự động trả lời — bạn bấm <b>Phát giọng</b> / <b>Gửi comment</b> trên từng câu.</p>}
    </div>
    <div className="lai-fields">
     <label>Thời gian để bạn huỷ trước khi gửi (giây, 0 = gửi ngay)<input type="number" min={0} max={15} value={reply.confirm_seconds} onChange={num('confirm_seconds')} onBlur={saveNumbers}/></label>
    </div>
    {reply.auto_comment_reply&&<div className="lai-group">
     <div className="lai-fields">
      <label>Cách nhau ít nhất (giây)<input type="number" min={4} max={120} value={reply.comment_min_interval_seconds} onChange={num('comment_min_interval_seconds')} onBlur={saveNumbers}/></label>
      <label>Tối đa mỗi phút<input type="number" min={1} max={20} value={reply.comment_max_per_minute} onChange={num('comment_max_per_minute')} onBlur={saveNumbers}/></label>
      <label>Bỏ qua nếu câu hỏi cũ hơn (giây)<input type="number" min={10} max={300} value={reply.comment_max_age_seconds} onChange={num('comment_max_age_seconds')} onBlur={saveNumbers}/></label>
     </div>
     <p className="lai-warn">⚠ Bình luận được gửi bằng tài khoản TikTok đang đăng nhập trong LIVE Console của máy phát. Gửi quá nhiều có thể bị TikTok giới hạn.</p>
    </div>}
    <p className="lai-hint">Provider, model, giọng và tốc độ được cấu hình tại <a href="#/ai">Voice Settings</a>.</p>
    <div className="lai-fields">
     <label>Gom bình luận (giây)<input type="number" min={1} max={10} step={0.5} value={options.window_seconds} onChange={e=>setOptions(x=>({...x,window_seconds:Number(e.target.value)}))} onBlur={()=>void save(options)}/></label>
     <label>Tối đa mỗi lượt<input type="number" min={1} max={30} value={options.max_comments} onChange={e=>setOptions(x=>({...x,max_comments:Number(e.target.value)}))} onBlur={()=>void save(options)}/></label>
    </div>
    <p className="lai-hint">Mỗi câu hỏi có câu trả lời riêng. Câu hỏi giá được trả lời từ dữ liệu sản phẩm (không tốn token); câu giống hệt đã hỏi dùng lại câu cũ. Chỉ gộp nhiều người khi cùng ý và cùng chi tiết (vd cùng cân nặng). Giọng nói phát qua nguồn <b>Sarae Voice</b> trong OBS; bình luận được gửi qua LIVE Console trên máy phát. Câu cần duyệt không bao giờ tự gửi.</p>
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
     <label className="lai-persona">Không trả lời các username (mỗi dòng một tên, vd @spam.bot; tài khoản đang LIVE luôn được bỏ qua)<textarea key={reply.ignored_usernames.join(',')} rows={3} placeholder={'@spam.bot\n@nhanvien.shop'} defaultValue={reply.ignored_usernames.map(u=>'@'+u).join('\n')} onBlur={e=>void saveReply({...reply,ignored_usernames:e.target.value.split(/[\s,;]+/).filter(Boolean)})}/></label>
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
    const chip=commentChip(r,now)
    const d=r.comment_delivery
    const commentText=r.comment_text||r.text
    const commentSent=r.comment==='sent'||r.comment==='done'||d?.status==='CONFIRMED'||d?.status==='SUBMITTED'
    const commentWaiting=r.comment==='scheduled'||r.comment==='queued'
    const canSend=!!commentText&&!commentSent&&!commentWaiting&&r.comment!=='sending'
    return <div key={r.id} className={'lai-card lai-reply'+(r.voice==='review'?' is-review':'')}>
     <div className="lai-chips">
      <span className={'lai-chip'+(r.source==='ai'?'':' is-ok')}>{SOURCE_LABEL[r.source]}</span><span className="lai-chip">{r.intent}</span><span className={'lai-chip'+(r.confidence<reply.min_confidence?' is-warn':'')}>{Math.round(r.confidence*100)}%</span>
      {r.voice&&<span className={'lai-chip'+(r.voice==='review'?' is-warn':r.voice==='sent'?' is-ok':r.voice==='skipped'?' is-bad':'')}>{VOICE_LABEL[r.voice]??r.voice}{left!==null?` · ${left}s`:''}</span>}
      {chip&&<span className={'lai-chip'+(chip.tone?' '+chip.tone:'')}>💬 {chip.label}</span>}
      {d?.mode&&<span className="lai-chip">{DELIVERY_MODE[d.mode]??d.mode}</span>}
      <small className="lai-push lai-muted">#{r.id} · {r.product_name}</small>
     </div>
     <div className="lai-asks">{r.comments.map((c,i)=>{
      // Several viewers asked the same question: each gets their own comment; show how each one went.
      const res=r.comments.length>1?r.comment_results?.[i]:undefined
      const tag=res?DELIVERY_LABEL[res.status]??{label:res.status,tone:''}:null
      return <div key={c.id}><b>{c.nickname||c.username}</b>: {c.comment}{tag&&<span className={'lai-muted'+(tag.tone?' '+tag.tone:'')}> · 💬 {tag.label}</span>}</div>})}</div>
     {r.review_reasons.length>0&&<div className="lai-warn">⚠ {r.review_reasons.join(' · ')}</div>}
     {r.note&&<div className="lai-muted">{r.note}</div>}
     {editing[r.id]!==undefined?<textarea rows={2} maxLength={300} value={editing[r.id]} onChange={e=>setEditing(x=>({...x,[r.id]:e.target.value}))}/>:r.say&&<div className="lai-say"><b>🔊 </b>{r.spoken_text||r.say}</div>}
     {commentEdit[r.id]!==undefined?<textarea rows={2} maxLength={150} value={commentEdit[r.id]} onChange={e=>setCommentEdit(x=>({...x,[r.id]:e.target.value}))}/>:commentText&&<div className="lai-say is-comment"><b>💬 </b>{commentText}</div>}
     {d&&(d.steps.length>0||d.error)&&<details className="lai-sub lai-delivery"><summary>Chi tiết gửi</summary>
      {d.steps.length>0&&<ol>{d.steps.map((st,i)=><li key={i}><b>{st.step}</b> · {st.ms} ms{st.action&&<span className="lai-muted"> · {st.action}</span>}</li>)}</ol>}
      {d.error&&<div className="is-bad">Lỗi: {d.error}</div>}
     </details>}
     <div className="lai-actions">
      {left!==null&&<button type="button" className="lai-btn" onClick={()=>void act(r,'cancel')}><X size={14}/> Huỷ phát</button>}
      {r.say&&<button type="button" className="lai-btn is-primary" onClick={()=>speak(r)}><Mic size={14}/> {r.voice==='sent'?'Phát lại':'Phát giọng'}</button>}
      {r.say&&r.voice!=='sent'&&<button type="button" className="lai-btn" onClick={()=>toggleEdit(r)}>{editing[r.id]!==undefined?'Bỏ sửa':'Sửa câu'}</button>}
      {r.say&&<button type="button" className="lai-btn" onClick={()=>preview(editing[r.id]??r.say)}><Volume2 size={14}/> Nghe thử</button>}
      {commentWaiting&&<button type="button" className="lai-btn" onClick={()=>void act(r,'comment/cancel')}><X size={14}/> Huỷ gửi comment</button>}
      {canSend&&<button type="button" className="lai-btn is-primary" onClick={()=>sendComment(r)}><Send size={14}/> Gửi comment</button>}
      {canSend&&<button type="button" className="lai-btn" onClick={()=>toggleCommentEdit(r)}>{commentEdit[r.id]!==undefined?'Bỏ sửa comment':'Sửa comment'}</button>}
      {commentText&&r.comment!=='done'&&<button type="button" className="lai-btn" onClick={()=>void copy(r)}><Copy size={14}/> Copy comment</button>}
      {r.voice!=='sent'&&!commentSent&&<button type="button" className="lai-btn is-ghost" onClick={()=>void act(r,'dismiss')}>Bỏ qua</button>}
     </div>
    </div>})}
  </div>
  {batches.length>0&&<details className="lai-block lai-quiet"><summary><span className="lai-block-title">Lượt gọi gần đây (token)</span></summary>
   <div className="lai-block-body">{batches.slice().reverse().map(b=><div key={b.batch_id} className="lai-hint">#{b.batch_id} · {b.product_name}: {b.comments} bình luận → {b.questions} câu hỏi (luật {b.sources.rule}, kho câu {b.sources.bank??b.sources.cache??0}, AI {b.sources.ai}){b.usage.map((u,i)=><span key={i}> · {u.input_tokens} in / {u.output_tokens} out · {u.latency_ms}ms</span>)}{b.skipped.length>0&&<span> · bỏ qua {b.skipped.length}: {b.skipped.map(x=>x.comment).join(', ')}</span>}{b.error&&<span className="is-bad"> · {b.error}</span>}</div>)}</div>
  </details>}
 </section>
}
