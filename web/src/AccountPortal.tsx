import {useEffect,useState} from 'react'
import {api} from './mediaApi'

type User={id:string;email:string;role:'SUPER_ADMIN'|'OWNER'}
type Owner={id:string;email:string;active:number;enabled:number;max_agents:number;expires_at:string|null}
type Agent={id:string;owner_id:string;hostname:string;enabled:number}
const storage='sarae-auth-token'
export function getAuthToken(){return sessionStorage.getItem(storage)||''}
export default function AccountPortal(){
 const [token,setToken]=useState(getAuthToken)
 const [email,setEmail]=useState('')
 const [password,setPassword]=useState('')
 const [user,setUser]=useState<User|null>(null)
 const [owners,setOwners]=useState<Owner[]>([])
 const [agents,setAgents]=useState<Agent[]>([])
 const [newEmail,setNewEmail]=useState('')
 const [newPassword,setNewPassword]=useState('')
 const [maxAgents,setMaxAgents]=useState(1)
 const [licenseEnd,setLicenseEnd]=useState('')
 const [activation,setActivation]=useState('')
 const [error,setError]=useState('')
 const [busy,setBusy]=useState(false)
 const [renames,setRenames]=useState<Record<string,string>>({})
 const request=async<T,>(path:string,body?:unknown,provided?:string,method?:string):Promise<T>=>{
  const res=await fetch(api+'/api/identity'+path,{method:method||(body?'POST':'GET'),
   headers:{'Content-Type':'application/json',Authorization:'Bearer '+(provided||token)},
   body:body?JSON.stringify(body):undefined})
  const data=await res.json().catch(()=>({}))
  if(!res.ok)throw new Error(data.detail||'HTTP '+res.status)
  return data as T
 }
 const refresh=async(value?:string)=>{
  const me=await request<User>('/me',undefined,value)
  setUser(me)
  setAgents(await request<Agent[]>('/agents',undefined,value))
  if(me.role==='SUPER_ADMIN')setOwners(await request<Owner[]>('/owners',undefined,value))
 }
 useEffect(()=>{if(token)void refresh().catch(()=>{sessionStorage.removeItem(storage);setToken('');setUser(null)})},[token])
 const perform=async(fn:()=>Promise<void>)=>{
  setBusy(true);setError('')
  try{await fn()}catch(e){setError(e instanceof Error?e.message:'Có lỗi xảy ra')}finally{setBusy(false)}
 }
 if(!user)return <section className="panel" style={{padding:24,maxWidth:460}}>
  <h2>Đăng nhập Sarae</h2><p>SUPER_ADMIN quản lý khách hàng và license; OWNER quản lý thiết bị của mình.</p>
  <form onSubmit={e=>{e.preventDefault();void perform(async()=>{
   const v=await request<{access_token:string}>('/login',{email,password})
   sessionStorage.setItem(storage,v.access_token);setToken(v.access_token);setPassword('')
  })}}>
   <label>Email<input required type="email" value={email} onChange={e=>setEmail(e.target.value)}/></label>
   <label>Mật khẩu<input required type="password" value={password} onChange={e=>setPassword(e.target.value)}/></label>
   <button disabled={busy} type="submit">Đăng nhập</button>
  </form>{error&&<p role="alert">{error}</p>}
 </section>
 return <section className="panel" style={{padding:24,display:'grid',gap:16}}>
  <header><h2>Quản lý tài khoản và Agent</h2><p>{user.email} — {user.role}</p>
   <button onClick={()=>{sessionStorage.removeItem(storage);setToken('');setUser(null)}}>Đăng xuất</button>
   <button disabled={busy} onClick={()=>void perform(()=>refresh())}>Làm mới</button></header>
  {user.role==='SUPER_ADMIN'&&<section><h3>Khách hàng OWNER</h3><form onSubmit={e=>{e.preventDefault();void perform(async()=>{await request('/owners',{email:newEmail,password:newPassword,max_agents:maxAgents,expires_at:licenseEnd?new Date(licenseEnd).toISOString():null});setNewEmail('');setNewPassword('');await refresh()})}}>
    <label>Email OWNER<input required type="email" value={newEmail} onChange={e=>setNewEmail(e.target.value)}/></label>
    <label>Mật khẩu OWNER (tối thiểu 12 ký tự)<input required type="password" minLength={12} value={newPassword} onChange={e=>setNewPassword(e.target.value)}/></label>
    <label>Giới hạn Agent<input type="number" min={1} max={100} value={maxAgents} onChange={e=>setMaxAgents(+e.target.value)}/></label>
    <label>License hết hạn (tùy chọn)<input type="datetime-local" value={licenseEnd} onChange={e=>setLicenseEnd(e.target.value)}/></label>
    <button disabled={busy}>Tạo OWNER</button>
   </form>{owners.map(o=><div key={o.id} style={{borderTop:'1px solid #8884',padding:'12px 0'}}>
    <strong>{o.email}</strong> · Giới hạn {o.max_agents} máy · {o.enabled?'Đang hoạt động':'Đã khóa'}
    <button disabled={busy} onClick={()=>void perform(async()=>{
     const result=await request<{activation_key:string}>(`/owners/${o.id}/activation-keys`,{})
     setActivation(result.activation_key)
    })}>Cấp mã kích hoạt</button>
    <div style={{display:'grid',gap:6,maxWidth:340,marginTop:8}}>
     <label>Giới hạn máy<input type="number" min={1} max={100} value={o.max_agents} onChange={e=>setOwners(all=>all.map(x=>x.id===o.id?{...x,max_agents:Number(e.target.value)}:x))}/></label>
     <label>Hết hạn license<input type="datetime-local" value={o.expires_at?.slice(0,16)||''} onChange={e=>setOwners(all=>all.map(x=>x.id===o.id?{...x,expires_at:e.target.value?new Date(e.target.value).toISOString():null}:x))}/></label>
     <div><button disabled={busy} onClick={()=>void perform(async()=>{await request(`/owners/${o.id}/license`,{max_agents:o.max_agents,expires_at:o.expires_at,enabled:Boolean(o.enabled)},undefined,'PUT');await refresh()})}>Lưu license</button>
     <button disabled={busy} onClick={()=>void perform(async()=>{await request(`/owners/${o.id}/license`,{max_agents:o.max_agents,expires_at:o.expires_at,enabled:!o.enabled},undefined,'PUT');await refresh()})}>{o.enabled?'Khóa license':'Mở license'}</button></div>
    </div>
   </div>)}
  {activation&&<p><strong>Mã kích hoạt (chỉ hiện một lần):</strong><code style={{overflowWrap:'anywhere'}}>{activation}</code></p>}
  </section>}
  <section><h3>Thiết bị {user.role==='OWNER'?'của tôi':'trên hệ thống'}</h3>
  {!agents.length?<p>Chưa có Agent được kích hoạt.</p>:agents.map(a=><div key={a.id} style={{borderTop:'1px solid #8884',padding:'12px 0'}}><strong>{a.hostname}</strong> — {a.id} · {a.enabled?'Được cấp quyền':'Đã khóa'}<div style={{display:'flex',gap:8,flexWrap:'wrap',marginTop:8}}><input value={renames[a.id]??a.hostname} onChange={e=>setRenames(x=>({...x,[a.id]:e.target.value}))}/><button disabled={busy} onClick={()=>void perform(async()=>{await request(`/agents/${a.id}`,{hostname:renames[a.id]??a.hostname},undefined,'PATCH');await refresh()})}>Đổi tên</button><button disabled={busy||!a.enabled} onClick={()=>{if(window.confirm('Thu hồi quyền kết nối Agent này?'))void perform(async()=>{await request(`/agents/${a.id}`,undefined,undefined,'DELETE');await refresh()})}}>Thu hồi Agent</button></div></div>)}</section>
  {error&&<p role="alert">{error}</p>}
 </section>
}
