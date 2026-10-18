import { useCallback, useEffect, useState } from 'react'
import { api } from './mediaApi'

export type SaraeUser = { id:string; email:string; role:'SUPER_ADMIN'|'OWNER' }
const storage='sarae-auth-token'
export const getAuthToken=()=>sessionStorage.getItem(storage)||''
export function clearAuth(){sessionStorage.removeItem(storage);window.dispatchEvent(new Event('sarae-auth-changed'))}
export function setAuth(token:string){sessionStorage.setItem(storage,token);window.dispatchEvent(new Event('sarae-auth-changed'))}
export async function authFetch(path:string,init:RequestInit={}){
 const token=getAuthToken()
 const response=await fetch(api+'/api/identity'+path,{...init,headers:{...init.headers,Authorization:'Bearer '+token}})
 if(response.status===401){clearAuth();throw new Error('Phiên đăng nhập đã hết hạn')}
 return response
}
export function AuthGate({children}:{children:(user:SaraeUser,logout:()=>void)=>React.ReactNode}){
 const [user,setUser]=useState<SaraeUser|null>(null)
 const [state,setState]=useState<'checking'|'login'|'ready'>('checking')
 const [email,setEmail]=useState('')
 const [password,setPassword]=useState('')
 const [busy,setBusy]=useState(false)
 const [error,setError]=useState('')
 const check=useCallback(async()=>{
  const token=getAuthToken()
  if(!token){setUser(null);setState('login');return}
  try{
   const res=await fetch(api+'/api/identity/me',{headers:{Authorization:'Bearer '+token}})
   if(!res.ok)throw new Error('Phiên đăng nhập không hợp lệ')
   const data=await res.json() as SaraeUser
   setUser(data);setState('ready')
  }catch{sessionStorage.removeItem(storage);setUser(null);setState('login')}
 },[])
 useEffect(()=>{void check();const fn=()=>void check();window.addEventListener('sarae-auth-changed',fn);return()=>window.removeEventListener('sarae-auth-changed',fn)},[check])
 const logout=()=>{const token=getAuthToken();clearAuth();void fetch(api+'/api/identity/logout',{method:'POST',headers:{Authorization:'Bearer '+token}}).catch(()=>{})}
 const login=async(e:React.FormEvent)=>{
  e.preventDefault();setBusy(true);setError('')
  try{
   const response=await fetch(api+'/api/identity/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password})})
   const data=await response.json()
   if(!response.ok)throw new Error(data.detail||'Không thể đăng nhập')
   setPassword('');setAuth(data.access_token)
  }catch(ex){setError(ex instanceof Error?ex.message:'Không thể đăng nhập')}finally{setBusy(false)}
 }
 if(state==='checking')return <div style={{minHeight:'100vh',display:'grid',placeItems:'center',background:'#101827',color:'white'}}>Đang xác minh phiên đăng nhập...</div>
 if(state==='login'||!user)return <div style={{minHeight:'100vh',background:'linear-gradient(135deg,#101827,#25224b)',display:'grid',placeItems:'center',padding:20,color:'#f4f5fc'}}>
  <form onSubmit={e=>void login(e)} style={{width:'100%',maxWidth:420,padding:32,background:'#ffffff',color:'#1c2440',borderRadius:20,boxShadow:'0 20px 80px #0005',display:'grid',gap:15}}>
   <div style={{fontSize:13,color:'#6553d7',fontWeight:800,letterSpacing:2}}>SARAE LIVE STUDIO</div>
   <h1 style={{fontSize:27,margin:'6px 0'}}>Đăng nhập</h1>
   <p style={{fontSize:13,color:'#64748b',margin:0}}>Đăng nhập để quản lý tài khoản và Windows Agent của bạn.</p>
   <label style={{display:'grid',gap:7,fontSize:13}}>Email<input autoComplete="username" type="email" required value={email} onChange={e=>setEmail(e.target.value)} style={{border:'1px solid #d7dce7',padding:12,borderRadius:9,color:'#111827',background:'white'}}/></label>
   <label style={{display:'grid',gap:7,fontSize:13}}>Mật khẩu<input autoComplete="current-password" type="password" required value={password} onChange={e=>setPassword(e.target.value)} style={{border:'1px solid #d7dce7',padding:12,borderRadius:9,color:'#111827',background:'white'}}/></label>
   {error&&<p role="alert" style={{color:'#b91c1c',fontSize:13,margin:0}}>{error}</p>}
   <button type="submit" disabled={busy} style={{border:0,borderRadius:9,padding:13,color:'white',background:'#6553d7',fontWeight:700,cursor:'pointer'}}>{busy?'Đang đăng nhập...':'Đăng nhập'}</button>
  </form>
 </div>
 return <>{children(user,logout)}</>
}
