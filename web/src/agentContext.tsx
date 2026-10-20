/**
 * Which Agent this browser tab works with.
 *
 * The choice belongs to the TAB (sessionStorage), never to the account: two tabs of the same OWNER can drive two PCs
 * and switching here never changes what another tab or another browser does. Every Agent action sends this agent_id
 * and the Backend checks it belongs to the logged-in OWNER. A LIVE session always uses its own Agent instead.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Check, ChevronDown, MonitorPlay, MonitorX } from 'lucide-react'
import { apiRequest, type OwnerAgent } from './mediaApi'
import './agent-picker.css'

const STORAGE = 'sarae-agent-id'
const POLL_MS = 10000

type AgentContextValue = {
  agents:OwnerAgent[]
  /** The tab's Agent (null while loading or when the OWNER has none). */
  agent:OwnerAgent|null
  agentId:string|null
  loaded:boolean
  select:(id:string)=>void
  refresh:()=>Promise<void>
}

const AgentContext = createContext<AgentContextValue|null>(null)

const readStored = () => {try{return sessionStorage.getItem(STORAGE)}catch{return null}}
const writeStored = (id:string) => {try{sessionStorage.setItem(STORAGE, id)}catch{/* private mode: keep in memory only */}}

/** Stored choice if it still exists, else the first online Agent, else the first one. */
const pick = (agents:OwnerAgent[], wanted:string|null) =>
  agents.find(a=>a.id === wanted)?.id ?? agents.find(a=>a.online)?.id ?? agents[0]?.id ?? null

export function AgentProvider({children}:{children:ReactNode}){
  const [agents,setAgents] = useState<OwnerAgent[]>([])
  const [agentId,setAgentId] = useState<string|null>(readStored)
  const [loaded,setLoaded] = useState(false)
  const wanted = useRef(agentId)

  const refresh = useCallback(async ()=>{
    try{
      const {agents:list} = await apiRequest<{agents:OwnerAgent[]}>('/owner/agents')
      setAgents(list)
      setAgentId(pick(list, wanted.current))
    }catch{/* keep the last known list; pages show their own errors */}
    finally{setLoaded(true)}
  },[])
  useEffect(()=>{
    void refresh()
    const timer = window.setInterval(()=>void refresh(), POLL_MS)
    return ()=>window.clearInterval(timer)
  },[refresh])

  const select = useCallback((id:string)=>{wanted.current = id;writeStored(id);setAgentId(id)},[])
  const value = useMemo<AgentContextValue>(()=>({
    agents, agentId, loaded, select, refresh, agent:agents.find(a=>a.id === agentId) ?? null,
  }),[agents,agentId,loaded,select,refresh])
  return <AgentContext.Provider value={value}>{children}</AgentContext.Provider>
}

export function useAgent(){
  const value = useContext(AgentContext)
  if (!value) throw new Error('useAgent must be used inside <AgentProvider>')
  return value
}

/** Short state shown next to an Agent's name. */
export const agentState = (agent:OwnerAgent) =>
  !agent.online ? 'Offline' : agent.platform === 'ANDROID' ? (agent.playback_state ?? (agent.playing ? 'Đang phát' : 'Đang kết nối')) : agent.playing ? 'Đang phát' : agent.obs_connected ? 'OBS sẵn sàng' : 'Online · OBS chưa kết nối'

/** Topbar control: which Agent this tab works with. */
export function AgentPicker(){
  const {agents, agent, select, loaded} = useAgent()
  const [open,setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(()=>{
    if (!open) return
    const close = (event:MouseEvent) => {if (!box.current?.contains(event.target as Node)) setOpen(false)}
    const escape = (event:KeyboardEvent) => {if (event.key === 'Escape') setOpen(false)}
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', escape)
    return ()=>{document.removeEventListener('mousedown', close);document.removeEventListener('keydown', escape)}
  },[open])
  if (!loaded) return null
  if (!agents.length) return <a className="agent-picker is-empty" href="#/agents" title="Chưa có Agent nào được kích hoạt"><MonitorX size={16}/><span>Chưa có Agent</span></a>
  return <div className="agent-picker-wrap" ref={box}>
    <button type="button" className={'agent-picker' + (agent?.online ? ' is-online' : ' is-offline')} aria-haspopup="listbox" aria-expanded={open}
      onClick={()=>setOpen(!open)} title="Agent mà tab này đang điều khiển">
      <span className="agent-dot"/><MonitorPlay size={16}/>
      <span className="agent-picker-text"><strong>{agent?.hostname ?? 'Chọn Agent'}</strong><small>{agent ? `${agent.platform === 'ANDROID' ? 'Android' : 'Windows'} · ${agentState(agent)}` : ''}</small></span>
      <ChevronDown size={15}/>
    </button>
    {open && <div className="agent-menu" role="listbox" aria-label="Chọn Agent">
      <div className="agent-menu-head">Agent tab này điều khiển<small>Chỉ áp dụng cho tab hiện tại</small></div>
      {agents.map(a=><button type="button" role="option" aria-selected={a.id === agent?.id} key={a.id} className={a.id === agent?.id ? 'is-selected' : ''}
        onClick={()=>{select(a.id);setOpen(false)}}>
        <span className={'agent-dot' + (a.online ? ' is-online' : '')}/>
        <span className="agent-menu-text"><strong>{a.hostname}</strong><small>{agentState(a)}</small></span>
        {a.id === agent?.id && <Check size={15}/>}
      </button>)}
      <a className="agent-menu-link" href="#/agents" onClick={()=>setOpen(false)}>Quản lý Agents →</a>
    </div>}
  </div>
}

/** Renders `children(agent)` for the tab's Agent, or a hint when the OWNER has no Agent yet. */
export function WithAgent({children, purpose}:{children:(agent:OwnerAgent)=>ReactNode; purpose:string}){
  const {agent, loaded} = useAgent()
  if (!loaded) return null
  if (!agent) return <div className="agent-required">
    <MonitorX size={30}/>
    <strong>Chưa có Windows Agent</strong>
    <p>{purpose} cần một máy Windows đã cài Sarae Agent. Nhờ quản trị viên cấp mã kích hoạt, nhập mã trong ứng dụng Agent trên máy chạy OBS, rồi quay lại đây.</p>
    <a href="#/agents">Đến trang Windows Agents →</a>
  </div>
  if (agent.platform === 'ANDROID') return <div className="agent-required">
    <MonitorPlay size={30}/>
    <strong>Tab này cần Windows Agent</strong>
    <p>Agent Android đang được chọn. Chọn Windows Agent ở bộ chọn phía trên để dùng {purpose}; các lệnh Android nằm trong trang Agents.</p>
    <a href="#/agents">Đến trang Agents →</a>
  </div>
  return <>{children(agent)}</>
}
