import { useEffect, useState } from 'react'
import { Button, Input, Select } from './ui/core'
import { Play, Pause, SkipBack, SkipForward, Square, RefreshCw, Trash2, Radio, Pencil } from 'lucide-react'
import './live.css'

type Video = {id:number;filename:string;duration_seconds:number|null}
type Playlist = {id:number;name:string;media_ids:number[]}
type Status = {connected:boolean;error?:string;obs_version?:string;media_state?:string;running:boolean;playlist_id:number|null;index:number;count:number;input_name:string;last_status:string;loop?:boolean;paused?:boolean;retry_count?:number;error_count?:number;transition_count?:number;recovery_count?:number;session_seconds?:number}
const apiRoot=((import.meta.env.VITE_API_URL as string|undefined)||'http://127.0.0.1:8000').replace(/\/$/,'')
async function call<T>(path:string,init?:RequestInit):Promise<T>{
  const r=await fetch(apiRoot+'/api'+path,init)
  if(!r.ok){const body=await r.json().catch(()=>({}));throw new Error(typeof body.detail==='string'?body.detail:'HTTP '+r.status)}
  return r.status===204?undefined as T:r.json() as Promise<T>
}
const json=(body:unknown):RequestInit=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
export default function LiveWorkspace({mode}:{mode:'playlists'|'live'}){
  const [videos,setVideos]=useState<Video[]>([])
  const [playlists,setPlaylists]=useState<Playlist[]>([])
  const [name,setName]=useState('')
  const [chosen,setChosen]=useState<number[]>([])
  const [selected,setSelected]=useState('')
  const [editingPlaylist,setEditingPlaylist]=useState<number|null>(null)
  const [seekMs,setSeekMs]=useState('0')
  const [host,setHost]=useState('127.0.0.1')
  const [port,setPort]=useState('4455')
  const [password,setPassword]=useState('')
  const [source,setSource]=useState('Sarae Video')
  const [dualMode,setDualMode]=useState(false)
  const [loop,setLoop]=useState(true)
  const [status,setStatus]=useState<Status|null>(null)
  const [error,setError]=useState('')
  const [notice,setNotice]=useState('')
  const [busy,setBusy]=useState(false)
  const refresh=async()=>{const [v,p,s]=await Promise.all([call<Video[]>('/media?limit=500'),call<Playlist[]>('/live/playlists'),call<Status>('/live/status')]);setVideos(v);setPlaylists(p);setStatus(s)}
  useEffect(()=>{void refresh().catch(e=>setError(String(e)));if(mode!=='live')return;const t=window.setInterval(()=>{void call<Status>('/live/status').then(setStatus).catch(()=>{})},3000);return()=>window.clearInterval(t)},[mode])
  const act=async(fn:()=>Promise<unknown>,message:string)=>{setBusy(true);setError('');setNotice('');try{await fn();setNotice(message);await refresh()}catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}}
  const add=(id:number)=>{setChosen(v=>[...v,id])}
  const beginEdit=(p:Playlist)=>{setEditingPlaylist(p.id);setName(p.name);setChosen([...p.media_ids]);setError('')}
  const resetEditor=()=>{setEditingPlaylist(null);setName('');setChosen([])}
  return <section className="live-workspace panel">
    <div className="live-head"><div><h3>{mode==='playlists'?'Playlist Engine':'OBS / LIVE Control'}</h3><p>{mode==='playlists'?'Tạo thứ tự phát từ video có trong thư viện.':'Thử phát lên OBS Preview trước khi phát LIVE thật.'}</p></div><Button variant="outline" leftIcon={<RefreshCw size={16}/>} onClick={()=>void act(async()=>{},'Đã cập nhật trạng thái')}>Làm mới</Button></div>
    {error&&<p className="live-message error" role="alert">{error}</p>}{notice&&<p className="live-message" role="status">{notice}</p>}
    {mode==='playlists'?<div className="live-columns">
      <div className="live-section"><h4>{editingPlaylist===null?'Tạo playlist':'Chỉnh sửa playlist #'+editingPlaylist}</h4><Input label="Tên playlist" value={name} onChange={e=>setName(e.target.value)} placeholder="VD: Test LIVE sáng"/>
        <div className="live-pick"><Select label="Thêm video" value="" options={[{value:'',label:'Chọn video…'},...videos.map(v=>({value:String(v.id),label:v.filename}))]} onChange={value=>{if(value)add(Number(value))}}/></div>
        <ol className="live-order">{chosen.map((id,i)=><li key={i}><span>{i+1}. {videos.find(v=>v.id===id)?.filename||'Video đã xóa'}</span><div><button onClick={()=>setChosen(s=>{const n=[...s];if(i>0)[n[i-1],n[i]]=[n[i],n[i-1]];return n})} disabled={i===0}>↑</button><button onClick={()=>setChosen(s=>{const n=[...s];if(i<n.length-1)[n[i+1],n[i]]=[n[i],n[i+1]];return n})} disabled={i===chosen.length-1}>↓</button><button onClick={()=>setChosen(s=>s.filter((_,j)=>i!==j))}>×</button></div></li>)}</ol>
        <Button disabled={busy||!name.trim()||!chosen.length} onClick={()=>void act(async()=>{await call(editingPlaylist===null?'/live/playlists':'/live/playlists/'+editingPlaylist,{...json({name,media_ids:chosen}),method:editingPlaylist===null?'POST':'PUT'});resetEditor()},'Đã tạo playlist')}>{editingPlaylist===null?'Lưu playlist':'Lưu thay đổi'}</Button>{editingPlaylist!==null&&<Button variant="outline" onClick={resetEditor}>Hủy sửa</Button>}
      </div><div className="live-section"><h4>Playlist đã lưu</h4>{!playlists.length?<p>Chưa có playlist.</p>:playlists.map(p=><div className="live-playlist" key={p.id}><div><strong>{p.name}</strong><small>{p.media_ids.length} video · {p.media_ids.map(id=>videos.find(v=>v.id===id)?.filename||'Thiếu file').join(' → ')}</small></div><button aria-label={'Sửa '+p.name} disabled={busy||!!(status?.running&&status.playlist_id===p.id)} onClick={()=>beginEdit(p)}><Pencil size={16}/></button><button aria-label={'Xóa '+p.name} disabled={busy} onClick={()=>{if(window.confirm('Xóa playlist này?'))void act(()=>call('/live/playlists/'+p.id,{method:'DELETE'}),'Đã xóa playlist')}}><Trash2 size={16}/></button></div>)}</div>
    </div>:<div className="live-columns">
      <div className="live-section"><h4>Kết nối OBS</h4><p>OBS → Tools → WebSocket Server Settings: bật server, port mặc định 4455. Tạo nguồn <b>Media Source</b> tên <b>Sarae Video</b>, tắt Loop.</p>
        <div className="live-inline"><Input label="Host" value={host} onChange={e=>setHost(e.target.value)}/><Input label="Port" value={port} onChange={e=>setPort(e.target.value)}/></div>
        <Input label="Mật khẩu OBS WebSocket" type="password" autoComplete="off" value={password} onChange={e=>setPassword(e.target.value)}/>
        <Input label="Tên Media Source trong OBS" value={source} onChange={e=>setSource(e.target.value)}/>
        <label className="live-dual-toggle"><input type="checkbox" checked={dualMode} onChange={e=>setDualMode(e.target.checked)}/><span><strong>Phát A/B (giảm nháy khi Next)</strong><small>Cần tạo 2 Media Sources: Sarae Video A và Sarae Video B trong cùng scene.</small></span></label>
        <Button disabled={busy} onClick={()=>void act(()=>call('/live/config',json({host,port:Number(port),password,input_name:source,dual_mode:dualMode})),'Đã lưu kết nối OBS trong phiên Backend')}>Kết nối / Kiểm tra</Button>
        <div className={'live-connection '+(status?.connected?'online':'offline')}><Radio size={16}/>{status?.connected?'Đã kết nối OBS '+(status.obs_version||''):status?.error||'OBS chưa kết nối'}</div>
      </div><div className="live-section"><h4>Điều khiển phát thử</h4>
        <Select label="Playlist" value={selected} options={[{value:'',label:'Chọn playlist…'},...playlists.map(p=>({value:String(p.id),label:p.name}))]} onChange={setSelected}/>
        <label className="live-dual-toggle"><input type="checkbox" checked={loop} onChange={e=>setLoop(e.target.checked)}/><span><strong>Lặp playlist liên tục</strong><small>Hết video cuối sẽ phát lại video đầu tiên.</small></span></label>
        <div className="live-controls"><Button disabled={busy||!selected||!!status?.running} leftIcon={<Play size={16}/>} onClick={()=>void act(()=>call('/live/start',json({playlist_id:Number(selected),start_index:0,loop})),'Đã gửi lệnh phát lên OBS')}>Phát thử</Button><Button disabled={busy||!status?.running} variant="outline" leftIcon={<SkipBack size={16}/>} onClick={()=>void act(()=>call('/live/previous',{method:'POST'}),'Đã quay lại video trước')}>Previous</Button><Button disabled={busy||!status?.running} variant="outline" leftIcon={<SkipForward size={16}/>} onClick={()=>void act(()=>call('/live/next',{method:'POST'}),'Đã chuyển video')}>Next</Button><Button disabled={busy||!status?.running} variant="danger" leftIcon={<Square size={16}/>} onClick={()=>void act(()=>call('/live/stop',{method:'POST'}),'Đã dừng phát')}>Stop</Button><Button disabled={busy||!status?.running} variant="outline" leftIcon={status?.paused?<Play size={16}/>:<Pause size={16}/>} onClick={()=>void act(()=>call(status?.paused?'/live/resume':'/live/pause',{method:'POST'}),status?.paused?'Đã tiếp tục phát':'Đã tạm dừng')}>{status?.paused?'Resume':'Pause'}</Button></div>
        <div className="live-pick"><Select label="Phát video trong playlist" value="" options={[{value:"",label:"Chọn video để phát ngay…"},...(playlists.find(p=>p.id===status?.playlist_id)?.media_ids||[]).map((id,i)=>({value:String(i),label:(i+1)+". "+(videos.find(v=>v.id===id)?.filename||"Video không tồn tại")}))]} onChange={value=>{if(value!==""&&status?.running)void act(()=>call('/live/select',json({index:Number(value)})),'Đã phát video được chọn')}}/></div>
        <div className="live-inline"><Input label="Tua tới (giây)" type="number" min="0" value={seekMs} onChange={e=>setSeekMs(e.target.value)}/><Button disabled={busy||!status?.running||!Number.isFinite(Number(seekMs))||Number(seekMs)<0} onClick={()=>void act(()=>call('/live/seek',json({milliseconds:Math.round(Number(seekMs)*1000)})),'Đã gửi lệnh Seek')}>Seek</Button></div>
        <div className="live-now"><b>{status?.paused?'Đã tạm dừng':status?.running?'Đang phát trên OBS':'Chưa phát'}</b><span>Playlist #{status?.playlist_id??'—'} · Video {(status?.index??0)+1}/{status?.count??0}</span><span>{status?.last_status||'Chưa có hoạt động'}</span><span>Trạng thái OBS: {status?.media_state||'—'}</span><span>Loop: {status?.loop?'Bật':'Tắt'} · Retry: {status?.retry_count??0}/5</span><span>Thời gian phiên: {Math.floor((status?.session_seconds??0)/3600)}h {Math.floor(((status?.session_seconds??0)%3600)/60)}m · Chuyển video: {status?.transition_count??0}</span><span>Lỗi: {status?.error_count??0} · Phục hồi: {status?.recovery_count??0}</span></div>
        <p className="live-disclaimer">Bản test chỉ cập nhật Media Source trong OBS; không tự bật Start Streaming. Khi phát thử ổn mới kết nối nền tảng LIVE.</p>
      </div>
    </div>}
  </section>
}
