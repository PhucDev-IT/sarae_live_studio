import { useCallback, useEffect, useState, type ChangeEvent } from 'react'
import { apiRequest, errorText, type OwnerAgent } from './mediaApi'
import { Button, Card, useToast } from './ui/core'

type Video = {media_id:string;filename:string;size_bytes:number;modified_ns:number}
const label:Record<string,string> = {android_play:'Phát ngay',android_switch:'Chuyển video',android_pause:'Tạm dừng',android_resume:'Tiếp tục',android_next:'Video tiếp',android_previous:'Video trước',android_stop:'Dừng',android_queue_set:'Cập nhật playlist',android_rotation_set:'Auto rotation',android_mock_live_start:'Bật Mock LIVE',android_mock_live_stop:'Tắt Mock LIVE'}
const commands = (agentId:string, kind:string, payload:Record<string,unknown>={}) => apiRequest(`/agents/${encodeURIComponent(agentId)}/commands`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,payload,wait:0})})

/** Android Agent controls and MP4 upload. Mock LIVE is an internal player state and never opens TikTok. */
export default function AndroidAgentPanel({agent}:{agent:OwnerAgent}) {
  const toast = useToast()
  const [videos,setVideos] = useState<Video[]>([])
  const [queue,setQueue] = useState<string[]>([])
  const [busy,setBusy] = useState(false)
  const [rotation,setRotation] = useState(true)
  const [log,setLog] = useState<string[]>([])
  const refresh = useCallback(async()=>{
    try{setVideos((await apiRequest<{videos:Video[]}>(`/android-agents/${encodeURIComponent(agent.id)}/videos`)).videos)}
    catch(e){setLog(v=>[`Lỗi tải danh sách: ${errorText(e,'')}`,...v].slice(0,8))}
  },[agent.id])
  useEffect(()=>{void refresh();const timer=window.setInterval(()=>void refresh(),5000);return()=>window.clearInterval(timer)},[refresh])
  const send = async(kind:string,payload:Record<string,unknown>={})=>{
    setBusy(true)
    try{await commands(agent.id,kind,payload);setLog(v=>[`${new Date().toLocaleTimeString()} · ${label[kind]??kind}: đã gửi`,...v].slice(0,8));toast.push({tone:'success',title:'Đã gửi lệnh Android'})}
    catch(e){toast.push({tone:'danger',title:'Lệnh thất bại',description:errorText(e,'')})}
    finally{setBusy(false)}
  }
  const upload = async(event:ChangeEvent<HTMLInputElement>)=>{
    const file=event.target.files?.[0];if(!file)return
    if(!file.name.toLowerCase().endsWith('.mp4')){toast.push({tone:'danger',title:'Chọn file MP4'});event.target.value='';return}
    const form=new FormData();form.append('video',file)
    setBusy(true)
    try{await apiRequest(`/android-agents/${encodeURIComponent(agent.id)}/videos`,{method:'POST',body:form});await refresh();toast.push({tone:'success',title:`Đã tải lên ${file.name}`})}
    catch(e){toast.push({tone:'danger',title:'Upload lỗi',description:errorText(e,'')})}
    finally{setBusy(false);event.target.value=''}
  }
  const toggle=(id:string)=>setQueue(v=>v.includes(id)?v.filter(x=>x!==id):[...v,id])
  return <div style={{display:'grid',gap:16,marginTop:20}}>
    <Card title="Android Agent · Mock LIVE" description="Phát và điều khiển video trong ứng dụng Android. Mock LIVE chỉ kiểm tra playback; không mở TikTok hoặc LIVE thật.">
      <div style={{display:'flex',gap:8,flexWrap:'wrap',marginBottom:12}}>
        <Button disabled={busy||!agent.online} onClick={()=>void send('android_mock_live_start')}>Bật Mock LIVE</Button>
        <Button disabled={busy||!agent.online} variant="ghost" onClick={()=>void send('android_mock_live_stop')}>Tắt Mock LIVE</Button>
        <Button disabled={busy||!agent.online} variant="ghost" onClick={()=>void send('android_pause')}>Tạm dừng</Button>
        <Button disabled={busy||!agent.online} variant="ghost" onClick={()=>void send('android_resume')}>Tiếp tục</Button>
        <Button disabled={busy||!agent.online} variant="ghost" onClick={()=>void send('android_previous')}>Trước</Button>
        <Button disabled={busy||!agent.online} variant="ghost" onClick={()=>void send('android_next')}>Tiếp</Button>
        <Button disabled={busy||!agent.online} variant="ghost" onClick={()=>void send('android_stop')}>Dừng</Button>
      </div>
      <label style={{display:'flex',gap:8,alignItems:'center'}}><input type="checkbox" checked={rotation} onChange={e=>{setRotation(e.target.checked);void send('android_rotation_set',{enabled:e.target.checked})}}/>Tự chuyển video khi kết thúc</label>
      {log.length>0&&<pre style={{whiteSpace:'pre-wrap',fontSize:12}}>{log.join('\n')}</pre>}
    </Card>
    <Card title="Video Android" description="MP4 được lưu trong thư viện Android Agent trên backend; thiết bị tải xuống local qua HTTPS.">
      <div style={{display:'flex',gap:12,alignItems:'center',marginBottom:12}}><input type="file" accept="video/mp4,.mp4" disabled={busy} onChange={e=>void upload(e)}/><Button variant="ghost" onClick={()=>void refresh()}>Làm mới</Button></div>
      {!videos.length?<p>Chưa có video. Tải MP4 lên để đồng bộ về điện thoại.</p>:<div style={{display:'grid',gap:8}}>{videos.map(v=><label key={v.media_id} style={{display:'flex',gap:10,alignItems:'center',padding:8,border:'1px solid var(--border,#ddd)',borderRadius:8}}>
        <input type="checkbox" checked={queue.includes(v.media_id)} onChange={()=>toggle(v.media_id)}/><span style={{flex:1}}><strong>{v.filename}</strong><small style={{display:'block'}}>{(v.size_bytes/1024/1024).toFixed(1)} MB · {v.media_id.slice(0,12)}</small></span>
        <Button disabled={busy||!agent.online} onClick={()=>void send('android_play',{media_id:v.media_id})}>Phát ngay</Button>
        <Button disabled={busy||!agent.online} variant="ghost" onClick={()=>void send('android_switch',{media_id:v.media_id,mode:'after_current'})}>Sau hiện tại</Button>
      </label>)}</div>}
      <div style={{display:'flex',gap:8,marginTop:12}}><Button disabled={busy||!agent.online||!queue.length} onClick={()=>void send('android_queue_set',{media_ids:queue,loop:true,auto_rotate:rotation})}>Đặt playlist ({queue.length})</Button></div>
    </Card>
  </div>
}
