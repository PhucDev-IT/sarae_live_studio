import { useCallback, useEffect, useRef, useState } from 'react'
import { Bot, ListChecks, MessageSquare, MessagesSquare, RefreshCw, Square, Wifi, WifiOff } from 'lucide-react'
import { Button, Card, SegmentedControl } from './ui/core'
import { apiRequest, errorText, onAgent, type OwnerAgent } from './mediaApi'
import LiveAIBatches from './LiveAIBatches'
import './session-live-chat.css'

type ChatMessage = { id:number; username:string; nickname:string; comment:string; timestamp:string }
type ChatState = 'idle'|'connecting'|'connected'|'stopping'|'disconnected'|'error'
/** GET/POST /live/sessions/{id}/chat…: the comment reader of one LIVE session (Backend connects it on start, stops it on end). */
type SessionChat = { session_id:number; username:string; agent_id:string|null; status:ChatState; error:string; room_id:string|null; last_id:number; messages:ChatMessage[] }
type Trace = { id:number; timestamp:string; stage:string; decision:string; text:string; comment_id:number|null; username:string|null }
type Processing = { last_id:number; selected:number; duplicates:number; spam_suppressed:number; mode:string; logs:Trace[] }
type Tab = 'comments'|'ai'|'log'

const POLL_MS = 1500
const time = (date:string) => {const d = new Date(date);return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString('vi-VN')}
/** Merges a polled page into the list: a smaller last_id than our cursor means the reader restarted, so start over. */
function merge<T extends {id:number}>(old:T[], page:T[], lastId:number, cursor:{current:number}, keep:number):T[]{
  if (lastId < cursor.current) {cursor.current = lastId;return page.slice(-keep)}
  cursor.current = Math.max(cursor.current, lastId)
  if (!page.length) return old
  const seen = new Set(old.map(x=>x.id))
  return [...old, ...page.filter(x=>!seen.has(x.id))].slice(-keep)
}

/** Comments and AI replies of a RUNNING LIVE session: the TikTok handle comes from the session's TikTok Account. */
export default function SessionLiveChat({sessionId, handle, agent}:{sessionId:number; handle:string; agent?:OwnerAgent}){
  const [chat,setChat] = useState<SessionChat|null>(null)
  const [messages,setMessages] = useState<ChatMessage[]>([])
  const [error,setError] = useState('')
  const [busy,setBusy] = useState(false)
  const [tab,setTab] = useState<Tab>('comments')
  const cursor = useRef(0)

  const apply = useCallback((data:SessionChat)=>{setChat(data);setMessages(old=>merge(old, data.messages, data.last_id, cursor, 300))},[])
  const poll = useCallback(async ()=>{
    apply(await apiRequest<SessionChat>(`/live/sessions/${sessionId}/chat?after=${cursor.current}`))
    setError('')
  },[sessionId,apply])
  useEffect(()=>{
    let mounted = true
    cursor.current = 0
    const tick = () => void poll().catch(e=>{if (mounted) setError(errorText(e, 'Không tải được bình luận'))})
    tick()
    const timer = window.setInterval(tick, POLL_MS)
    return ()=>{mounted = false;window.clearInterval(timer)}
  },[poll])

  const action = async (mode:'connect'|'disconnect') => {
    setBusy(true);setError('')
    try {
      const data = await apiRequest<SessionChat>(`/live/sessions/${sessionId}/chat/${mode}`, {method:'POST'})
      cursor.current = 0;setMessages([]);apply(data)
    } catch(e) {setError(errorText(e, mode === 'connect' ? 'Không kết nối được bình luận' : 'Không ngắt được'))}
    finally {setBusy(false)}
  }

  const state:ChatState = chat?.status ?? 'connecting'
  const name = chat?.username || handle
  const active = state === 'connecting' || state === 'connected' || state === 'stopping'
  const pill = state === 'connected' ? {tone:'ok', icon:<Wifi size={14}/>, text:`Đang nhận bình luận @${name}`}
    : state === 'connecting' ? {tone:'wait', icon:<RefreshCw size={14} className="slc-spin"/>, text:'Đang kết nối…'}
    : state === 'stopping' ? {tone:'wait', icon:<RefreshCw size={14} className="slc-spin"/>, text:'Đang ngắt…'}
    : state === 'error' ? {tone:'bad', icon:<WifiOff size={14}/>, text:'Lỗi kết nối'}
    : {tone:'off', icon:<WifiOff size={14}/>, text:'Mất kết nối'}
  const problem = error || (state === 'error' ? chat?.error || 'TikTok không cho đọc bình luận lúc này.' : '')

  return <Card className="slc" padding={false}>
    <div className="slc-head">
      <div className="slc-title">
        <h3><MessagesSquare size={18}/>Bình luận & AI trả lời</h3>
        <p>Sarae tự đọc bình luận của <b>@{name || 'tài khoản phiên'}</b> và gợi ý câu trả lời. Không cần nhập gì thêm.</p>
      </div>
      <div className="slc-state">
        <span className={'slc-pill is-' + pill.tone} role="status">{pill.icon}{pill.text}</span>
        {active
          ? <Button size="sm" variant="ghost" leftIcon={<Square size={13}/>} disabled={busy || state === 'stopping'} onClick={()=>void action('disconnect')}>Ngắt</Button>
          : <Button size="sm" variant="outline" leftIcon={<RefreshCw size={13}/>} loading={busy} onClick={()=>void action('connect')}>Kết nối lại</Button>}
      </div>
    </div>
    {problem && <div className="slc-error" role="alert">{problem}</div>}

    <div className="slc-tabs">
      <SegmentedControl<Tab> value={tab} onChange={setTab} items={[
        {value:'comments', label:<><MessageSquare size={14}/>Bình luận<span className="slc-count">{messages.length}</span></>},
        {value:'ai', label:<><Bot size={14}/>AI trả lời</>},
        {value:'log', label:<><ListChecks size={14}/>Nhật ký</>},
      ]}/>
    </div>

    <div className="slc-body">
      {tab === 'comments' && <CommentFeed messages={messages} state={state} name={name}/>}
      {/* Kept mounted while hidden: replies being edited and the recent batches survive switching tabs. */}
      <div hidden={tab !== 'ai'}>{agent ? <LiveAIBatches agent={agent}/> : <NoAgent/>}</div>
      {tab === 'log' && (agent ? <ProcessingLog agentId={agent.id}/> : <NoAgent/>)}
    </div>
  </Card>
}

function NoAgent(){
  return <div className="slc-empty"><strong>Không tìm thấy máy phát của phiên</strong><span>Máy Windows của phiên đã bị thu hồi hoặc chưa tải xong. Thử tải lại trang.</span></div>
}

/** Live comment feed: newest at the bottom, follows new comments unless the user scrolled up to read. */
function CommentFeed({messages, state, name}:{messages:ChatMessage[]; state:ChatState; name:string}){
  const box = useRef<HTMLDivElement>(null)
  const stick = useRef(true)
  useEffect(()=>{const el = box.current;if (el && stick.current) el.scrollTop = el.scrollHeight},[messages])
  if (!messages.length) return <div className="slc-empty">
    <MessageSquare size={26}/>
    <strong>{state === 'connected' ? 'Chưa có bình luận mới' : 'Chưa nhận được bình luận'}</strong>
    <span>{state === 'connected'
      ? `Đã kết nối @${name}. Bình luận mới của người xem sẽ hiện ở đây ngay khi có.`
      : `Kênh @${name || '…'} cần đang LIVE trên TikTok. Nếu đã LIVE mà vẫn trống, bấm “Kết nối lại” ở góc trên.`}</span>
  </div>
  return <div className="slc-feed" ref={box} aria-live="polite" onScroll={e=>{const el = e.currentTarget;stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60}}>
    {messages.map(m=>{
      const who = m.nickname || m.username || 'Người xem'
      return <div className="slc-msg" key={m.id}>
        <span className="slc-avatar" aria-hidden="true">{who.slice(0,1).toUpperCase()}</span>
        <div className="slc-msg-body">
          <div className="slc-msg-who"><b>{who}</b>{m.username && <small>@{m.username}</small>}<time>{time(m.timestamp)}</time></div>
          <p>{m.comment}</p>
        </div>
      </div>
    })}
  </div>
}

const STAGE:Record<string,string> = {RECEIVED:'Nhận', DEDUP:'Lọc trùng', ANTI_SPAM:'Chống spam', SELECT:'Chọn'}
const DECISION:Record<string,{label:string; tone:string}> = {
  accepted:{label:'Đã nhận', tone:'ok'}, unique_event:{label:'Không trùng', tone:'ok'}, duplicate_event:{label:'Trùng, bỏ qua', tone:'muted'},
  unverified:{label:'Không kiểm tra được', tone:'warn'}, suppressed:{label:'Spam, bỏ qua', tone:'warn'}, pass:{label:'Hợp lệ', tone:'ok'},
  selected:{label:'Được chọn', tone:'ok'}, queued_if_enabled:{label:'Chờ AI', tone:'ok'},
}
/** What happened to each comment (GET /tiktok-chat/processing on the session's Agent), newest first. */
function ProcessingLog({agentId}:{agentId:string}){
  const [traces,setTraces] = useState<Trace[]>([])
  const [info,setInfo] = useState<Processing|null>(null)
  const [error,setError] = useState('')
  const cursor = useRef(0)
  useEffect(()=>{
    let mounted = true
    cursor.current = 0
    const tick = () => void apiRequest<Processing>(onAgent(`/tiktok-chat/processing?after=${cursor.current}`, agentId)).then(p=>{
      if (!mounted) return
      setInfo(p);setError('');setTraces(old=>merge(old, p.logs, p.last_id, cursor, 400))
    }).catch(e=>{if (mounted) setError(errorText(e, 'Không tải được nhật ký'))})
    tick()
    const timer = window.setInterval(tick, POLL_MS)
    return ()=>{mounted = false;window.clearInterval(timer)}
  },[agentId])
  return <div className="slc-log">
    <p className="slc-hint">Nhật ký kỹ thuật: Sarae đã làm gì với từng bình luận. Chỉ cần xem khi có điều gì không như ý.</p>
    <div className="slc-log-stats">
      <span>Đã chọn trả lời <b>{info?.selected ?? 0}</b></span>
      <span>Bình luận trùng <b>{info?.duplicates ?? 0}</b></span>
      <span>Chặn do spam <b>{info?.spam_suppressed ?? 0}</b></span>
    </div>
    {error && <div className="slc-error" role="alert">{error}</div>}
    {!traces.length ? <div className="slc-empty"><span>Chưa có bình luận nào được xử lý.</span></div>
      : <ol className="slc-log-list">{traces.slice().reverse().map(t=><li key={t.id}>
        <time>{time(t.timestamp)}</time>
        <span className="slc-stage">{STAGE[t.stage] ?? t.stage}</span>
        <span className="slc-log-text">{t.username && <b>@{t.username} </b>}{t.text}</span>
        {t.decision && <span className={'slc-decision is-' + (DECISION[t.decision]?.tone ?? 'muted')}>{DECISION[t.decision]?.label ?? t.decision}</span>}
      </li>)}</ol>}
  </div>
}
