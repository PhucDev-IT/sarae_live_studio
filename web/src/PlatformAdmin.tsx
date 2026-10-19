import { useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import {
  Alert, Badge, Button, ConfirmModal, EmptyState, Input, Modal, PasswordInput, SearchInput, SegmentedControl, Sheet, Switch, Table,
  ThemeProvider, ToastProvider, useToast, type TableColumn,
} from './ui/core'
import {
  CalendarClock, CalendarPlus, ChartNoAxesCombined, CheckCircle2, ChevronLeft, ChevronRight, CircleAlert, Clock3, Copy, HardDrive, KeyRound,
  LogOut, Menu, Moon, Plus, RefreshCcw, ScanEye, Settings2, ShieldCheck, ShieldOff, Sun, Trash2, Unplug, UploadCloud, UserPlus, Users, Wand2, Wifi, WifiOff, X,
} from 'lucide-react'
import { api } from './mediaApi'
import { clearAuth, getAuthToken } from './AuthGate'
import { IconButton, Stat } from './CatalogParts'
import './platform-admin.css'

type Stats = { owners_total:number; owners_active:number; licenses_enabled:number; licenses_expiring_7d:number; agents_total:number; agents_online:number; agents_offline:number; agents_revoked:number }
type Owner = { id:string; email:string; active:number; enabled:number; max_agents:number; expires_at:string|null }
type Registered = { id:string; owner_id:string; hostname:string; enabled:number; created_at:string }
type Heartbeat = { agent_id:string; hostname:string; online:boolean; last_seen:string; seconds_ago:number }
type LicenseState = 'active'|'expiring'|'expired'|'locked'
type Tab = 'dashboard'|'customers'|'licenses'|'agents'|'captcha'|'settings'
type OwnerRow = Owner & { state:LicenseState; daysLeft:number|null; agentsUsed:number } & Record<string, unknown>
type AgentRow = Registered & { ownerEmail:string; online:boolean; lastSeen:string|null } & Record<string, unknown>

const NAV:{id:Tab;title:string;icon:typeof Users;hint:string}[] = [
  {id:'dashboard', title:'Tổng quan', icon:ChartNoAxesCombined, hint:'Sức khỏe nền tảng và các việc cần chú ý.'},
  {id:'customers', title:'Khách hàng', icon:Users, hint:'Tài khoản OWNER, số máy đang dùng và mã kích hoạt.'},
  {id:'licenses', title:'License & Gói', icon:ShieldCheck, hint:'Thời hạn license, gia hạn nhanh và khóa / mở.'},
  {id:'agents', title:'Windows Agents', icon:HardDrive, hint:'Thiết bị đã kích hoạt trên toàn hệ thống. Chỉ giám sát, không điều khiển LIVE.'},
  {id:'captcha', title:'Mẫu CAPTCHA', icon:ScanEye, hint:'Ảnh mẫu để Agent nhận diện CAPTCHA. Agent tự tải khi khởi động; khách hàng không thấy mục này.'},
  {id:'settings', title:'Cấu hình', icon:Settings2, hint:'Thông tin nền tảng và chính sách phân quyền.'},
]
const STATE_META:Record<LicenseState,{label:string;tone:'success'|'warning'|'danger'|'default'}> = {
  active:{label:'Đang hoạt động', tone:'success'}, expiring:{label:'Sắp hết hạn', tone:'warning'},
  expired:{label:'Hết hạn', tone:'danger'}, locked:{label:'Đã khóa', tone:'default'},
}
const DAY = 86_400_000
const REFRESH_MS = 30_000
const EMPTY_STATS:Stats = {owners_total:0, owners_active:0, licenses_enabled:0, licenses_expiring_7d:0, agents_total:0, agents_online:0, agents_offline:0, agents_revoked:0}

const readTab = ():Tab => {const id = window.location.hash.replace('#/admin/', '') as Tab;return NAV.some(n=>n.id === id) ? id : 'dashboard'}
const dateLabel = (iso:string|null) => iso ? new Date(iso).toLocaleDateString('vi-VN', {day:'2-digit', month:'2-digit', year:'numeric'}) : 'Không giới hạn'
const ago = (iso:string|null) => {
  if (!iso) return 'Chưa kết nối'
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  return s < 60 ? 'vừa xong' : s < 3600 ? `${Math.floor(s / 60)} phút trước` : s < 86400 ? `${Math.floor(s / 3600)} giờ trước` : `${Math.floor(s / 86400)} ngày trước`
}
/** Date input value (local) <-> ISO at the end of that local day, so a license is valid through the chosen date. */
const toDateInput = (iso:string|null) => {if (!iso) return '';const d = new Date(iso);return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`}
const fromDateInput = (value:string) => value ? new Date(`${value}T23:59:59`).toISOString() : null
const addMonths = (from:string|null, months:number) => {
  const base = from && new Date(from).getTime() > Date.now() ? new Date(from) : new Date()
  base.setMonth(base.getMonth() + months)
  return toDateInput(base.toISOString())
}
const randomPassword = () => {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789'
  return Array.from(crypto.getRandomValues(new Uint32Array(12)), n=>chars[n % chars.length]).join('')
}
function licenseState(o:Owner):{state:LicenseState;daysLeft:number|null}{
  const daysLeft = o.expires_at ? Math.ceil((new Date(o.expires_at).getTime() - Date.now()) / DAY) : null
  if (!o.enabled || !o.active) return {state:'locked', daysLeft}
  if (daysLeft != null && daysLeft < 0) return {state:'expired', daysLeft}
  if (daysLeft != null && daysLeft <= 7) return {state:'expiring', daysLeft}
  return {state:'active', daysLeft}
}

async function call<T>(path:string, init:RequestInit = {}):Promise<T>{
  const res = await fetch(api + '/api' + path, {...init, headers:{'Content-Type':'application/json', Authorization:'Bearer ' + getAuthToken(), ...init.headers}})
  if (res.status === 401) {clearAuth();throw new Error('Phiên đăng nhập đã hết hạn')}
  const data = await res.json().catch(()=>({}))
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Yêu cầu thất bại (HTTP ${res.status})`)
  return data as T
}
const body = (data:unknown, method = 'POST'):RequestInit => ({method, body:JSON.stringify(data)})

/** Console for the platform operator (SUPER_ADMIN): customers, licenses and the Windows Agents they activate. */
export default function PlatformAdmin({email,logout}:{email:string;logout:()=>void}){
  const [dark,setDark] = useState(()=>{try{return localStorage.getItem('sarae-theme') === 'dark'}catch{return false}})
  useEffect(()=>{
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
    try{localStorage.setItem('sarae-theme', dark ? 'dark' : 'light')}catch{/* preference only */}
  },[dark])
  return <ThemeProvider value={{mode:dark ? 'dark' : 'light', primary:dark ? '#a898ff' : '#6553d7', density:'comfortable', radius:'soft', fontSize:'normal'}}>
    <ToastProvider><Console email={email} logout={logout} dark={dark} toggleDark={()=>setDark(d=>!d)}/></ToastProvider>
  </ThemeProvider>
}

function Console({email,logout,dark,toggleDark}:{email:string;logout:()=>void;dark:boolean;toggleDark:()=>void}){
  const toast = useToast()
  const [tab,setTab] = useState<Tab>(readTab)
  const [collapsed,setCollapsed] = useState(false)
  const [drawer,setDrawer] = useState(false)
  const [stats,setStats] = useState<Stats>(EMPTY_STATS)
  const [owners,setOwners] = useState<Owner[]>([])
  const [registered,setRegistered] = useState<Registered[]>([])
  const [beats,setBeats] = useState<Heartbeat[]>([])
  const [loading,setLoading] = useState(true)
  const [error,setError] = useState('')
  const [refreshedAt,setRefreshedAt] = useState<Date|null>(null)
  const [creating,setCreating] = useState(false)
  const [editing,setEditing] = useState<OwnerRow|null>(null)
  const [issued,setIssued] = useState<{email:string;activationKey:string;password?:string}|null>(null)
  const [confirm,setConfirm] = useState<null|{title:string;description:string;label:string;run:()=>Promise<void>}>(null)
  const [busy,setBusy] = useState(false)

  const load = useCallback(async ()=>{
    setLoading(true);setError('')
    try{
      const [s, o, r, b] = await Promise.all([
        call<Stats>('/identity/platform/summary'), call<Owner[]>('/identity/owners'),
        call<Registered[]>('/identity/agents'), call<{agents:Heartbeat[]}>('/agents/status'),
      ])
      setStats(s);setOwners(o);setRegistered(r);setBeats(b.agents);setRefreshedAt(new Date())
    }catch(e){setError(e instanceof Error ? e.message : 'Không tải được dữ liệu quản trị')}
    finally{setLoading(false)}
  },[])
  useEffect(()=>{void load();const t = window.setInterval(()=>void load(), REFRESH_MS);return ()=>window.clearInterval(t)},[load])
  useEffect(()=>{const sync = ()=>setTab(readTab());window.addEventListener('hashchange', sync);return ()=>window.removeEventListener('hashchange', sync)},[])
  const go = (next:Tab) => {setTab(next);window.location.hash = `/admin/${next}`;setDrawer(false)}

  const beatById = useMemo(()=>new Map(beats.map(b=>[b.agent_id,b])),[beats])
  const ownerRows:OwnerRow[] = useMemo(()=>owners.map(o=>({
    ...o, ...licenseState(o), agentsUsed:registered.filter(a=>a.owner_id === o.id && a.enabled).length,
  })),[owners,registered])
  const ownerById = useMemo(()=>new Map(ownerRows.map(o=>[o.id,o])),[ownerRows])
  const agentRows:AgentRow[] = useMemo(()=>registered.map(a=>{
    const beat = beatById.get(a.id)
    return {...a, ownerEmail:ownerById.get(a.owner_id)?.email ?? '—', online:!!a.enabled && !!beat?.online, lastSeen:beat?.last_seen ?? null}
  }).sort((x,y)=>Number(y.online) - Number(x.online) || (y.lastSeen ?? '').localeCompare(x.lastSeen ?? '')),[registered,beatById,ownerById])

  const act = async (run:()=>Promise<unknown>, success:string) => {
    setBusy(true)
    try{await run();toast.push({tone:'success', title:success});await load();return true}
    catch(e){toast.push({tone:'danger', title:'Không thực hiện được', description:e instanceof Error ? e.message : ''});return false}
    finally{setBusy(false)}
  }
  const saveLicense = (o:Owner, patch:{max_agents?:number;expires_at?:string|null;enabled?:boolean}, success:string) =>
    act(()=>call(`/identity/owners/${o.id}/license`, body({max_agents:patch.max_agents ?? o.max_agents, expires_at:patch.expires_at !== undefined ? patch.expires_at : o.expires_at, enabled:Boolean(patch.enabled ?? o.enabled)}, 'PUT')), success)
  const issueKey = async (o:{id:string;email:string}, password?:string) => {
    setBusy(true)
    try{const r = await call<{activation_key:string}>(`/identity/owners/${o.id}/activation-keys`, body({}));setIssued({email:o.email, activationKey:r.activation_key, password})}
    catch(e){toast.push({tone:'danger', title:'Không cấp được mã', description:e instanceof Error ? e.message : ''})}
    finally{setBusy(false)}
  }
  const askToggle = (o:OwnerRow) => setConfirm(o.enabled ? {
    title:`Khóa license của ${o.email}?`, label:'Khóa license',
    description:'Agent của khách sẽ không kích hoạt thêm được và các lệnh mới bị từ chối cho tới khi mở lại.',
    run:async ()=>{await saveLicense(o, {enabled:false}, 'Đã khóa license')},
  } : {title:`Mở lại license của ${o.email}?`, label:'Mở license', description:'Khách hàng dùng lại được Agent trong thời hạn license.', run:async ()=>{await saveLicense(o, {enabled:true}, 'Đã mở license')}})
  const askRevoke = (a:AgentRow) => setConfirm({
    title:`Thu hồi Agent “${a.hostname}”?`, label:'Thu hồi',
    description:`Máy này của ${a.ownerEmail} sẽ mất quyền kết nối ngay ở lần heartbeat kế tiếp. Không thể hoàn tác — khách cần mã kích hoạt mới.`,
    run:async ()=>{await act(()=>call(`/identity/agents/${a.id}`, {method:'DELETE'}), 'Đã thu hồi Agent')},
  })

  const current = NAV.find(n=>n.id === tab)!
  const attention = ownerRows.filter(o=>o.state === 'expiring' || o.state === 'expired').length
  const renderNav = (compact:boolean) => <>
    <div className="brand">
      <div className="brand-icon pa-brand-icon"><ShieldCheck size={20} strokeWidth={2.2}/></div>
      {!compact && <div className="brand-label"><strong>SARAE</strong><span>PLATFORM ADMIN</span></div>}
      <button className="sidebar-toggle desktop-only" onClick={()=>setCollapsed(c=>!c)} aria-label={collapsed ? 'Mở rộng sidebar' : 'Thu gọn sidebar'}>{collapsed ? <ChevronRight size={17}/> : <ChevronLeft size={17}/>}</button>
    </div>
    <div className="nav-scroller"><div className="nav-group">
      {!compact && <div className="nav-heading">SUPER ADMIN</div>}
      {NAV.map(item=><button key={item.id} className={`nav-item ${tab === item.id ? 'active' : ''}`} onClick={()=>go(item.id)} title={compact ? item.title : undefined}>
        <item.icon size={19} strokeWidth={1.85}/>
        {!compact && <span>{item.title}</span>}
        {!compact && item.id === 'licenses' && attention > 0 && <span className="pa-nav-count">{attention}</span>}
        {!compact && tab === item.id && <span className="active-indicator"/>}
      </button>)}
    </div></div>
    <div className="sidebar-footer">
      <div className="sidebar-divider"/>
      {!compact && <div className="pa-me"><span className="avatar">{email.slice(0, 1).toUpperCase()}</span><span><strong title={email}>{email}</strong><small>Super admin</small></span></div>}
      <button className="nav-item footer-help" onClick={logout} title="Đăng xuất"><LogOut size={19}/>{!compact && <span>Đăng xuất</span>}</button>
    </div>
  </>

  return <div className={`shell pa-shell ${collapsed ? 'is-collapsed' : ''}`}>
    <aside className="sidebar">{renderNav(collapsed)}</aside>
    {drawer && <div className="mobile-overlay" onClick={()=>setDrawer(false)}><aside className="mobile-sidebar" onClick={e=>e.stopPropagation()}>
      <div className="drawer-heading"><strong>Sarae Platform</strong><button className="icon-button" onClick={()=>setDrawer(false)} aria-label="Đóng menu"><X size={20}/></button></div>
      {renderNav(false)}
    </aside></div>}
    <div className="workspace">
      <header className="topbar">
        <div className="topbar-left">
          <button className="icon-button mobile-menu" onClick={()=>setDrawer(true)} aria-label="Mở menu"><Menu size={21}/></button>
          <span className="breadcrumb-root">Platform</span><ChevronRight size={15} className="breadcrumb-chevron"/><span className="breadcrumb-active">{current.title}</span>
        </div>
        <div className="topbar-actions">
          <span className="pa-refreshed"><Clock3 size={14}/>{refreshedAt ? `Cập nhật ${refreshedAt.toLocaleTimeString('vi-VN', {hour:'2-digit', minute:'2-digit'})}` : 'Đang tải…'}</span>
          <button className="icon-button" onClick={()=>void load()} disabled={loading} aria-label="Làm mới" title="Làm mới (tự động mỗi 30 giây)"><RefreshCcw size={18} className={loading ? 'pa-spin' : ''}/></button>
          <span className="topbar-separator"/>
          <button className="icon-button" onClick={toggleDark} aria-label={dark ? 'Chế độ sáng' : 'Chế độ tối'}>{dark ? <Sun size={19}/> : <Moon size={19}/>}</button>
          <span className="avatar" title={email}>{email.slice(0, 1).toUpperCase()}</span>
        </div>
      </header>
      <main className="main"><div className="page-content">
        <div className="page-heading">
          <div><div className="eyebrow">SARAE PLATFORM <span className="eyebrow-line"/></div><h1>{current.title}</h1><p>{current.hint}</p></div>
          {(tab === 'dashboard' || tab === 'customers') && <Button leftIcon={<UserPlus size={17}/>} onClick={()=>setCreating(true)}>Tạo khách hàng</Button>}
        </div>
        {error && <Alert tone="danger" title="Không tải được dữ liệu">{error}</Alert>}
        <div className="pa-page">
          {tab === 'dashboard' && <Dashboard stats={stats} owners={ownerRows} agents={agentRows} loaded={!!refreshedAt} go={go} onEdit={setEditing}/>}
          {tab === 'customers' && <Customers rows={ownerRows} loaded={!!refreshedAt} onCreate={()=>setCreating(true)} onEdit={setEditing} onKey={o=>void issueKey(o)} onToggle={askToggle} busy={busy}/>}
          {tab === 'licenses' && <Licenses rows={ownerRows} loaded={!!refreshedAt} busy={busy} onEdit={setEditing} onToggle={askToggle}
            onExtend={(o,m)=>void saveLicense(o, {expires_at:fromDateInput(addMonths(o.expires_at, m))}, `Đã gia hạn ${m} tháng cho ${o.email}`)}/>}
          {tab === 'agents' && <Agents rows={agentRows} loaded={!!refreshedAt} onRevoke={askRevoke}/>}
          {tab === 'captcha' && <CaptchaTemplates/>}
          {tab === 'settings' && <SettingsView email={email}/>}
        </div>
      </div>
      <footer className="footer"><span>© 2026 Sarae Live Studio · Platform Admin</span><span>Tự động làm mới mỗi 30 giây</span></footer>
      </main>
    </div>

    {creating && <CreateOwner busy={busy} onClose={()=>setCreating(false)} onCreated={async (o, password, withKey)=>{
      setCreating(false);toast.push({tone:'success', title:`Đã tạo ${o.email}`});await load()
      if (withKey) await issueKey(o, password)
    }}/>}
    {editing && <LicenseSheet owner={ownerById.get(editing.id) ?? editing} agents={agentRows.filter(a=>a.owner_id === editing.id)} busy={busy}
      onClose={()=>setEditing(null)} onKey={()=>void issueKey(editing)} onRevoke={askRevoke}
      onSave={async patch=>{if (await saveLicense(editing, patch, 'Đã lưu license')) setEditing(null)}}/>}
    {issued && <IssuedKey {...issued} onClose={()=>setIssued(null)}/>}
    <ConfirmModal open={!!confirm} loading={busy} title={confirm?.title} description={confirm?.description} confirmLabel={confirm?.label} tone="danger"
      onClose={()=>setConfirm(null)} onConfirm={()=>{const c = confirm;setConfirm(null);void c?.run()}}/>
  </div>
}

function Dashboard({stats,owners,agents,loaded,go,onEdit}:{stats:Stats;owners:OwnerRow[];agents:AgentRow[];loaded:boolean;go:(t:Tab)=>void;onEdit:(o:OwnerRow)=>void}){
  const watch = owners.filter(o=>o.state === 'expiring' || o.state === 'expired').sort((a,b)=>(a.daysLeft ?? 0) - (b.daysLeft ?? 0))
  const full = owners.filter(o=>o.state === 'active' && o.agentsUsed >= o.max_agents)
  const onlinePct = stats.agents_total ? Math.round(stats.agents_online / stats.agents_total * 100) : 0
  return <>
    <section className="pp-stats">
      <Stat icon={<Users size={18}/>} label="Khách hàng" value={stats.owners_total} hint={`${stats.owners_active} tài khoản hoạt động`} tone="violet" onClick={()=>go('customers')} actionTitle="Xem khách hàng"/>
      <Stat icon={<ShieldCheck size={18}/>} label="License hiệu lực" value={stats.licenses_enabled} hint={stats.licenses_expiring_7d ? `${stats.licenses_expiring_7d} sắp hết hạn trong 7 ngày` : 'không có license sắp hết hạn'} tone={stats.licenses_expiring_7d ? 'peach' : 'blue'} onClick={()=>go('licenses')} actionTitle="Xem license"/>
      <Stat icon={<Wifi size={18}/>} label="Agent online" value={stats.agents_online} hint={`trên ${stats.agents_total} Agent đang cấp quyền`} tone="mint" onClick={()=>go('agents')} actionTitle="Xem Agent"/>
      <Stat icon={<WifiOff size={18}/>} label="Agent offline" value={stats.agents_offline} hint={`${stats.agents_revoked} đã thu hồi`} tone="muted" onClick={()=>go('agents')} actionTitle="Xem Agent"/>
    </section>
    <div className="pa-grid">
      <section className="pa-card">
        <header className="pa-card-head"><h3><CircleAlert size={17}/>Cần chú ý</h3><span>{watch.length + full.length} mục</span></header>
        {!loaded ? <p className="pa-muted">Đang tải…</p> : !watch.length && !full.length
          ? <div className="pa-allgood"><CheckCircle2 size={26}/><strong>Mọi thứ ổn</strong><span>Không có license sắp hết hạn hay khách dùng hết số máy.</span></div>
          : <ul className="pa-list">
            {watch.map(o=><li key={o.id}>
              <span className={'pa-dot ' + (o.state === 'expired' ? 'is-danger' : 'is-warn')}/>
              <span className="pa-list-text"><strong>{o.email}</strong><small>{o.state === 'expired' ? `License đã hết hạn ${dateLabel(o.expires_at)}` : `Hết hạn sau ${o.daysLeft} ngày · ${dateLabel(o.expires_at)}`}</small></span>
              <Button size="sm" variant="outline" leftIcon={<CalendarPlus size={14}/>} onClick={()=>onEdit(o)}>Gia hạn</Button>
            </li>)}
            {full.map(o=><li key={o.id + '-full'}>
              <span className="pa-dot is-info"/>
              <span className="pa-list-text"><strong>{o.email}</strong><small>Đã dùng hết {o.max_agents}/{o.max_agents} máy — không kích hoạt thêm được</small></span>
              <Button size="sm" variant="outline" onClick={()=>onEdit(o)}>Nâng gói</Button>
            </li>)}
          </ul>}
      </section>
      <section className="pa-card">
        <header className="pa-card-head"><h3><HardDrive size={17}/>Agent</h3><button type="button" className="pa-link" onClick={()=>go('agents')}>Xem tất cả →</button></header>
        <div className="pa-meter" aria-label={`${onlinePct}% Agent online`}><span style={{width:`${onlinePct}%`}}/></div>
        <p className="pa-meter-text"><b>{onlinePct}%</b> Agent đang online · {stats.agents_online}/{stats.agents_total}</p>
        {!agents.length ? <p className="pa-muted">{loaded ? 'Chưa có Agent nào được kích hoạt.' : 'Đang tải…'}</p>
          : <ul className="pa-list">{agents.slice(0, 6).map(a=><li key={a.id}>
            <span className={'pa-dot ' + (!a.enabled ? 'is-off' : a.online ? 'is-ok' : 'is-idle')}/>
            <span className="pa-list-text"><strong>{a.hostname}</strong><small>{a.ownerEmail}</small></span>
            <span className="pa-when">{!a.enabled ? 'Đã thu hồi' : a.online ? 'Online' : ago(a.lastSeen)}</span>
          </li>)}</ul>}
      </section>
    </div>
  </>
}

function StateBadge({row}:{row:OwnerRow}){
  return <Badge tone={STATE_META[row.state].tone} dot={row.state === 'active'}>{STATE_META[row.state].label}</Badge>
}
function Usage({used,max}:{used:number;max:number}){
  const pct = Math.min(100, Math.round(used / Math.max(1, max) * 100))
  return <span className="pa-usage"><span className="pa-usage-bar"><span className={pct >= 100 ? 'is-full' : ''} style={{width:`${pct}%`}}/></span><small>{used}/{max} máy</small></span>
}
function Toolbar({children}:{children:ReactNode}){return <div className="pp-toolbar">{children}</div>}

function Customers({rows,loaded,busy,onCreate,onEdit,onKey,onToggle}:{rows:OwnerRow[];loaded:boolean;busy:boolean;onCreate:()=>void;onEdit:(o:OwnerRow)=>void;onKey:(o:OwnerRow)=>void;onToggle:(o:OwnerRow)=>void}){
  const [query,setQuery] = useState('')
  const [filter,setFilter] = useState<'ALL'|LicenseState>('ALL')
  const q = query.trim().toLowerCase()
  const visible = rows.filter(o=>(filter === 'ALL' || o.state === filter) && (!q || o.email.includes(q)))
  const columns:TableColumn<OwnerRow>[] = [
    {key:'email', header:'Khách hàng', render:o=><button type="button" className="pa-owner" onClick={()=>onEdit(o)}>
      <span className="pa-initial">{o.email.slice(0, 1).toUpperCase()}</span><span><strong>{o.email}</strong><small>ID {o.id.slice(0, 8)}</small></span>
    </button>},
    {key:'state', header:'License', render:o=><StateBadge row={o}/>},
    {key:'agents', header:'Thiết bị', render:o=><Usage used={o.agentsUsed} max={o.max_agents}/>},
    {key:'expires_at', header:'Hết hạn', render:o=><span className="pa-date">{dateLabel(o.expires_at)}{o.daysLeft != null && o.daysLeft >= 0 && <small>còn {o.daysLeft} ngày</small>}</span>},
    {key:'actions', header:'Thao tác', align:'right', render:o=><div className="pp-row-actions">
      <Button size="sm" variant="outline" leftIcon={<KeyRound size={14}/>} disabled={busy || o.state === 'locked' || o.state === 'expired'} onClick={()=>onKey(o)}>Cấp mã</Button>
      <IconButton title="Sửa license" onClick={()=>onEdit(o)}><Settings2 size={15}/></IconButton>
      <IconButton title={o.enabled ? 'Khóa license' : 'Mở license'} danger={!!o.enabled} onClick={()=>onToggle(o)}>{o.enabled ? <ShieldOff size={15}/> : <ShieldCheck size={15}/>}</IconButton>
    </div>},
  ]
  return <section className="pp-panel">
    <Toolbar>
      <div className="pp-search"><SearchInput value={query} onChange={e=>setQuery(e.target.value)} placeholder="Tìm theo email…" aria-label="Tìm khách hàng"/></div>
      <SegmentedControl<'ALL'|LicenseState> value={filter} onChange={setFilter} items={[
        {value:'ALL', label:'Tất cả'}, {value:'active', label:'Hoạt động'}, {value:'expiring', label:'Sắp hết hạn'}, {value:'expired', label:'Hết hạn'}, {value:'locked', label:'Đã khóa'},
      ]}/>
    </Toolbar>
    <div className="pp-count">{loaded ? `${visible.length} / ${rows.length} khách hàng` : 'Đang tải…'}</div>
    {loaded && !visible.length ? <div className="pp-empty"><EmptyState icon={<Users size={30}/>} title={rows.length ? 'Không có khách hàng phù hợp' : 'Chưa có khách hàng'}
      description={rows.length ? 'Thử từ khóa hoặc bộ lọc khác.' : 'Tạo tài khoản OWNER, đặt số máy và thời hạn, rồi cấp mã kích hoạt cho Windows Agent.'}
      actionLabel={rows.length ? undefined : 'Tạo khách hàng'} onAction={onCreate}/></div>
    : <div className="pp-table"><Table<OwnerRow> data={visible} columns={columns}/></div>}
  </section>
}

function Licenses({rows,loaded,busy,onEdit,onToggle,onExtend}:{rows:OwnerRow[];loaded:boolean;busy:boolean;onEdit:(o:OwnerRow)=>void;onToggle:(o:OwnerRow)=>void;onExtend:(o:OwnerRow,months:number)=>void}){
  const order:Record<LicenseState,number> = {expired:0, expiring:1, active:2, locked:3}
  const sorted = [...rows].sort((a,b)=>order[a.state] - order[b.state] || (a.daysLeft ?? 1e9) - (b.daysLeft ?? 1e9))
  const counts = rows.reduce((acc,o)=>({...acc, [o.state]:(acc[o.state] ?? 0) + 1}), {} as Record<LicenseState,number>)
  const columns:TableColumn<OwnerRow>[] = [
    {key:'email', header:'Khách hàng', render:o=><button type="button" className="pa-owner" onClick={()=>onEdit(o)}><span className="pa-initial">{o.email.slice(0, 1).toUpperCase()}</span><span><strong>{o.email}</strong></span></button>},
    {key:'plan', header:'Gói', render:o=><span className="pa-plan">{o.max_agents} máy</span>},
    {key:'expires_at', header:'Thời hạn', render:o=><span className="pa-date">{dateLabel(o.expires_at)}
      <small className={o.state === 'expired' ? 'is-danger' : o.state === 'expiring' ? 'is-warn' : ''}>{o.daysLeft == null ? 'vĩnh viễn' : o.daysLeft < 0 ? `quá hạn ${-o.daysLeft} ngày` : `còn ${o.daysLeft} ngày`}</small></span>},
    {key:'state', header:'Trạng thái', render:o=><StateBadge row={o}/>},
    {key:'actions', header:'Gia hạn nhanh', align:'right', render:o=><div className="pp-row-actions">
      {[1, 12].map(m=><Button key={m} size="sm" variant="ghost" disabled={busy || !o.expires_at} title={o.expires_at ? `Gia hạn thêm ${m} tháng` : 'License không giới hạn'} onClick={()=>onExtend(o, m)}>+{m === 12 ? '1 năm' : '1 tháng'}</Button>)}
      <IconButton title="Sửa license" onClick={()=>onEdit(o)}><Settings2 size={15}/></IconButton>
      <IconButton title={o.enabled ? 'Khóa license' : 'Mở license'} danger={!!o.enabled} onClick={()=>onToggle(o)}>{o.enabled ? <ShieldOff size={15}/> : <ShieldCheck size={15}/>}</IconButton>
    </div>},
  ]
  return <>
    <div className="pa-chips">
      {(['expired','expiring','active','locked'] as LicenseState[]).map(s=><span key={s} className={'pa-chip is-' + s}><b>{counts[s] ?? 0}</b>{STATE_META[s].label}</span>)}
    </div>
    <section className="pp-panel">
      {loaded && !rows.length ? <div className="pp-empty"><EmptyState icon={<ShieldCheck size={30}/>} title="Chưa có license" description="License được tạo cùng tài khoản khách hàng."/></div>
        : <div className="pp-table"><Table<OwnerRow> data={sorted} columns={columns}/></div>}
    </section>
  </>
}

function Agents({rows,loaded,onRevoke}:{rows:AgentRow[];loaded:boolean;onRevoke:(a:AgentRow)=>void}){
  const [query,setQuery] = useState('')
  const [filter,setFilter] = useState<'ALL'|'online'|'offline'|'revoked'>('ALL')
  const q = query.trim().toLowerCase()
  const kind = (a:AgentRow) => !a.enabled ? 'revoked' : a.online ? 'online' : 'offline'
  const visible = rows.filter(a=>(filter === 'ALL' || kind(a) === filter) && (!q || `${a.hostname} ${a.id} ${a.ownerEmail}`.toLowerCase().includes(q)))
  const columns:TableColumn<AgentRow>[] = [
    {key:'hostname', header:'Máy Windows', render:a=><span className="pa-agent"><span className={'pa-dot ' + (!a.enabled ? 'is-off' : a.online ? 'is-ok' : 'is-idle')}/><span><strong>{a.hostname}</strong><code>{a.id}</code></span></span>},
    {key:'ownerEmail', header:'Khách hàng', render:a=><span className="pa-ellipsis">{a.ownerEmail}</span>},
    {key:'status', header:'Trạng thái', render:a=>!a.enabled ? <Badge>Đã thu hồi</Badge> : a.online ? <Badge tone="success" dot>Online</Badge> : <Badge tone="warning">Offline</Badge>},
    {key:'lastSeen', header:'Heartbeat', render:a=><span className="pa-date">{ago(a.lastSeen)}{a.lastSeen && <small>{new Date(a.lastSeen).toLocaleString('vi-VN')}</small>}</span>},
    {key:'created_at', header:'Kích hoạt', render:a=><span className="pa-date">{dateLabel(a.created_at)}</span>},
    {key:'actions', header:'', align:'right', render:a=>a.enabled ? <IconButton title="Thu hồi Agent" danger onClick={()=>onRevoke(a)}><Unplug size={15}/></IconButton> : null},
  ]
  return <section className="pp-panel">
    <Toolbar>
      <div className="pp-search"><SearchInput value={query} onChange={e=>setQuery(e.target.value)} placeholder="Tìm theo tên máy, Agent ID hoặc email…" aria-label="Tìm Agent"/></div>
      <SegmentedControl value={filter} onChange={setFilter} items={[{value:'ALL', label:'Tất cả'}, {value:'online', label:'Online'}, {value:'offline', label:'Offline'}, {value:'revoked', label:'Đã thu hồi'}]}/>
    </Toolbar>
    <div className="pp-count">{loaded ? `${visible.length} / ${rows.length} Agent · chỉ giám sát, SUPER_ADMIN không gửi lệnh LIVE/OBS` : 'Đang tải…'}</div>
    {loaded && !visible.length ? <div className="pp-empty"><EmptyState icon={<HardDrive size={30}/>} title={rows.length ? 'Không có Agent phù hợp' : 'Chưa có Agent nào'}
      description={rows.length ? 'Thử từ khóa hoặc bộ lọc khác.' : 'Agent xuất hiện ở đây sau khi khách hàng nhập mã kích hoạt trên máy Windows.'}/></div>
    : <div className="pp-table"><Table<AgentRow> data={visible} columns={columns}/></div>}
  </section>
}

type CaptchaTemplate = { id:string; name:string; sha256:string; bytes:number; created_at:string }

const readBase64 = (file:File) => new Promise<string>((resolve, reject)=>{
  const reader = new FileReader()
  reader.onload = ()=>resolve(String(reader.result).split(',', 2)[1] ?? '')
  reader.onerror = ()=>reject(reader.error ?? new Error('Không đọc được file'))
  reader.readAsDataURL(file)
})

/** Reference images every Windows Agent downloads for CAPTCHA detection; OWNERs never see them. */
function CaptchaTemplates(){
  const toast = useToast()
  const [items,setItems] = useState<CaptchaTemplate[]|null>(null)
  const [previews,setPreviews] = useState<Record<string,string>>({})
  const [busy,setBusy] = useState(false)
  const [removing,setRemoving] = useState<CaptchaTemplate|null>(null)

  const load = useCallback(async ()=>{
    try{setItems((await call<{templates:CaptchaTemplate[]}>('/captcha-templates')).templates)}
    catch(e){toast.push({tone:'danger', title:'Không tải được mẫu CAPTCHA', description:e instanceof Error ? e.message : ''})}
  },[toast])
  useEffect(()=>{void load()},[load])

  // <img> cannot send the admin token: fetch each preview once and show it from a blob URL.
  useEffect(()=>{
    let alive = true
    for (const t of items ?? []) {
      if (previews[t.id]) continue
      fetch(`${api}/api/captcha-templates/${t.id}/image`, {headers:{Authorization:'Bearer ' + getAuthToken()}})
        .then(r=>r.ok ? r.blob() : Promise.reject(new Error(String(r.status))))
        .then(b=>{if (alive) setPreviews(p=>({...p, [t.id]:URL.createObjectURL(b)}))})
        .catch(()=>undefined)
    }
    return ()=>{alive = false}
  },[items, previews])

  const upload = async (files:FileList|null) => {
    if (!files?.length) return
    setBusy(true)
    try{
      for (const file of Array.from(files)) {
        if (!/^image\/(png|jpeg)$/.test(file.type) || file.size > 3_000_000) throw new Error(`${file.name}: chỉ nhận PNG/JPEG tối đa 3 MB`)
        await call('/captcha-templates', body({name:file.name.replace(/\.(png|jpe?g)$/i, '').slice(0, 100) || 'captcha', image_base64:await readBase64(file)}))
      }
      toast.push({tone:'success', title:'Đã thêm mẫu CAPTCHA', description:'Agent nhận mẫu mới khi khởi động lại hoặc trong vòng 6 giờ.'})
    }catch(e){toast.push({tone:'danger', title:'Không thêm được mẫu', description:e instanceof Error ? e.message : ''})}
    finally{setBusy(false);await load()}
  }
  const remove = async (t:CaptchaTemplate) => {
    setBusy(true)
    try{await call(`/captcha-templates/${t.id}`, {method:'DELETE'});toast.push({tone:'success', title:'Đã xoá mẫu', description:'Agent tự xoá mẫu khi khởi động lại hoặc trong vòng 6 giờ.'})}
    catch(e){toast.push({tone:'danger', title:'Không xoá được mẫu', description:e instanceof Error ? e.message : ''})}
    finally{setBusy(false);await load()}
  }

  return <div className="pa-grid">
    <section className="pa-card"><header className="pa-card-head"><h3><ScanEye size={17}/>Mẫu đang dùng</h3><span>{items ? `${items.length} mẫu` : 'Đang tải…'}</span></header>
      {items && !items.length ? <EmptyState icon={<ScanEye size={30}/>} title="Chưa có mẫu CAPTCHA" description="Khi chưa có mẫu, Agent của khách không bật được giám sát CAPTCHA."/>
      : <div className="pa-captcha-list">{(items ?? []).map(t=><div key={t.id} className="pa-captcha-item">
          {previews[t.id] ? <img src={previews[t.id]} alt={t.name}/> : <span className="pa-captcha-ph"/>}
          <span className="pa-captcha-meta"><strong title={t.name}>{t.name}</strong><small>{Math.max(1, Math.round(t.bytes / 1024))} KB · {dateLabel(t.created_at)}</small></span>
          <IconButton title="Xoá mẫu" danger onClick={()=>setRemoving(t)}><Trash2 size={15}/></IconButton>
        </div>)}</div>}
    </section>
    <section className="pa-card"><header className="pa-card-head"><h3><UploadCloud size={17}/>Thêm mẫu</h3></header>
      <label className="pa-upload">
        <input type="file" accept="image/png,image/jpeg" multiple disabled={busy} onChange={e=>{void upload(e.target.files);e.target.value=''}}/>
        <UploadCloud size={22}/><strong>{busy ? 'Đang tải lên…' : 'Chọn ảnh PNG/JPEG'}</strong><small>Tối đa 3 MB mỗi ảnh, cạnh 20–1800 px</small>
      </label>
      <ul className="pa-rules">
        <li><CheckCircle2 size={16}/>Cắt một vùng nhỏ, rõ nét chứa tiêu đề / khung CAPTCHA của TikTok LIVE Studio.</li>
        <li><CheckCircle2 size={16}/>Windows Agent cập nhật mẫu khi khởi động (kết nối Cloud) và 6 giờ một lần; mẫu bị xoá ở đây cũng tự bị xoá trên máy.</li>
        <li><CheckCircle2 size={16}/>Ảnh thiếu chi tiết sẽ bị Agent bỏ qua (ghi vào nhật ký của Agent).</li>
        <li className="is-no"><X size={16}/>Khách hàng OWNER không thấy và không sửa được mục này.</li>
      </ul>
    </section>
    <ConfirmModal open={!!removing} loading={busy} title={`Xoá mẫu “${removing?.name ?? ''}”?`} confirmLabel="Xoá" tone="danger"
      description="Mọi Agent sẽ ngừng dùng mẫu này ở lần cập nhật kế tiếp."
      onClose={()=>setRemoving(null)} onConfirm={()=>{const t = removing;setRemoving(null);if (t) void remove(t)}}/>
  </div>
}

function SettingsView({email}:{email:string}){
  const items:[string,ReactNode][] = [
    ['API endpoint', <code key="api">{api}/api</code>],
    ['Tài khoản quản trị', email],
    ['Vai trò', 'SUPER_ADMIN (duy nhất trên nền tảng)'],
    ['Phiên đăng nhập', 'Lưu trong sessionStorage của tab, đóng tab là đăng xuất'],
    ['Làm mới dữ liệu', 'Tự động mỗi 30 giây'],
  ]
  return <div className="pa-grid">
    <section className="pa-card"><header className="pa-card-head"><h3><Settings2 size={17}/>Thông tin nền tảng</h3></header>
      <dl className="pa-dl">{items.map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
    </section>
    <section className="pa-card"><header className="pa-card-head"><h3><ShieldCheck size={17}/>Chính sách phân quyền</h3></header>
      <ul className="pa-rules">
        <li><CheckCircle2 size={16}/>Tạo khách hàng OWNER, đặt số máy và thời hạn license.</li>
        <li><CheckCircle2 size={16}/>Cấp mã kích hoạt dùng một lần; mã chỉ hiện một lần.</li>
        <li><CheckCircle2 size={16}/>Xem và thu hồi Windows Agent trên toàn hệ thống.</li>
        <li className="is-no"><X size={16}/>Không gửi lệnh LIVE / OBS / TTS tới Agent của khách.</li>
      </ul>
      <p className="pa-muted">Cấu hình chính sách và phát hành phiên bản Agent sẽ có khi Backend có API lưu cấu hình và audit log.</p>
    </section>
  </div>
}

/** Date field with one-click durations and an "unlimited" switch. */
function ExpiryField({value,onChange,base}:{value:string;onChange:(v:string)=>void;base:string|null}){
  const unlimited = value === ''
  const [today] = useState(()=>toDateInput(new Date().toISOString()))
  return <div className="pa-expiry">
    <div className="pa-expiry-head"><span>Thời hạn license</span><Switch label="Không giới hạn" checked={unlimited} onChange={e=>onChange(e.target.checked ? '' : addMonths(base, 1))}/></div>
    {!unlimited && <>
      <Input type="date" aria-label="Ngày hết hạn" value={value} min={today} onChange={e=>onChange(e.target.value)}/>
      <div className="pa-presets">{[1, 3, 6, 12].map(m=><button key={m} type="button" onClick={()=>onChange(addMonths(base, m))}>+{m === 12 ? '1 năm' : `${m} tháng`}</button>)}</div>
      <small className="pa-muted">License có hiệu lực hết ngày {value ? new Date(value + 'T00:00:00').toLocaleDateString('vi-VN') : '—'}.</small>
    </>}
  </div>
}

function CreateOwner({busy,onClose,onCreated}:{busy:boolean;onClose:()=>void;onCreated:(o:{id:string;email:string}, password:string, withKey:boolean)=>Promise<void>}){
  const [email,setEmail] = useState('')
  const [password,setPassword] = useState(randomPassword)
  const [maxAgents,setMaxAgents] = useState('1')
  const [expiry,setExpiry] = useState(()=>addMonths(null, 1))
  const [withKey,setWithKey] = useState(true)
  const [error,setError] = useState('')
  const [saving,setSaving] = useState(false)
  const submit = async (e:FormEvent) => {
    e.preventDefault();setError('')
    if (password.length < 6) {setError('Mật khẩu tối thiểu 6 ký tự');return}
    setSaving(true)
    try{
      const o = await call<{id:string;email:string}>('/identity/owners', body({email:email.trim(), password, max_agents:Math.max(1, Math.min(100, Number(maxAgents) || 1)), expires_at:fromDateInput(expiry)}))
      await onCreated(o, password, withKey)
    }catch(err){setError(err instanceof Error ? err.message : 'Không tạo được khách hàng')}
    finally{setSaving(false)}
  }
  return <Sheet open onClose={()=>{if (!saving) onClose()}} width={520} title="Tạo khách hàng" footer={<div className="pp-sheet-footer">
    <Button variant="ghost" disabled={saving} onClick={onClose}>Hủy</Button>
    <Button type="submit" form="pa-create" loading={saving || busy} leftIcon={<Plus size={16}/>}>{withKey ? 'Tạo & cấp mã kích hoạt' : 'Tạo khách hàng'}</Button>
  </div>}>
    <form id="pa-create" className="pp-form" onSubmit={e=>void submit(e)}>
      {error && <Alert tone="danger">{error}</Alert>}
      <Input label="Email đăng nhập" type="email" required autoFocus value={email} onChange={e=>setEmail(e.target.value)} placeholder="khachhang@example.com"/>
      <div className="pa-password">
        <PasswordInput label="Mật khẩu" autoComplete="new-password" required minLength={6} value={password} onChange={e=>setPassword(e.target.value)} hint="Tối thiểu 6 ký tự. Gửi cho khách cùng mã kích hoạt."/>
        <Button type="button" size="sm" variant="outline" leftIcon={<Wand2 size={14}/>} onClick={()=>setPassword(randomPassword())}>Tạo ngẫu nhiên</Button>
      </div>
      <Input label="Số máy tối đa" type="number" min={1} max={100} value={maxAgents} onChange={e=>setMaxAgents(e.target.value)} hint="Số Windows Agent được kích hoạt cùng lúc (1–100)."/>
      <ExpiryField value={expiry} onChange={setExpiry} base={null}/>
      <div className="pp-switch-row"><Switch label="Cấp mã kích hoạt ngay" checked={withKey} onChange={e=>setWithKey(e.target.checked)}/><small>Mã dùng một lần để kích hoạt máy Windows đầu tiên.</small></div>
    </form>
  </Sheet>
}

function LicenseSheet({owner,agents,busy,onClose,onSave,onKey,onRevoke}:{owner:OwnerRow;agents:AgentRow[];busy:boolean;onClose:()=>void;onSave:(p:{max_agents:number;expires_at:string|null;enabled:boolean})=>Promise<void>;onKey:()=>void;onRevoke:(a:AgentRow)=>void}){
  const [maxAgents,setMaxAgents] = useState(String(owner.max_agents))
  const [expiry,setExpiry] = useState(toDateInput(owner.expires_at))
  const [enabled,setEnabled] = useState(!!owner.enabled)
  const active = agents.filter(a=>a.enabled)
  return <Sheet open onClose={()=>{if (!busy) onClose()}} width={560} title="License khách hàng" footer={<div className="pp-sheet-footer">
    <Button variant="ghost" disabled={busy} onClick={onClose}>Hủy</Button>
    <Button type="submit" form="pa-license" loading={busy}>Lưu license</Button>
  </div>}>
    <form id="pa-license" className="pp-form" onSubmit={e=>{e.preventDefault();void onSave({max_agents:Math.max(1, Math.min(100, Number(maxAgents) || 1)), expires_at:fromDateInput(expiry), enabled})}}>
      <div className="pa-owner-head">
        <span className="pa-initial is-lg">{owner.email.slice(0, 1).toUpperCase()}</span>
        <span><strong>{owner.email}</strong><small><StateBadge row={owner}/> · {owner.agentsUsed}/{owner.max_agents} máy</small></span>
        <Button type="button" size="sm" variant="outline" leftIcon={<KeyRound size={14}/>} disabled={busy || !owner.enabled} onClick={onKey}>Cấp mã</Button>
      </div>
      <Input label="Số máy tối đa" type="number" min={1} max={100} value={maxAgents} onChange={e=>setMaxAgents(e.target.value)}
        hint={Number(maxAgents) < active.length ? `Đang có ${active.length} máy hoạt động — giảm giới hạn không tự thu hồi máy đã kích hoạt.` : 'Số Windows Agent được kích hoạt cùng lúc (1–100).'}/>
      <ExpiryField value={expiry} onChange={setExpiry} base={owner.expires_at}/>
      <div className="pp-switch-row"><Switch label="License đang mở" checked={enabled} onChange={e=>setEnabled(e.target.checked)}/><small>{enabled ? 'Khách dùng được Agent trong thời hạn.' : 'Khóa: không kích hoạt thêm máy, lệnh mới bị từ chối.'}</small></div>
      <div className="pa-sub">
        <h4><HardDrive size={15}/>Thiết bị của khách ({agents.length})</h4>
        {!agents.length ? <p className="pa-muted">Chưa kích hoạt máy nào.</p> : <ul className="pa-list">{agents.map(a=><li key={a.id}>
          <span className={'pa-dot ' + (!a.enabled ? 'is-off' : a.online ? 'is-ok' : 'is-idle')}/>
          <span className="pa-list-text"><strong>{a.hostname}</strong><small>{!a.enabled ? 'Đã thu hồi' : a.online ? 'Online' : `Offline · ${ago(a.lastSeen)}`}</small></span>
          {!!a.enabled && <IconButton title="Thu hồi Agent" danger onClick={()=>onRevoke(a)}><Unplug size={15}/></IconButton>}
        </li>)}</ul>}
      </div>
    </form>
  </Sheet>
}

function IssuedKey({email,activationKey,password,onClose}:{email:string;activationKey:string;password?:string;onClose:()=>void}){
  const toast = useToast()
  const text = [`Tài khoản Sarae: ${email}`, password ? `Mật khẩu: ${password}` : '', `Mã kích hoạt Windows Agent: ${activationKey}`].filter(Boolean).join('\n')
  const copy = async (value:string, what:string) => {
    try{await navigator.clipboard.writeText(value);toast.push({tone:'success', title:`Đã sao chép ${what}`})}
    catch{toast.push({tone:'danger', title:'Không sao chép được, hãy bôi đen và copy thủ công'})}
  }
  return <Modal open size="md" title="Mã kích hoạt đã được cấp" onClose={onClose} footer={<>
    <Button variant="outline" leftIcon={<Copy size={15}/>} onClick={()=>void copy(text, 'thông tin gửi khách')}>Sao chép tất cả</Button>
    <Button onClick={onClose}>Tôi đã lưu mã</Button>
  </>}>
    <div className="pa-issued">
      <Alert tone="warning" title="Mã chỉ hiển thị một lần">Sao chép và gửi cho khách ngay. Đóng hộp thoại này là không xem lại được; khi cần, hãy cấp mã mới.</Alert>
      <div className="pa-field"><span>Khách hàng</span><strong>{email}</strong></div>
      {password && <div className="pa-field"><span>Mật khẩu</span><code>{password}</code><IconButton title="Sao chép mật khẩu" onClick={()=>void copy(password, 'mật khẩu')}><Copy size={15}/></IconButton></div>}
      <div className="pa-field is-key"><span>Mã kích hoạt</span><code>{activationKey}</code><IconButton title="Sao chép mã" onClick={()=>void copy(activationKey, 'mã kích hoạt')}><Copy size={15}/></IconButton></div>
      <ol className="pa-steps">
        <li>Trên máy Windows chạy OBS, khách mở Sarae Agent.</li>
        <li>Chọn <b>Kích hoạt</b> và dán mã ở trên (dùng được một lần).</li>
        <li>Máy sẽ xuất hiện trong tab <b>Windows Agents</b> khi gửi heartbeat đầu tiên.</li>
      </ol>
      <p className="pa-muted"><CalendarClock size={13}/> Mã gắn với license hiện tại của khách; license hết hạn hoặc bị khóa thì mã không kích hoạt được.</p>
    </div>
  </Modal>
}
