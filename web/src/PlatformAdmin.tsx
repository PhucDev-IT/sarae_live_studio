import {useEffect,useState} from 'react'
import {Activity,ChartNoAxesCombined,Clock3,HardDrive,RefreshCcw,Settings2,ShieldCheck,Users,Wifi,WifiOff,LogOut} from 'lucide-react'
import AccountPortal from './AccountPortal'
import {api} from './mediaApi'
import {getAuthToken} from './AuthGate'

type Stats={owners_total:number;owners_active:number;licenses_enabled:number;licenses_expiring_7d:number;agents_total:number;agents_online:number;agents_offline:number;agents_revoked:number}
type Agent={agent_id:string;hostname:string;online:boolean;last_seen:string;seconds_ago:number}
type Tab='dashboard'|'customers'|'licenses'|'agents'|'monitor'|'settings'
const nav:{id:Tab;name:string;icon:typeof Users}[]=[
 {id:'dashboard',name:'Tổng quan',icon:ChartNoAxesCombined},
 {id:'customers',name:'Khách hàng',icon:Users},
 {id:'licenses',name:'License & Gói',icon:ShieldCheck},
 {id:'agents',name:'Windows Agents',icon:HardDrive},
 {id:'monitor',name:'Giám sát',icon:Activity},
 {id:'settings',name:'Cấu hình',icon:Settings2},
]
const empty:Stats={owners_total:0,owners_active:0,licenses_enabled:0,licenses_expiring_7d:0,agents_total:0,agents_online:0,agents_offline:0,agents_revoked:0}
export default function PlatformAdmin({email,logout}:{email:string;logout:()=>void}){
 const [tab,setTab]=useState<Tab>('dashboard'),[stats,setStats]=useState<Stats>(empty),[agents,setAgents]=useState<Agent[]>([]),[error,setError]=useState(''),[loading,setLoading]=useState(true),[lastRefresh,setLastRefresh]=useState('')
 const fetchData=async()=>{
  setLoading(true);setError('')
  try{
   const headers={Authorization:'Bearer '+getAuthToken()}
   const [a,b]=await Promise.all([fetch(api+'/api/identity/platform/summary',{headers}),fetch(api+'/api/agents/status',{headers})])
   if(!a.ok||!b.ok)throw Error('Không tải được dữ liệu quản trị (HTTP '+(a.ok?b.status:a.status)+')')
   setStats(await a.json() as Stats);setAgents(((await b.json()) as {agents:Agent[]}).agents)
   setLastRefresh(new Date().toLocaleTimeString('vi-VN'))
  }catch(e){setError(e instanceof Error?e.message:'Lỗi hệ thống')}finally{setLoading(false)}
 }
 useEffect(()=>{void fetchData();const interval=setInterval(()=>void fetchData(),30000);return()=>clearInterval(interval)},[])
 const cards=[
  {name:'Tổng khách hàng',number:stats.owners_total,icon:Users,note:stats.owners_active+' đang hoạt động'},
  {name:'License hiệu lực',number:stats.licenses_enabled,icon:ShieldCheck,note:stats.licenses_expiring_7d+' sắp hết hạn (7 ngày)'},
  {name:'Agent đang online',number:stats.agents_online,icon:Wifi,note:stats.agents_total+' Agent đăng ký'},
  {name:'Agent offline',number:stats.agents_offline,icon:WifiOff,note:stats.agents_revoked+' đã thu hồi'},
 ]
 return <div style={{display:'flex',minHeight:'100vh',background:'#f6f7fb',color:'#182033',fontFamily:'Inter,Segoe UI,sans-serif'}}>
 <aside style={{width:240,flexShrink:0,background:'#171a2d',color:'#c5cce0',padding:20,display:'flex',flexDirection:'column',gap:7}}>
  <div style={{fontSize:18,fontWeight:800,letterSpacing:1,color:'white',padding:'12px 8px 28px'}}>SARAE <span style={{fontSize:10,color:'#aaa3ed'}}>PLATFORM</span></div>
  <div style={{fontSize:10,letterSpacing:2,opacity:.6,padding:'8px 12px'}}>SUPER ADMIN</div>
  {nav.map(item=><button key={item.id} onClick={()=>setTab(item.id)} style={{display:'flex',alignItems:'center',gap:12,textAlign:'left',border:0,borderRadius:9,padding:'13px 12px',background:tab===item.id?'#494183':'transparent',color:tab===item.id?'white':'#c5cce0',cursor:'pointer'}}><item.icon size={18}/>{item.name}</button>)}
  <div style={{flex:1}}/>
  <div style={{fontSize:12,wordBreak:'break-word',padding:'12px 8px'}}>{email}</div>
  <button onClick={logout} style={{display:'flex',gap:9,alignItems:'center',border:'1px solid #555a76',color:'white',background:'transparent',borderRadius:9,padding:12,cursor:'pointer'}}><LogOut size={16}/> Đăng xuất</button>
 </aside>
 <main style={{flex:1,padding:'30px min(4vw,50px)',minWidth:0,maxWidth:1650}}>
  <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',gap:12,marginBottom:26}}>
   <div><div style={{fontSize:11,color:'#827ba9',fontWeight:700,letterSpacing:2}}>SARAE / PLATFORM ADMIN</div><h1 style={{margin:'10px 0 4px',fontSize:26}}>{nav.find(n=>n.id===tab)?.name}</h1><p style={{color:'#738095',fontSize:13,margin:0}}>Quản trị khách hàng, license và thiết bị trên toàn nền tảng.</p></div>
   <button onClick={()=>void fetchData()} disabled={loading} style={{padding:'10px 14px',display:'flex',alignItems:'center',gap:8,border:'1px solid #dbe0ee',background:'white',borderRadius:9,cursor:'pointer'}}><RefreshCcw size={16}/> Làm mới</button>
  </div>
  {error&&<div role="alert" style={{background:'#fff1f1',padding:15,borderRadius:9,color:'#9b2323',marginBottom:20}}>{error}</div>}
  {tab==='dashboard'&&<><div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(185px,1fr))',gap:15,marginBottom:22}}>
   {cards.map(card=><div key={card.name} style={{background:'white',border:'1px solid #e7eaf3',borderRadius:13,padding:22}}><card.icon size={22} color="#6553d7"/><p style={{color:'#728097',fontSize:12}}>{card.name}</p><strong style={{fontSize:32}}>{loading&&!lastRefresh?'—':card.number}</strong><div style={{fontSize:12,color:'#8791a4',marginTop:10}}>{card.note}</div></div>)}
  </div><div style={{background:'white',border:'1px solid #e7eaf3',borderRadius:13,padding:22}}><h3 style={{marginTop:0}}>Hoạt động thiết bị gần đây</h3>{agents.length?agents.slice(0,10).map(agent=><div key={agent.agent_id} style={{display:'flex',justifyContent:'space-between',padding:'12px 0',borderTop:'1px solid #edf0f7',fontSize:13}}><span>{agent.hostname}</span><span style={{color:agent.online?'#208263':'#8791a4'}}>{agent.online?'Online':'Offline'}</span></div>):<p style={{color:'#8791a4',fontSize:13}}>Chưa có Agent hoạt động.</p>}</div></>}
  {(tab==='customers'||tab==='licenses')&&<><p style={{fontSize:13,color:'#728097'}}>Quản lý khách hàng, cấp mã kích hoạt và thay đổi license. Những thao tác này sử dụng API phân quyền SUPER_ADMIN.</p><AccountPortal/></>}
  {tab==='agents'&&<div style={{background:'white',borderRadius:13,padding:22,border:'1px solid #e7eaf3'}}><h3>Danh sách Agent</h3><p style={{fontSize:12,color:'#728097'}}>Chỉ giám sát. SUPER_ADMIN không được gửi lệnh điều khiển LIVE.</p>{agents.length?agents.map(agent=><div key={agent.agent_id} style={{display:'flex',justifyContent:'space-between',gap:10,borderTop:'1px solid #edf0f7',padding:'14px 0',fontSize:13}}><span><strong>{agent.hostname}</strong><br/><small style={{color:'#8591a4'}}>{agent.agent_id}</small></span><span style={{color:agent.online?'#208263':'#8791a4'}}>{agent.online?'Online':'Offline'}</span></div>):<p>Chưa có Agent.</p>}</div>}
  {tab==='monitor'&&<div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(240px,1fr))',gap:16}}>{[{label:'Agent online',value:stats.agents_online},{label:'Agent offline',value:stats.agents_offline},{label:'License sắp hết hạn',value:stats.licenses_expiring_7d}].map(m=><div key={m.label} style={{padding:24,background:'white',borderRadius:13,border:'1px solid #e7eaf3'}}><Activity size={20} color="#6553d7"/><p>{m.label}</p><strong style={{fontSize:28}}>{m.value}</strong></div>)}</div>}
  {tab==='settings'&&<div style={{background:'white',padding:24,borderRadius:13,border:'1px solid #e7eaf3'}}><h3>Cấu hình nền tảng</h3><p>Vai trò hiện tại: SUPER_ADMIN. Cấu hình chính sách hệ thống và phát hành phiên bản Agent sẽ được bổ sung sau khi có API lưu cấu hình và audit log.</p><p style={{fontSize:13,color:'#728097'}}>Không hiển thị các điều khiển chưa có Backend tương ứng.</p></div>}
  <div style={{display:'flex',alignItems:'center',gap:7,fontSize:11,color:'#95a0ae',marginTop:22}}><Clock3 size={13}/> {lastRefresh?'Dữ liệu làm mới lúc '+lastRefresh:'Đang lấy dữ liệu...'} · Cập nhật định kỳ 30 giây</div>
 </main></div>
}
