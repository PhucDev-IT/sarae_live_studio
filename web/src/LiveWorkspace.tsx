import { useEffect, useState } from 'react'
import { Button, Input, Select } from './ui/core'
import { RefreshCw, Trash2, Pencil } from 'lucide-react'
import { apiRequest } from './mediaApi'
import './live.css'

type Video = {id:number;filename:string;duration_seconds:number|null}
type Playlist = {id:number;name:string;media_ids:number[]}
/** Only the fields this page needs: a playlist that is playing cannot be edited. */
type Status = {running:boolean;playlist_id:number|null}
const call = apiRequest
const json=(body:unknown):RequestInit=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
/** Playlist editor for manual OBS test playback; the LIVE console lives in LiveControl. */
export default function LiveWorkspace(){
  const [videos,setVideos]=useState<Video[]>([])
  const [playlists,setPlaylists]=useState<Playlist[]>([])
  const [name,setName]=useState('')
  const [chosen,setChosen]=useState<number[]>([])
  const [editingPlaylist,setEditingPlaylist]=useState<number|null>(null)
  const [status,setStatus]=useState<Status|null>(null)
  const [error,setError]=useState('')
  const [notice,setNotice]=useState('')
  const [busy,setBusy]=useState(false)
  const refresh=async()=>{const [v,p,s]=await Promise.all([call<Video[]>('/media?limit=500'),call<Playlist[]>('/live/playlists'),call<Status>('/live/status')]);setVideos(v);setPlaylists(p);setStatus(s)}
  useEffect(()=>{void refresh().catch(e=>setError(String(e)))},[])
  const act=async(fn:()=>Promise<unknown>,message:string)=>{setBusy(true);setError('');setNotice('');try{await fn();setNotice(message);await refresh()}catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}}
  const add=(id:number)=>{setChosen(v=>[...v,id])}
  const beginEdit=(p:Playlist)=>{setEditingPlaylist(p.id);setName(p.name);setChosen([...p.media_ids]);setError('')}
  const resetEditor=()=>{setEditingPlaylist(null);setName('');setChosen([])}
  return <section className="live-workspace panel">
    <div className="live-head"><div><h3>Playlist Engine</h3><p>Tạo thứ tự phát từ video có trong thư viện.</p></div><Button variant="outline" leftIcon={<RefreshCw size={16}/>} onClick={()=>void act(async()=>{},'Đã cập nhật trạng thái')}>Làm mới</Button></div>
    {error&&<p className="live-message error" role="alert">{error}</p>}{notice&&<p className="live-message" role="status">{notice}</p>}
    <div className="live-columns">
      <div className="live-section"><h4>{editingPlaylist===null?'Tạo playlist':'Chỉnh sửa playlist #'+editingPlaylist}</h4><Input label="Tên playlist" value={name} onChange={e=>setName(e.target.value)} placeholder="VD: Test LIVE sáng"/>
        <div className="live-pick"><Select label="Thêm video" value="" options={[{value:'',label:'Chọn video…'},...videos.map(v=>({value:String(v.id),label:v.filename}))]} onChange={value=>{if(value)add(Number(value))}}/></div>
        <ol className="live-order">{chosen.map((id,i)=><li key={i}><span>{i+1}. {videos.find(v=>v.id===id)?.filename||'Video đã xóa'}</span><div><button onClick={()=>setChosen(s=>{const n=[...s];if(i>0)[n[i-1],n[i]]=[n[i],n[i-1]];return n})} disabled={i===0}>↑</button><button onClick={()=>setChosen(s=>{const n=[...s];if(i<n.length-1)[n[i+1],n[i]]=[n[i],n[i+1]];return n})} disabled={i===chosen.length-1}>↓</button><button onClick={()=>setChosen(s=>s.filter((_,j)=>i!==j))}>×</button></div></li>)}</ol>
        <Button disabled={busy||!name.trim()||!chosen.length} onClick={()=>void act(async()=>{await call(editingPlaylist===null?'/live/playlists':'/live/playlists/'+editingPlaylist,{...json({name,media_ids:chosen}),method:editingPlaylist===null?'POST':'PUT'});resetEditor()},'Đã tạo playlist')}>{editingPlaylist===null?'Lưu playlist':'Lưu thay đổi'}</Button>{editingPlaylist!==null&&<Button variant="outline" onClick={resetEditor}>Hủy sửa</Button>}
      </div><div className="live-section"><h4>Playlist đã lưu</h4>{!playlists.length?<p>Chưa có playlist.</p>:playlists.map(p=><div className="live-playlist" key={p.id}><div><strong>{p.name}</strong><small>{p.media_ids.length} video · {p.media_ids.map(id=>videos.find(v=>v.id===id)?.filename||'Thiếu file').join(' → ')}</small></div><button aria-label={'Sửa '+p.name} disabled={busy||!!(status?.running&&status.playlist_id===p.id)} onClick={()=>beginEdit(p)}><Pencil size={16}/></button><button aria-label={'Xóa '+p.name} disabled={busy} onClick={()=>{if(window.confirm('Xóa playlist này?'))void act(()=>call('/live/playlists/'+p.id,{method:'DELETE'}),'Đã xóa playlist')}}><Trash2 size={16}/></button></div>)}</div>
    </div>
  </section>
}
