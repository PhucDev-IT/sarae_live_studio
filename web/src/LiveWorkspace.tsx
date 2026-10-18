import { useEffect, useMemo, useState } from 'react'
import { Alert, Badge, Button, Card, EmptyState, Input, Select, Switch, useToast } from './ui/core'
import { ArrowDown, ArrowUp, ListVideo, Pencil, Play, Save, Trash2, X } from 'lucide-react'
import { agentAction, apiRequest, durationLabel, errorText, listVideos, onAgent, type Media, type OwnerAgent } from './mediaApi'
import { WithAgent } from './agentContext'
import './live.css'

type Playlist = { id:string; agent_id:string; name:string; media_ids:string[]; loop:boolean }
const json = (body:unknown, method:string):RequestInit => ({method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})

/** Test playlists on the tab's Windows Agent: they hold that PC's video ids and only ever play on that PC. */
export default function LiveWorkspace(){
  return <WithAgent purpose="Playlist phát thử">{agent=><Playlists key={agent.id} agent={agent}/>}</WithAgent>
}

function Playlists({agent}:{agent:OwnerAgent}){
  const toast = useToast()
  const [videos,setVideos] = useState<Media[]>([])
  const [playlists,setPlaylists] = useState<Playlist[]>([])
  const [name,setName] = useState('')
  const [loop,setLoop] = useState(true)
  const [chosen,setChosen] = useState<string[]>([])
  const [editing,setEditing] = useState<string|null>(null)
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')

  const refresh = async () => {
    const [v, p] = await Promise.all([listVideos(agent.id), apiRequest<Playlist[]>(onAgent('/agent-playlists', agent.id))])
    setVideos(v.filter(x=>x.available));setPlaylists(p)
  }
  useEffect(()=>{void refresh().catch(e=>setError(errorText(e, 'Không tải được dữ liệu')))},[])
  const byId = useMemo(()=>new Map(videos.map(v=>[v.id,v])),[videos])
  const label = (id:string) => byId.get(id)?.filename ?? 'Video không còn trên máy'

  const act = async (job:()=>Promise<unknown>, success:string) => {
    setBusy(true);setError('')
    try{await job();toast.push({tone:'success', title:success});await refresh()}
    catch(e){toast.push({tone:'danger', title:'Không thực hiện được', description:errorText(e, '')})}
    finally{setBusy(false)}
  }
  const reset = () => {setEditing(null);setName('');setChosen([]);setLoop(true)}
  const edit = (p:Playlist) => {setEditing(p.id);setName(p.name);setChosen([...p.media_ids]);setLoop(p.loop)}
  const move = (i:number, to:number) => setChosen(list=>{const next = [...list];[next[i], next[to]] = [next[to], next[i]];return next})
  const save = () => act(async ()=>{
    const body = {agent_id:agent.id, name:name.trim(), media_ids:chosen, loop}
    await apiRequest(editing ? `/agent-playlists/${editing}` : '/agent-playlists', json(body, editing ? 'PUT' : 'POST'))
    reset()
  }, editing ? 'Đã lưu playlist' : 'Đã tạo playlist')

  return <div className="live-columns lw">
    {error && <div className="lw-error"><Alert tone="danger">{error}</Alert></div>}
    <Card title={editing ? 'Sửa playlist' : 'Tạo playlist'} description={`Video trên máy ${agent.hostname}`}>
      <div className="live-section">
        <Input label="Tên playlist" value={name} onChange={e=>setName(e.target.value)} placeholder="VD: Test LIVE sáng"/>
        <Select label="Thêm video" value="" onChange={value=>{if (value) setChosen(list=>list.includes(value) ? list : [...list, value])}}
          options={[{value:'', label:videos.length ? 'Chọn video…' : 'Máy này chưa có video'}, ...videos.filter(v=>!chosen.includes(v.id)).map(v=>({value:v.id, label:v.filename}))]}/>
        <ol className="live-order">{chosen.map((id,i)=><li key={id}>
          <span>{i + 1}. {label(id)} <small>{durationLabel(byId.get(id)?.duration_seconds) ?? ''}</small></span>
          <div>
            <button type="button" aria-label="Đưa lên" disabled={i === 0} onClick={()=>move(i, i - 1)}><ArrowUp size={14}/></button>
            <button type="button" aria-label="Đưa xuống" disabled={i === chosen.length - 1} onClick={()=>move(i, i + 1)}><ArrowDown size={14}/></button>
            <button type="button" aria-label="Bỏ khỏi playlist" onClick={()=>setChosen(list=>list.filter(x=>x !== id))}><X size={14}/></button>
          </div>
        </li>)}</ol>
        <Switch label="Phát lặp liên tục" checked={loop} onChange={e=>setLoop(e.target.checked)}/>
        <div className="live-row">
          <Button leftIcon={<Save size={16}/>} disabled={busy || !name.trim() || !chosen.length} onClick={()=>void save()}>{editing ? 'Lưu thay đổi' : 'Lưu playlist'}</Button>
          {editing && <Button variant="ghost" onClick={reset}>Hủy sửa</Button>}
        </div>
      </div>
    </Card>
    <Card title="Playlist đã lưu" description="Chỉ dùng để phát thử. Phiên LIVE theo sản phẩm được điều khiển ở trang Điều khiển LIVE.">
      {!playlists.length ? <EmptyState icon={<ListVideo size={28}/>} title="Chưa có playlist" description="Tạo playlist từ video có trên máy này."/>
        : <div className="live-section">{playlists.map(p=><div className="live-playlist" key={p.id}>
          <div><strong>{p.name} {p.loop && <Badge tone="info">Lặp</Badge>}</strong><small>{p.media_ids.length} video · {p.media_ids.map(label).join(' → ')}</small></div>
          <button type="button" aria-label={'Phát thử ' + p.name} title={agent.online ? 'Phát thử trên OBS của máy này' : 'Agent đang offline'} disabled={busy || !agent.online}
            onClick={()=>void act(()=>agentAction(agent.id, `/agent-playlists/${p.id}/play`, {method:'POST'}), `Đã phát “${p.name}” trên ${agent.hostname}`)}><Play size={16}/></button>
          <button type="button" aria-label={'Sửa ' + p.name} disabled={busy} onClick={()=>edit(p)}><Pencil size={16}/></button>
          <button type="button" aria-label={'Xóa ' + p.name} disabled={busy} onClick={()=>{if (window.confirm(`Xóa playlist “${p.name}”?`)) void act(()=>apiRequest(`/agent-playlists/${p.id}`, {method:'DELETE'}), 'Đã xóa playlist')}}><Trash2 size={16}/></button>
        </div>)}</div>}
    </Card>
  </div>
}
