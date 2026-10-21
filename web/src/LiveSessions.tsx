import { useCallback, useEffect, useMemo, useState, type DragEvent, type FormEvent } from 'react'
import { Alert, Badge, Button, Card, ConfirmModal, EmptyState, Input, Modal, SearchInput, SegmentedControl, Select, useToast } from './ui/core'
import {
  CalendarClock, Check, CheckCircle2, CircleAlert, CircleDashed, Clock3, Copy, GripVertical, History, ListPlus,
  MonitorPlay, Package, Pencil, Play, Plus, Radio, RotateCcw, Square, Trash2, Video,
} from 'lucide-react'
import { apiRequest, assetUrl, errorText, onAgent, type OwnerAgent } from './mediaApi'
import { useAgent } from './agentContext'
import {
  EVENT_LABEL, STATUS_META, dateTime, durationText, eventDetail, sessionIdFromHash, sessionsApi, timeOnly,
  type LiveProduct, type LiveSession, type SessionEvent, type SessionStatus,
} from './liveSessionsApi'
import MediaPrepare from './MediaPrepare'
import './live-sessions.css'

type LiveStatus = { connected:boolean; running:boolean; session_id?:number|null; product_id?:number|null; pending_product_id?:number|null }
type Filter = 'ALL' | SessionStatus
type TikTokAccount = { id:number; user_id:string; user_name:string; nick_name:string; user_type:number|null }
type Confirm = null | {kind:'finish'|'delete'|'close-stale'|'discard'; session:LiveSession; next?:number|null}

const POLL_MS = 3000
const STEPS:{status:SessionStatus;label:string;hint:string}[] = [
  {status:'DRAFT', label:'Chuẩn bị', hint:'Chọn & sắp xếp sản phẩm'},
  {status:'RUNNING', label:'Sarae đang phát', hint:'Điều khiển ở màn LIVE'},
  {status:'ENDED', label:'Kết thúc', hint:'Xem lại lịch sử'},
]

/** Plans LIVE sessions: build the product lineup, check readiness, start, and review history. Live switching happens in #/live. */
export default function LiveSessions(){
  const toast = useToast()
  const {agents:allAgents, agentId:tabAgentId, select:selectTabAgent} = useAgent()
  const agents = useMemo(()=>allAgents.filter(agent=>agent.platform !== 'ANDROID'),[allAgents])
  const agentById = useMemo(()=>new Map(agents.map(a=>[a.id,a])),[agents])
  // TikTok Accounts the user verified through a Console sync (account_info.user_id), not Seller IDs.
  const [createAccount,setCreateAccount] = useState('')
  const [accountChoice,setAccountChoice] = useState('')
  const [accounts,setAccounts] = useState<TikTokAccount[]>([])
  useEffect(()=>{void apiRequest<TikTokAccount[]>('/tiktok-catalog/accounts').then(setAccounts).catch(()=>setAccounts([]))},[])
  const accountLabel = (id:number|null) => {
    const a = accounts.find(x=>x.id === id)
    return !id ? 'Chưa chọn TikTok Account' : a ? `${a.nick_name || a.user_name || a.user_id}${a.user_type === 3 ? ' · Affiliate' : a.user_type === 1 ? ' · Seller' : ''}` : `TikTok Account #${id}`
  }
  const [sessions,setSessions] = useState<LiveSession[]>([])
  const [products,setProducts] = useState<LiveProduct[]>([])
  const [status,setStatus] = useState<LiveStatus|null>(null)
  const [events,setEvents] = useState<SessionEvent[]>([])
  const [loaded,setLoaded] = useState(false)
  const [selected,setSelected] = useState<number|null>(sessionIdFromHash)
  /** Unsaved lineup of the selected session; null when it matches the server. */
  const [draft,setDraft] = useState<number[]|null>(null)
  const [filter,setFilter] = useState<Filter>('ALL')
  const [query,setQuery] = useState('')
  const [busy,setBusy] = useState(false)
  const [createOpen,setCreateOpen] = useState(false)
  const [renameOpen,setRenameOpen] = useState(false)
  const [nameInput,setNameInput] = useState('')
  const [pickerOpen,setPickerOpen] = useState(false)
  const [confirm,setConfirm] = useState<Confirm>(null)
  const [startOpen,setStartOpen] = useState(false)
  const [startAgent,setStartAgent] = useState('')
  /** Products with their video count on the Agent chosen in the start dialog (videos live on each PC). */
  const [startProducts,setStartProducts] = useState<LiveProduct[]>([])
  const [consoleRows,setConsoleRows] = useState<Array<{product_id:string;seller_id:string;name:string;sarae_product_id:number|null;match_status:string}>|null>(null)
  const [consoleSelection,setConsoleSelection] = useState<number[]>([])
  const [consoleConfirmed,setConsoleConfirmed] = useState(false)
  // Every video of the session is on the chosen Agent (MediaPrepare); playback may only start then.
  const [mediaReady,setMediaReady] = useState(false)
  /** Product chosen to open the LIVE with; any product of the session that has videos. */
  const [opener,setOpener] = useState<number|null>(null)
  const [dragIndex,setDragIndex] = useState<number|null>(null)

  const loadSessions = useCallback(async ()=>{setSessions(await sessionsApi.list())},[])
  const loadAll = useCallback(async ()=>{setSessions(await sessionsApi.list());setLoaded(true)},[])
  useEffect(()=>{void loadAll().catch(e=>{setLoaded(true);toast.push({tone:'danger',title:'Không tải được phiên LIVE',description:errorText(e, '')})})},[loadAll,toast])
  // DRAFT catalog is owned by the User, not an Agent. RUNNING playback is Agent-specific.
  const selectedSession = sessions.find(s=>s.id === selected)
  const viewAgentId = selectedSession?.agent_id ?? tabAgentId
  useEffect(()=>{
    let active=true
    setStatus(null)
    if (selectedSession?.status === 'RUNNING' && selectedSession.agent_id) {
      const agentId=selectedSession.agent_id
      const poll=()=>void apiRequest<LiveStatus>(onAgent('/live/status',agentId))
        .then(s=>{if(active)setStatus(s)}).catch(()=>{if(active)setStatus(null)})
      void sessionsApi.products(agentId).then(p=>{if(active)setProducts(p)}).catch(()=>{if(active)setProducts([])})
      poll()
      const timer=window.setInterval(poll,POLL_MS)
      return ()=>{active=false;window.clearInterval(timer)}
    }
    void apiRequest<LiveProduct[]>('/live/catalog-products')
      .then(p=>{if(active)setProducts(p)}).catch(()=>{if(active)setProducts([])})
    return ()=>{active=false}
  },[selectedSession?.status,selectedSession?.agent_id,selected])
  // The running session's current product changes from the LIVE console; refresh when playback moves.
  useEffect(()=>{if (loaded) void loadSessions().catch(()=>{})},[loaded,loadSessions,status?.running,status?.session_id,status?.product_id,status?.pending_product_id])
  // Default selection: the running session, else the newest one.
  useEffect(()=>{
    if (selected != null && sessions.some(s=>s.id === selected)) return
    if (sessions.length) setSelected((sessions.find(s=>s.status === 'RUNNING') ?? sessions[0]).id)
  },[sessions,selected])

  const chosen = sessions.find(s=>s.id === selected)
  const chosenKey = chosen ? `${chosen.id}:${chosen.status}:${chosen.current_product_id}:${chosen.product_ids.join(',')}` : ''
  useEffect(()=>{
    if (!chosen) {setEvents([]);return}
    void sessionsApi.events(chosen.id).then(setEvents).catch(()=>setEvents([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[chosenKey])

  const productById = useMemo(()=>new Map(products.map(p=>[p.id,p])),[products])
  const startById = useMemo(()=>new Map(startProducts.map(p=>[p.id,p])),[startProducts])
  const loadStartProducts = useCallback(async (agentId:string)=>{
    try{setStartProducts(agentId ? await sessionsApi.products(agentId) : [])}catch{setStartProducts([])}
  },[])
  useEffect(()=>{if (startOpen) void loadStartProducts(startAgent)},[startOpen,startAgent,loadStartProducts])
  const lineup = draft ?? chosen?.product_ids ?? []
  const dirty = draft != null
  const editable = !!chosen && chosen.status !== 'ENDED'
  const owner = chosen?.status === 'RUNNING' && !!status?.running && status?.session_id === chosen.id
  // Several Agents can be LIVE at once; each RUNNING session is checked against its own Agent's last heartbeat.
  const playingOnAgent = (s:LiveSession) => {const a = agentById.get(s.agent_id ?? '');return !!a?.playing && a.session_id === s.id}
  const stale = sessions.filter(s=>s.status === 'RUNNING' && !playingOnAgent(s))
  const live = sessions.filter(s=>s.status === 'RUNNING' && playingOnAgent(s))
  const agentName = (id:string|null) => id ? agentById.get(id)?.hostname ?? 'Agent đã thu hồi' : 'Chưa chọn Agent'
  const chosenAgent:OwnerAgent|undefined = chosen ? agentById.get(chosen.agent_id ?? startAgent) : undefined
  const counts = {ALL:sessions.length, DRAFT:0, RUNNING:0, ENDED:0} as Record<Filter,number>
  sessions.forEach(s=>{counts[s.status]++})
  const visible = sessions.filter(s=>(filter === 'ALL' || s.status === filter) && s.name.toLocaleLowerCase('vi').includes(query.trim().toLocaleLowerCase('vi')))

  /** A RUNNING session resumes only on its own Agent, when that Agent is online, has OBS and plays nothing else. */
  const resumeBlock = (s:LiveSession) => {
    const a = agentById.get(s.agent_id ?? '')
    return !a ? 'Agent của phiên đã bị thu hồi' : !a.online ? `${a.hostname} đang offline` : a.playing ? `${a.hostname} đang phát nội dung khác`
      : !a.obs_connected ? `OBS trên ${a.hostname} chưa kết nối` : undefined
  }
  /** The LIVE console shows the tab's Agent: point the tab at the session's own PC before opening it. */
  const openConsole = (agentId:string|null) => {if (agentId) selectTabAgent(agentId);window.location.hash = '/live'}
  const resume = (session:LiveSession) => void run(async ()=>{await sessionsApi.resume(session);openConsole(session.agent_id)}, `Đã tiếp tục “${session.name}”`)
  /** Agents that can take a new LIVE now: online, OBS connected, not playing anything. */
  const freeAgents = agents.filter(a=>a.online && a.obs_connected && !a.playing)
  const openStart = () => {
    if (!chosen) return
    const preferred = chosen.agent_id ?? (freeAgents.some(a=>a.id === tabAgentId) ? tabAgentId : freeAgents[0]?.id) ?? ''
    setOpener(null);setConsoleRows(null);setConsoleSelection([]);setConsoleConfirmed(false);setStartAgent(preferred);setStartOpen(true)
  }
  const select = (id:number) => {
    if (id === selected) return
    if (dirty && chosen) {setConfirm({kind:'discard', session:chosen, next:id});return}
    setSelected(id);setDraft(null)
  }
  const run = async (action:()=>Promise<unknown>, success:string) => {
    setBusy(true)
    try{await action();toast.push({tone:'success',title:success});await loadAll()}
    catch(e){toast.push({tone:'danger',title:'Không thực hiện được',description:errorText(e, '')})}
    finally{setBusy(false)}
  }
  const edit = (next:number[]) => setDraft(chosen && next.join(',') === chosen.product_ids.join(',') ? null : next)
  const move = (from:number, to:number) => {
    if (to < 0 || to >= lineup.length || from === to) return
    const next = [...lineup];const [item] = next.splice(from, 1);next.splice(to, 0, item);edit(next)
  }
  const onDrop = (event:DragEvent, to:number) => {event.preventDefault();if (dragIndex != null) move(dragIndex, to);setDragIndex(null)}

  const openCreate = () => {setNameInput('');setCreateAccount(accounts.length===1?String(accounts[0].id):'');setCreateOpen(true)}
  const create = (event:FormEvent) => {
    event.preventDefault()
    const name = nameInput.trim()
    if (!name) return
    void run(async ()=>{
      const created = await sessionsApi.create(name, Number(createAccount))
      setCreateOpen(false);setSelected(created.id);setDraft(null);setFilter('ALL');setPickerOpen(true)
    }, 'Đã tạo phiên — thêm sản phẩm để chuẩn bị')
  }
  const rename = (event:FormEvent) => {
    event.preventDefault()
    if (!chosen || !nameInput.trim()) return
    void run(async ()=>{await sessionsApi.rename(chosen.id, nameInput.trim());setRenameOpen(false)}, 'Đã đổi tên phiên')
  }
  const save = () => chosen && draft && void run(async ()=>{await sessionsApi.saveProducts(chosen.id, draft);setDraft(null)}, 'Đã lưu danh sách sản phẩm')
  const confirmAction = () => {
    if (!confirm) return
    const {kind, session} = confirm
    setConfirm(null)
    if (kind === 'discard') {setSelected(confirm.next ?? null);setDraft(null);return}
    if (kind === 'finish' || kind === 'close-stale') void run(()=>sessionsApi.finish(session.id), kind === 'finish' ? 'Đã kết thúc phiên LIVE' : 'Đã đóng phiên bị treo')
    if (kind === 'delete') void run(async ()=>{await sessionsApi.remove(session.id);setSelected(null);setDraft(null)}, 'Đã xóa phiên')
  }

  // Readiness: what must be true before "Bắt đầu LIVE" (blocking) and what is only advised.
  const missingVideo = lineup.filter(id=>!productById.get(id)?.video_count)
  const readyIds = lineup.filter(id=>productById.get(id)?.video_count)
  // Opener in the start dialog: a product of the lineup with videos on the chosen Agent.
  const startReady = lineup.filter(id=>startById.get(id)?.video_count)
  const startOpener = opener != null && startReady.includes(opener) ? opener : startReady[0] ?? null
  const checks = [
    {ok:lineup.length > 0, label:'Có ít nhất 1 sản phẩm trong danh sách', blocking:true},
    {ok:readyIds.length > 0, label:'Có ít nhất 1 sản phẩm đã có video để phát', blocking:true, href:'#/cloud-media'},
    {ok:!dirty, label:'Đã lưu thay đổi danh sách', blocking:true},
    {ok:true, label:'Chọn Agent và xác minh LIVE Console trước khi phát', blocking:false},
    {ok:missingVideo.length === 0, label:missingVideo.length ? `${missingVideo.length} sản phẩm chưa có video (chưa đưa lên LIVE được)` : 'Mọi sản phẩm đều đã có video', blocking:false, href:'#/cloud-media'},
  ]
  const canStart = chosen?.status === 'DRAFT' && chosen.tiktok_account_id != null && checks.every(c=>c.ok || !c.blocking)

  const syncConsole = async () => {
    if (!chosen || !startAgent) return
    setBusy(true)
    try {
      await sessionsApi.prepare(chosen,startAgent)
      const snapshot=await apiRequest<{
        account_user_id:string|null;scanned_at:string|null;
        products:Array<{product_id:string;seller_id:string;name:string;sarae_product_id:number|null;match_status:string}>
      }>(`/tiktok-products/${encodeURIComponent(startAgent)}`)
      const expected=accounts.find(a=>a.id===chosen.tiktok_account_id)?.user_id
      if (!expected || snapshot.account_user_id!==expected)
        throw new Error('Tài khoản LIVE Console không trùng TikTok Account của phiên. Kiểm tra đăng nhập.')
      setConsoleRows(snapshot.products)
      // A sync can import new cart products and change video readiness: reload them for this Agent.
      const fresh = await sessionsApi.products(startAgent)
      setStartProducts(fresh)
      const videos = new Map(fresh.map(p=>[p.id,p.video_count]))
      const validIds=new Set(snapshot.products.map(row=>row.sarae_product_id).filter((v):v is number=>v!=null))
      // Pre-tick the prepared products that are in the cart and have videos; nothing else is added automatically.
      setConsoleSelection(chosen.product_ids.filter(id=>validIds.has(id)&&!!videos.get(id)))
      setConsoleConfirmed(false)
      toast.push({tone:'success',title:'Đã xác minh TikTok Account và đồng bộ giỏ sản phẩm'})
    } catch(e) {
      setConsoleRows(null);setConsoleConfirmed(false)
      toast.push({tone:'danger',title:'Không thể đồng bộ LIVE Console',description:errorText(e,'')})
    } finally {setBusy(false)}
  }
  const confirmConsole = async () => {
    if (!chosen || !consoleRows || !consoleSelection.length) return
    setBusy(true)
    try {
      await sessionsApi.saveProducts(chosen.id,consoleSelection)
      setDraft(null)
      await loadAll()
      setConsoleConfirmed(true)
      setOpener(consoleSelection[0])
      toast.push({tone:'success',title:'Đã xác nhận danh sách sản phẩm LIVE'})
    }catch(e){toast.push({tone:'danger',title:'Không lưu được sản phẩm',description:errorText(e,'')})}
    finally{setBusy(false)}
  }

  // Prepared products the confirmed selection would remove: shown and confirmed explicitly, never dropped silently.
  const dropped = chosen ? chosen.product_ids.filter(id=>!consoleSelection.includes(id)) : []
  const productName = (id:number) => startById.get(id)?.name ?? productById.get(id)?.name ?? `Sản phẩm #${id}`
  const switches = events.filter(e=>e.event_type === 'PRODUCT_SWITCHED').length

  return <div className="ls-page">
    {live.map(running=><div key={running.id} className="ls-live-banner" role="status">
      <span className="ls-live-dot" title="Trạng thái phát của Sarae trên OBS; TikTok LIVE thật được theo dõi riêng"><Radio size={16}/>SARAE ĐANG PHÁT</span>
      <div className="ls-live-text">
        <strong>{running.name}</strong>
        <small><MonitorPlay size={12}/> {agentName(running.agent_id)} · {durationText(running.started_at, null)}</small>
      </div>
      <Button size="sm" variant="outline" onClick={()=>select(running.id)}>Xem phiên</Button>
      <Button size="sm" leftIcon={<Radio size={15}/>} onClick={()=>openConsole(running.agent_id)}>Mở Điều khiển LIVE</Button>
    </div>)}
    {stale.length > 0 && <Alert tone="warning" title={`${stale.length} phiên LIVE bị ngắt`}>
      <span>Máy Windows của phiên không còn phát (Agent, OBS hoặc máy đã khởi động lại). Tiếp tục để phát lại sản phẩm đang LIVE trên đúng máy đó (giữ nguyên lịch sử), hoặc đóng nếu buổi LIVE đã xong.</span>
      {stale.map(s=><div key={s.id} className="ls-inline">
        <strong>{s.name} · {agentName(s.agent_id)}</strong>
        <Button size="sm" leftIcon={<Play size={14}/>} disabled={busy || !!resumeBlock(s)} title={resumeBlock(s)} onClick={()=>resume(s)}>Tiếp tục phiên</Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={()=>setConfirm({kind:'close-stale', session:s})}>Đóng phiên</Button>
      </div>)}
    </Alert>}

    <div className="ls-layout">
      {/* Session list */}
      <Card className="ls-list" padding={false} title={<span className="ls-card-title"><CalendarClock size={17}/>Danh sách phiên</span>}
        actions={<Button size="sm" leftIcon={<Plus size={15}/>} disabled={!accounts.length} title={accounts.length ? undefined : 'Mở LIVE Console trên Windows Agent và bấm Đồng bộ một lần để xác minh TikTok Account'} onClick={openCreate}>Tạo phiên</Button>}>
        <div className="ls-list-tools">
          <SegmentedControl<Filter> fullWidth value={filter} onChange={setFilter} items={[
            {value:'ALL', label:<>Tất cả<span className="ls-count">{counts.ALL}</span></>}, {value:'DRAFT', label:<>Chuẩn bị<span className="ls-count">{counts.DRAFT}</span></>},
            {value:'RUNNING', label:<>Đang phát<span className="ls-count">{counts.RUNNING}</span></>}, {value:'ENDED', label:<>Xong<span className="ls-count">{counts.ENDED}</span></>},
          ]}/>
          <SearchInput value={query} onChange={e=>setQuery(e.target.value)} placeholder="Tìm theo tên phiên…" aria-label="Tìm phiên"/>
        </div>
        <ul className="ls-sessions">
          {visible.map(s=>{
            // Video counts are only known for the Agent whose products are loaded.
            const ready = s.agent_id === viewAgentId ? s.product_ids.filter(id=>productById.get(id)?.video_count).length : null
            return <li key={s.id}><button type="button" className={'ls-session' + (s.id === selected ? ' is-selected' : '') + (s.status === 'RUNNING' ? ' is-live' : '')} onClick={()=>select(s.id)}>
              <span className="ls-session-top"><strong>{s.name}</strong><Badge tone={STATUS_META[s.status].tone} dot={s.status === 'RUNNING'}>{STATUS_META[s.status].label}</Badge></span>
              <span className="ls-session-meta">
                <span><Package size={13}/>{s.product_ids.length} sản phẩm</span>
                {s.status === 'DRAFT' && s.product_ids.length > 0 && ready != null && <span className={ready === s.product_ids.length ? 'is-ok' : 'is-warn'}><Video size={13}/>{ready}/{s.product_ids.length} có video</span>}
                <span><MonitorPlay size={13}/>{agentName(s.agent_id)}</span>
                <span title="TikTok Account phát LIVE">@{accountLabel(s.tiktok_account_id)}</span>
                <span><Clock3 size={13}/>{dateTime(s.status === 'DRAFT' ? s.created_at : s.started_at ?? s.created_at)}</span>
              </span>
            </button></li>
          })}
          {loaded && !visible.length && <li className="ls-list-empty">{sessions.length ? 'Không có phiên khớp bộ lọc.' : 'Chưa có phiên LIVE nào.'}</li>}
        </ul>
      </Card>

      {/* Session detail */}
      {!chosen ? <Card className="ls-detail-empty">
        <EmptyState icon={<CalendarClock size={30}/>} title={loaded ? 'Chưa chọn phiên LIVE' : 'Đang tải…'}
          description="Một phiên LIVE là danh sách sản phẩm sẽ bán, theo đúng thứ tự giới thiệu. Tạo phiên, thêm sản phẩm rồi bắt đầu phát."
          actionLabel={loaded && accounts.length ? 'Tạo phiên LIVE' : undefined} onAction={openCreate}/>
      </Card> : <div className="ls-detail">
        <Card className="ls-head" padding={false}>
          <div className="ls-head-main">
            <div className="ls-head-title">
              <span className="ls-head-id">PHIÊN #{chosen.id}</span>
              <h2>{chosen.name}
                {chosen.status !== 'ENDED' && <button type="button" className="ls-icon-btn" title="Đổi tên" aria-label="Đổi tên phiên" onClick={()=>{setNameInput(chosen.name);setRenameOpen(true)}}><Pencil size={15}/></button>}
              </h2>
              <div className="ls-head-meta">
                <Badge tone={STATUS_META[chosen.status].tone} dot={chosen.status === 'RUNNING'}>{STATUS_META[chosen.status].label}</Badge>
                <span title="TikTok Account phát LIVE">@{accountLabel(chosen.tiktok_account_id)}</span>
                <span className={'ls-agent' + (chosenAgent?.online ? ' is-online' : '')} title="Máy Windows phát phiên này"><MonitorPlay size={13}/>{chosen.status === 'DRAFT' && !chosen.agent_id ? 'Chọn Agent khi bắt đầu' : agentName(chosen.agent_id)}{chosen.agent_id && chosenAgent && !chosenAgent.online ? ' · offline' : ''}</span>
                {chosen.status === 'RUNNING' && <span title="Sarae chưa đọc được trạng thái LIVE thật từ TikTok">TikTok LIVE: chưa xác định</span>}
                <span>Tạo {dateTime(chosen.created_at)}</span>
                {chosen.started_at && <span>Bắt đầu {dateTime(chosen.started_at)}</span>}
                {chosen.ended_at && <span>Kết thúc {dateTime(chosen.ended_at)}</span>}
              </div>
            </div>
            <div className="ls-head-actions">
              {chosen.status === 'DRAFT' && <>
                <Button variant="ghost" leftIcon={<Trash2 size={15}/>} disabled={busy} onClick={()=>setConfirm({kind:'delete', session:chosen})}>Xóa</Button>
                <Button variant="outline" leftIcon={<Copy size={15}/>} disabled={busy} onClick={()=>void run(async ()=>{const c = await sessionsApi.duplicate(chosen.id);setSelected(c.id);setDraft(null)}, 'Đã nhân bản phiên')}>Nhân bản</Button>
                <Button leftIcon={<Play size={16}/>} disabled={busy || !canStart} title={canStart ? undefined : 'Hoàn tất các mục kiểm tra bên dưới'} onClick={openStart}>Bắt đầu LIVE</Button>
              </>}
              {chosen.status === 'RUNNING' && (owner ? <>
                <Button variant="danger" leftIcon={<Square size={15}/>} disabled={busy} onClick={()=>setConfirm({kind:'finish', session:chosen})}>Kết thúc phiên</Button>
                <Button leftIcon={<Radio size={16}/>} onClick={()=>openConsole(chosen.agent_id)}>Mở Điều khiển LIVE</Button>
              </> : <>
                <Button variant="outline" disabled={busy} onClick={()=>setConfirm({kind:'close-stale', session:chosen})}>Đóng phiên</Button>
                <Button leftIcon={<Play size={16}/>} disabled={busy || !!resumeBlock(chosen)} title={resumeBlock(chosen)} onClick={()=>resume(chosen)}>Tiếp tục phiên</Button>
              </>)}
              {chosen.status === 'ENDED' && <>
                <Button variant="ghost" leftIcon={<Trash2 size={15}/>} disabled={busy} onClick={()=>setConfirm({kind:'delete', session:chosen})}>Xóa</Button>
                <Button leftIcon={<RotateCcw size={15}/>} disabled={busy} onClick={()=>void run(async ()=>{const c = await sessionsApi.duplicate(chosen.id);setSelected(c.id);setDraft(null)}, 'Đã tạo phiên mới từ danh sách cũ')}>LIVE lại danh sách này</Button>
              </>}
            </div>
          </div>
          <ol className="ls-steps" aria-label="Tiến trình phiên">
            {STEPS.map((step,i)=>{
              const at = STEPS.findIndex(s=>s.status === chosen.status)
              const state = i < at || chosen.status === 'ENDED' ? 'done' : i === at ? 'current' : 'todo'
              return <li key={step.status} className={'is-' + state}>
                <span className="ls-step-dot">{state === 'done' ? <Check size={14}/> : i + 1}</span>
                <span><strong>{step.label}</strong><small>{step.hint}</small></span>
              </li>
            })}
          </ol>
        </Card>

        {chosen.status==='DRAFT' && <Card>
          <strong>{chosen.tiktok_account_id == null ? 'Phiên chưa chọn TikTok Account' : 'TikTok Account phát LIVE'}</strong>
          <p className="ls-muted">Phiên khóa theo tài khoản này: không phiên nào khác (của bạn hay User khác) phát cùng tài khoản đồng thời. Khi bắt đầu, Agent sẽ kiểm tra đúng tài khoản đang đăng nhập trên LIVE Console.</p>
          <div style={{display:'flex',gap:12,alignItems:'end',flexWrap:'wrap'}}>
            <div style={{flex:1,minWidth:200}}><Select label="TikTok Account" value={accountChoice || String(chosen.tiktok_account_id ?? '')} onChange={setAccountChoice}
              options={[...(chosen.tiktok_account_id == null ? [{value:'',label:'Chọn tài khoản…'}] : []),
                ...accounts.map(a=>({value:String(a.id),label:accountLabel(a.id)}))]}/></div>
            <Button variant="outline" disabled={!accountChoice || accountChoice === String(chosen.tiktok_account_id ?? '') || busy} onClick={()=>void run(async()=>{
              await apiRequest('/live/sessions/'+chosen.id,{method:'PATCH',headers:{'Content-Type':'application/json'},
                body:JSON.stringify({tiktok_account_id:Number(accountChoice)})})
              setAccountChoice('');setConsoleRows(null);setConsoleConfirmed(false)
            },'Đã đổi TikTok Account của phiên')}>Lưu tài khoản</Button>
          </div>
        </Card>}
        <div className="ls-detail-grid">
          {/* Product lineup */}
          <Card className="ls-lineup" padding={false} title={<span className="ls-card-title"><Package size={17}/>Danh sách sản phẩm</span>}
            description={chosen.status === 'ENDED' ? `${lineup.length} sản phẩm đã dùng trong phiên` : 'Sản phẩm chuẩn bị sẵn cho phiên · khi LIVE có thể đưa bất kỳ sản phẩm nào lên, không theo thứ tự'}
            actions={editable ? <Button size="sm" variant="outline" leftIcon={<ListPlus size={15}/>} disabled={busy||chosen.tiktok_account_id==null} onClick={()=>setPickerOpen(true)}>Thêm sản phẩm</Button> : undefined}>
            {!lineup.length
              ? <div className="ls-lineup-empty"><EmptyState icon={<Package size={28}/>} title="Chưa có sản phẩm" description="Thêm những sản phẩm dự định bán trong phiên này, theo thứ tự sẽ giới thiệu."
                actionLabel={editable&&chosen.tiktok_account_id!=null ? 'Thêm sản phẩm' : undefined} onAction={()=>{if(chosen.tiktok_account_id!=null)setPickerOpen(true)}}/></div>
              : <ol className="ls-rows">{lineup.map((pid,i)=>{
                const p = productById.get(pid)
                const isLive = chosen.status === 'RUNNING' && pid === (owner ? status?.product_id : chosen.current_product_id)
                const isPending = owner && pid === status?.pending_product_id
                return <li key={pid} className={'ls-row' + (isLive ? ' is-live' : '') + (dragIndex === i ? ' is-dragging' : '')}
                  draggable={editable} onDragStart={()=>setDragIndex(i)} onDragEnd={()=>setDragIndex(null)} onDragOver={e=>{if (editable) e.preventDefault()}} onDrop={e=>onDrop(e, i)}>
                  {editable && <span className="ls-grip" aria-hidden="true"><GripVertical size={16}/></span>}
                  <ProductImage product={p}/>
                  <span className="ls-row-text">
                    <strong>{p?.name ?? `Sản phẩm #${pid}`}</strong>
                    <small>{p?.sku || '—'}</small>
                  </span>
                  <span className="ls-row-tags">
                    {isLive && <Badge tone="danger" dot>Đang phát</Badge>}
                    {isPending && <Badge tone="warning">Chờ chuyển</Badge>}
                    {p?.video_count ? <Badge tone="success"><Video size={12}/>{p.video_count} video</Badge>
                      : <a className="ls-novideo" href="#/cloud-media" title="Gán video cho sản phẩm"><CircleAlert size={13}/>Chưa có video</a>}
                  </span>
                  {editable && <span className="ls-row-actions">
                    <button type="button" className="ls-icon-btn is-danger" title={isLive ? 'Không thể gỡ sản phẩm đang phát' : 'Gỡ khỏi phiên'} aria-label="Gỡ khỏi phiên"
                      disabled={busy || isLive || (chosen.status === 'RUNNING' && lineup.length === 1)} onClick={()=>edit(lineup.filter(id=>id !== pid))}><Trash2 size={15}/></button>
                  </span>}
                </li>
              })}</ol>}
            {dirty && <div className="ls-savebar">
              <span><CircleAlert size={15}/>Danh sách có thay đổi chưa lưu</span>
              <Button size="sm" variant="ghost" disabled={busy} onClick={()=>setDraft(null)}>Hoàn tác</Button>
              <Button size="sm" loading={busy} onClick={save}>Lưu danh sách</Button>
            </div>}
          </Card>

          <div className="ls-side">
            {chosen.status === 'DRAFT' && <Card className="ls-checks" title={<span className="ls-card-title"><CheckCircle2 size={17}/>Sẵn sàng LIVE?</span>}
              description={canStart ? 'Mọi điều kiện bắt buộc đã đạt.' : 'Hoàn tất các mục bắt buộc để bắt đầu.'}>
              <ul className="ls-checklist">{checks.map(c=><li key={c.label} className={c.ok ? 'is-ok' : c.blocking ? 'is-bad' : 'is-warn'}>
                {c.ok ? <CheckCircle2 size={16}/> : c.blocking ? <CircleDashed size={16}/> : <CircleAlert size={16}/>}
                <span>{c.label}{!c.ok && c.href && <> · <a href={c.href}>{c.href === '#/live' ? 'Kết nối' : c.href === '#/agents' ? 'Xem Agent' : 'Gán video'}</a></>}</span>
              </li>)}</ul>
        
            </Card>}
            {chosen.status !== 'DRAFT' && <Card className="ls-summary" title={<span className="ls-card-title"><Clock3 size={17}/>Tóm tắt</span>}>
              <dl className="ls-stats">
                <div><dt>Thời lượng</dt><dd>{durationText(chosen.started_at, chosen.ended_at)}</dd></div>
                <div><dt>Sản phẩm</dt><dd>{lineup.length}</dd></div>
                <div><dt>Lần chuyển SP</dt><dd>{switches}</dd></div>
                <div><dt>{chosen.status === 'RUNNING' ? 'Đang phát' : 'SP cuối'}</dt><dd className="ls-ellipsis">{productById.get((owner ? status?.product_id : chosen.current_product_id) ?? -1)?.name ?? '—'}</dd></div>
              </dl>
            </Card>}
            <Card className="ls-history" padding={false} title={<span className="ls-card-title"><History size={17}/>Lịch sử phiên</span>} description={`${events.length} sự kiện`}>
              {!events.length ? <p className="ls-muted ls-pad">Chưa có sự kiện.</p>
                : <ol className="ls-timeline">{events.slice().reverse().map(ev=><li key={ev.id} className={'ev-' + ev.event_type}>
                  <span className="ls-tl-dot"/>
                  <div>
                    <strong>{EVENT_LABEL[ev.event_type] ?? ev.event_type}{ev.product_id != null && <em> · {productById.get(ev.product_id)?.name ?? '#' + ev.product_id}</em>}</strong>
                    {ev.detail && <small>{eventDetail(ev.detail)}</small>}
                  </div>
                  <time title={dateTime(ev.created_at)}>{timeOnly(ev.created_at)}</time>
                </li>)}</ol>}
            </Card>
          </div>
        </div>
      </div>}
    </div>

    <Modal open={createOpen} title="Tạo phiên LIVE" size="sm" onClose={()=>!busy && setCreateOpen(false)} footer={<>
      <Button variant="ghost" disabled={busy} onClick={()=>setCreateOpen(false)}>Hủy</Button>
      <Button type="submit" form="ls-create-form" loading={busy} disabled={!nameInput.trim() || !createAccount}>Tạo & thêm sản phẩm</Button>
    </>}>
      <form id="ls-create-form" className="ls-create-form" onSubmit={create}>
        <Input label="Tên phiên" autoFocus required maxLength={200} value={nameInput} onChange={e=>setNameInput(e.target.value)} placeholder="VD: LIVE thời trang tối 20/10" hint="Bước tiếp theo: chọn sản phẩm sẽ bán và sắp xếp thứ tự."/>
        <Select label="TikTok Account" value={createAccount} onChange={setCreateAccount} options={[{value:'',label:'Chọn tài khoản…'},...accounts.map(a=>({value:String(a.id),label:accountLabel(a.id)}))]} hint="Seller và Affiliate dùng chung TikTok Account. Windows Agent được chọn khi bắt đầu."/>
      </form>
    </Modal>

    <Modal open={renameOpen} title="Đổi tên phiên" size="sm" onClose={()=>!busy && setRenameOpen(false)} footer={<>
      <Button variant="ghost" disabled={busy} onClick={()=>setRenameOpen(false)}>Hủy</Button>
      <Button type="submit" form="ls-rename-form" loading={busy} disabled={!nameInput.trim()}>Lưu</Button>
    </>}>
      <form id="ls-rename-form" onSubmit={rename}><Input label="Tên phiên" autoFocus required maxLength={200} value={nameInput} onChange={e=>setNameInput(e.target.value)}/></form>
    </Modal>

    {pickerOpen && chosen && <ProductPicker products={products} exclude={lineup} onClose={()=>setPickerOpen(false)}
      onAdd={ids=>{edit([...lineup, ...ids]);setPickerOpen(false)}}/>}

    <ConfirmModal open={!!confirm} loading={busy} onClose={()=>setConfirm(null)} onConfirm={confirmAction}
      tone={confirm?.kind === 'discard' ? 'primary' : 'danger'}
      {...(confirm ? confirmText(confirm) : {})}/>

    {startOpen && chosen && <Modal open title={`Bắt đầu “${chosen.name}”`} size="md" onClose={()=>{if (!busy) setStartOpen(false)}} footer={<>
      <Button variant="ghost" disabled={busy} onClick={()=>setStartOpen(false)}>Hủy</Button>
      <Button leftIcon={<Play size={16}/>} loading={busy} disabled={startOpener == null || !startAgent || !consoleConfirmed || !mediaReady || busy}
        onClick={()=>void run(async ()=>{await sessionsApi.start(chosen, startAgent, startOpener ?? undefined);setStartOpen(false);openConsole(startAgent)}, 'Sarae đã bắt đầu phát')}>Bắt đầu phát</Button>
    </>}>
      <div className="ls-picker">
        {chosen.agent_id
          ? <Alert tone="warning">Phiên đang giữ máy <b>{agentName(chosen.agent_id)}</b> từ lần bắt đầu trước (chưa rõ kết quả). Tiếp tục trên máy này, hoặc đóng hộp thoại và bấm Kết thúc để hủy lần bắt đầu đó.</Alert>
          : freeAgents.length
            ? <Select label="Windows Agent thực thi" value={startAgent} onChange={v=>{setStartAgent(v);setConsoleRows(null);setConsoleConfirmed(false)}}
                options={[{value:'',label:'Chọn máy…'},...freeAgents.map(a=>({value:a.id,label:a.hostname}))]}
                hint={agents.length > freeAgents.length ? `${agents.length - freeAgents.length} máy khác đang offline, chưa kết nối OBS hoặc đang phát nên không chọn được.` : undefined}/>
            : <Alert tone="danger">Không có máy Windows nào rảnh (online, đã kết nối OBS, không phát nội dung khác).</Alert>}
        <Alert tone="info">Mở TikTok LIVE Console trên máy này và đăng nhập đúng TikTok Account, sau đó nhấn Đồng bộ giỏ sản phẩm. Chỉ khi xác nhận danh sách mới được phát.</Alert>
        <Button variant="outline" disabled={!startAgent || busy} loading={busy} onClick={()=>void syncConsole()}>Đồng bộ giỏ sản phẩm từ Agent</Button>
        {consoleRows && <div className="ls-console-review">
          <strong>Giỏ TikTok vừa đồng bộ: {consoleRows.length} sản phẩm</strong>
          <p className="ls-muted">Tích những sản phẩm muốn phát. Sản phẩm chưa liên kết hoặc chưa có video không được chọn.</p>
          <ul className="ls-picker-list">{consoleRows.map(row=>{
            const id=row.sarae_product_id
            const available=id!=null && !!startById.get(id)?.video_count
            const checked=id!=null && consoleSelection.includes(id)
            return <li key={row.seller_id+':'+row.product_id}>
              <label style={{display:'flex',gap:10,alignItems:'center',padding:8}}>
                <input type="checkbox" checked={checked} disabled={!available||busy||consoleConfirmed}
                  onChange={e=>{if(id==null)return;setConsoleSelection(prev=>e.target.checked?[...prev,id]:prev.filter(v=>v!==id))}}/>
                <span>{row.name} {available ? `(${startById.get(id!)?.video_count} video trong Thư viện)` : id == null ? '— chưa có trong Sarae' : '— chưa có video trong Thư viện'}</span>
              </label>
            </li>
          })}</ul>
          {!consoleConfirmed && dropped.length > 0 && <Alert tone="warning" title={`${dropped.length} sản phẩm đã chuẩn bị sẽ bị bỏ khỏi phiên`}>
            {dropped.map(productName).join(', ')} — không có trong giỏ LIVE vừa đồng bộ, chưa có video trong Thư viện, hoặc bạn đã bỏ tích.
          </Alert>}
          {consoleConfirmed
            ? <MediaPrepare key={`${chosen.id}:${startAgent}`} sessionId={chosen.id} agentId={startAgent} hostname={agentName(startAgent)} onReady={setMediaReady}/>
            : <Button disabled={!consoleSelection.length||busy} loading={busy} onClick={()=>void confirmConsole()}>{dropped.length ? `Xác nhận (bỏ ${dropped.length} sản phẩm) & lưu danh sách` : 'Xác nhận & lưu danh sách LIVE'}</Button>}
        </div>}
        {consoleConfirmed && <p className="ls-muted">Chọn sản phẩm phát đầu tiên. Trong lúc LIVE bạn chuyển sang sản phẩm nào cũng được, kể cả sản phẩm chưa có trong danh sách.</p>}
        {consoleConfirmed && <ul className="ls-picker-list">{lineup.map(pid=>{
          const p = startById.get(pid)
          return <li key={pid}><button type="button" className={startOpener === pid ? 'is-picked' : ''} disabled={!p?.video_count} aria-pressed={startOpener === pid} onClick={()=>setOpener(pid)}>
            <span className="ls-check ls-radio">{startOpener === pid && <Check size={13}/>}</span>
            <ProductImage product={p}/>
            <span className="ls-row-text"><strong>{p?.name ?? `Sản phẩm #${pid}`}</strong><small>{p?.sku || '—'}</small></span>
            {p?.video_count ? <Badge tone="success">{p.video_count} video</Badge> : <Badge tone="warning">Chưa có video</Badge>}
          </button></li>
        })}</ul>}
        <Alert tone="info">Sarae bắt đầu phát video trên OBS; việc TikTok có đang LIVE thật hay không được theo dõi riêng. Ghim sản phẩm trên TikTok LIVE. Sau khi bắt đầu bạn sẽ được chuyển sang màn Điều khiển LIVE của đúng máy này.</Alert>
      </div>
    </Modal>}
  </div>
}

function confirmText({kind, session}:NonNullable<Confirm>){
  switch (kind) {
    case 'finish': return {title:'Kết thúc phiên LIVE?', description:`OBS sẽ dừng phát và “${session.name}” được lưu vào lịch sử. Không thể tiếp tục phiên sau khi kết thúc.`, confirmLabel:'Kết thúc phiên'}
    case 'close-stale': return {title:'Đóng phiên bị ngắt?', description:`“${session.name}” không còn phát trên OBS. Phiên sẽ được đánh dấu Đã kết thúc; OBS không bị ảnh hưởng.`, confirmLabel:'Đóng phiên'}
    case 'delete': return {title:'Xóa phiên?', description:`Xóa “${session.name}” cùng danh sách sản phẩm và lịch sử. Không thể hoàn tác.`, confirmLabel:'Xóa phiên'}
    case 'discard': return {title:'Bỏ thay đổi chưa lưu?', description:`Danh sách sản phẩm của “${session.name}” có thay đổi chưa lưu.`, confirmLabel:'Bỏ thay đổi'}
  }
}

function ProductImage({product}:{product?:LiveProduct}){
  const [failed,setFailed] = useState(false)
  return <span className="ls-thumb">{product?.image_url && !failed ? <img src={assetUrl(product.image_url) ?? undefined} alt="" loading="lazy" onError={()=>setFailed(true)}/> : <Package size={18}/>}</span>
}

/** Multi-select dialog that appends products to the lineup in the order they are ticked. */
function ProductPicker({products,exclude,onClose,onAdd}:{products:LiveProduct[];exclude:number[];onClose:()=>void;onAdd:(ids:number[])=>void}){
  const [query,setQuery] = useState('')
  const [picked,setPicked] = useState<number[]>([])
  const q = query.trim().toLocaleLowerCase('vi')
  const options = products.filter(p=>!exclude.includes(p.id) && (p.name + ' ' + p.sku).toLocaleLowerCase('vi').includes(q))
  const toggle = (id:number) => setPicked(prev=>prev.includes(id) ? prev.filter(x=>x !== id) : [...prev, id])
  return <Modal open title="Thêm sản phẩm vào phiên" size="md" onClose={onClose} footer={<>
    <span className="ls-picker-count">Đã chọn {picked.length}</span>
    <Button variant="ghost" onClick={onClose}>Hủy</Button>
    <Button disabled={!picked.length} onClick={()=>onAdd(picked)}>Thêm {picked.length || ''} sản phẩm</Button>
  </>}>
    <div className="ls-picker">
      <SearchInput autoFocus value={query} onChange={e=>setQuery(e.target.value)} placeholder="Tìm theo tên hoặc SKU…" aria-label="Tìm sản phẩm"/>
      <ul className="ls-picker-list">{options.map(p=>{
        const order = picked.indexOf(p.id)
        return <li key={p.id}><button type="button" className={order >= 0 ? 'is-picked' : ''} disabled={!p.video_count || p.status==='INACTIVE'} onClick={()=>toggle(p.id)} aria-pressed={order >= 0}>
          <span className="ls-check">{order >= 0 ? order + 1 : ''}</span>
          <ProductImage product={p}/>
          <span className="ls-row-text"><strong>{p.name}</strong><small>{p.sku || '—'}</small></span>
          {p.video_count ? <Badge tone="success">{p.video_count} video</Badge> : <Badge tone="warning">Chưa có video</Badge>}
        </button></li>
      })}{!options.length && <li className="ls-list-empty">{!products.length?'Chưa có sản phẩm đồng bộ từ TikTok LIVE Console.':products.every(p=>exclude.includes(p.id))?'Tất cả sản phẩm đã có trong phiên.':'Không có sản phẩm khớp.'}</li>}</ul>
      <p className="ls-muted">Số trên mỗi ô là thứ tự sẽ thêm vào cuối danh sách. Sản phẩm chưa có video hoặc đang tạm ẩn vẫn hiển thị để kiểm tra, nhưng chưa thể chọn. Hãy gắn video cho sản phẩm trong Thư viện video.</p>
    </div>
  </Modal>
}
