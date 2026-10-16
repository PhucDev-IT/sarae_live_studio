import {useCallback,useEffect,useRef,useState} from 'react'
import {MessageSquare, Radio, RefreshCw, Square, Wifi, WifiOff} from 'lucide-react'
import {apiRequest, errorText} from './mediaApi'
import './tiktok-chat.css'
import LiveAIBatches from './LiveAIBatches'

type Trace={id:number;timestamp:string;stage:string;decision:string;text:string;comment_id:number|null;username:string|null}
type Processing={last_id:number;selected:number;duplicates:number;spam_suppressed:number;mode:string;logs:Trace[]}
type ChatMessage={id:number;username:string;nickname:string;comment:string;timestamp:string}
type ChatStatus={username:string;status:'idle'|'connecting'|'connected'|'stopping'|'disconnected'|'error';error:string;room_id:string|null;last_id:number;messages:ChatMessage[]}
const EMPTY:ChatStatus={username:'',status:'idle',error:'',room_id:null,last_id:0,messages:[]}

export default function TikTokChat(){
  const [value,setValue]=useState('')
  const [status,setStatus]=useState<ChatStatus>(EMPTY)
  const [messages,setMessages]=useState<ChatMessage[]>([])
  const [busy,setBusy]=useState(false)
  const [traces,setTraces]=useState<Trace[]>([])
  const [processing,setProcessing]=useState<Processing|null>(null)
  const traceRef=useRef(0)
  const [error,setError]=useState('')
  const lastRef=useRef(0)
  const request=useCallback(async()=>{
    const data=await apiRequest<ChatStatus>(`/tiktok-chat/status?after=${lastRef.current}`)
    setStatus(data)
    const p=await apiRequest<Processing>(`/tiktok-chat/processing?after=${traceRef.current}`)
    setProcessing(p)
    if(p.last_id<traceRef.current){traceRef.current=0;setTraces(p.logs);traceRef.current=p.last_id}
    else if(p.logs.length){traceRef.current=p.last_id;setTraces(old=>[...old,...p.logs.filter(x=>!old.some(y=>y.id===x.id))].slice(-400))}
    if(data.last_id < lastRef.current){lastRef.current=0;setMessages(data.messages);lastRef.current=data.last_id}
    else if(data.messages.length){lastRef.current=data.last_id;setMessages(old=>[...old,...data.messages.filter(x=>!old.some(y=>y.id===x.id))].slice(-300))}
  },[])
  useEffect(()=>{
    let mounted=true
    const tick=()=>{if(mounted)void request().catch(e=>{if(mounted)setError(errorText(e,'Không tải được bình luận'))})}
    tick()
    const timer=window.setInterval(tick,1500)
    return ()=>{mounted=false;window.clearInterval(timer)}
  },[request])
  const action=async(mode:'connect'|'disconnect')=>{
    setBusy(true);setError('')
    try{
      const next=await apiRequest<ChatStatus>(`/tiktok-chat/${mode}`,{method:'POST',headers:mode==='connect'?{'Content-Type':'application/json'}:undefined,body:mode==='connect'?JSON.stringify({username:value.trim()}):undefined})
      setStatus(next);setMessages([]);lastRef.current=0;setTraces([]);traceRef.current=0
    }catch(e){setError(errorText(e,'Kết nối thất bại'))}finally{setBusy(false)}
  }
  const active=status.status==='connecting'||status.status==='connected'||status.status==='stopping'
  const fmt=(date:string)=>new Date(date).toLocaleTimeString('vi-VN')
  return <div className="tc-page">
    <section className="tc-panel">
      <div className="tc-header"><div><h3><Radio size={19}/> TikTok LIVE Comment Monitor</h3><p>Thử đọc bình luận công khai của bất kỳ tài khoản đang LIVE. Không cần đăng nhập TikTok.</p></div><span className={'tc-state '+status.status}>{status.status==='connected'?<><Wifi size={15}/> Đã kết nối</>:status.status==='connecting'?'Đang kết nối…':status.status==='stopping'?'Đang ngắt…':<><WifiOff size={15}/> {status.status==='error'?'Lỗi kết nối':'Chưa kết nối'}</>}</span></div>
      <form className="tc-form" onSubmit={e=>{e.preventDefault();void action('connect')}}>
        <label htmlFor="tc-username">Username TikTok hoặc link hồ sơ</label>
        <div className="tc-input-row"><input id="tc-username" value={value} onChange={e=>setValue(e.target.value)} disabled={active||busy} placeholder="@tenkenh hoặc https://www.tiktok.com/@tenkenh" autoComplete="off"/><button className="tc-primary" disabled={active||busy||!value.trim()}><Wifi size={16}/> Kết nối</button><button type="button" onClick={()=>void action('disconnect')} disabled={!active||busy}><Square size={15}/> Ngắt</button></div>
      </form>
      <div className="tc-meta"><span>Trạng thái: <b>{status.status}</b></span><span>Kênh: <b>{status.username?'@'+status.username:'—'}</b></span><span>Room ID: <b>{status.room_id||'—'}</b></span><button type="button" onClick={()=>void request().catch(e=>setError(errorText(e,'Không tải được')))}><RefreshCw size={14}/> Làm mới</button></div>
      {(error||status.error)&&<div className="tc-error" role="alert">{error||status.error}</div>}
      <p className="tc-note">Đây là kết nối không chính thức qua TikTokLive. Kênh phải đang LIVE; nếu TikTok giới hạn truy cập, kết nối có thể không thành công. Công cụ chỉ đọc comment, không gửi tin nhắn hay ghim sản phẩm.</p>
    </section>
    <section className="tc-panel">
      <div className="tc-chat-title"><h3><MessageSquare size={19}/> Bình luận realtime</h3><span>{messages.length} bình luận đã nhận trong bộ nhớ</span></div>
      <div className="tc-feed" aria-live="polite">{messages.length===0?<div className="tc-empty">Chưa có bình luận. Hãy kết nối tới một kênh đang LIVE và chờ bình luận mới.</div>:messages.map(m=><div className="tc-message" key={m.id}><span className="tc-avatar">{(m.nickname||m.username||'?').slice(0,1).toUpperCase()}</span><div className="tc-comment"><div><b>{m.nickname||m.username||'Người xem'}</b> <small>@{m.username||'unknown'} · {fmt(m.timestamp)}</small></div><p>{m.comment}</p></div></div>)}</div>
    </section>
    <LiveAIBatches/>
    <section className="tc-panel">
      <div className="tc-chat-title"><h3>Processing Monitor — Event Terminal</h3><span>Chế độ: {processing?.mode==='rules_only_no_ai'?'Quy tắc, chưa kết nối AI':'—'}</span></div>
      <div className="tc-meta" style={{marginTop:12}}><span>Đã chọn: <b>{processing?.selected??0}</b></span><span>Event trùng: <b>{processing?.duplicates??0}</b></span><span>Chặn AI do spam: <b>{processing?.spam_suppressed??0}</b></span></div>
      <div className="tc-terminal" role="log" aria-label="Processing trace">{traces.length===0?<p>Chưa có event xử lý.</p>:traces.slice().reverse().map(t=><div className="tc-trace" key={t.id}><time>{fmt(t.timestamp)}</time> <b>[{t.stage}]</b> <em>{t.decision}</em> <span>#{t.comment_id??'?'} @{t.username||'?'} — {t.text}</span></div>)}</div>
      <p className="tc-note">Log chạy từ Backend thật. Dedup chỉ xác nhận được khi TikTok cung cấp ID sự kiện; chống spam giới hạn cùng username và nội dung trong 20 giây. Khi bật AI realtime, câu trả lời thật sẽ hiển thị trong Batch Monitor; giọng nói hiện chỉ cho nghe thử tại máy.</p>
    </section>
  </div>
}
