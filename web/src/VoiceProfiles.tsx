import {useEffect,useRef,useState} from 'react'
import {Plus,RefreshCw,Pencil,Trash2,Play} from 'lucide-react'
import {Alert,Badge,Button,ConfirmModal,Modal,EmptyState} from './ui/core'
import {errorText} from './mediaApi'
import {qcWarnings,requireVoiceIdle,voiceApi,type VoiceProfile} from './voiceApi'

export default function VoiceProfiles({profiles,selected,busy,onReload,onPreview}:{profiles:VoiceProfile[];selected:string;busy:boolean;onReload:()=>Promise<void>;onPreview:(p:VoiceProfile)=>void}){
 const [editor,setEditor]=useState<VoiceProfile|'new'|null>(null)
 const [remove,setRemove]=useState<VoiceProfile|null>(null)
 const [working,setWorking]=useState(false)
 const [error,setError]=useState('')
 const [notice,setNotice]=useState('')
 const lock=useRef(false)
 const run=async(action:()=>Promise<void>)=>{if(lock.current)return;lock.current=true;setWorking(true);setError('');setNotice('');try{await action();await onReload()}catch(e){setError(errorText(e,'Không xử lý được giọng mẫu'))}finally{lock.current=false;setWorking(false)}}
 return <div className="voice-profiles">
  <div className="voice-section-head"><div><h4>Giọng mẫu của bạn</h4><p>WAV gốc được giữ nguyên. Transcript tự động bằng Whisper là mặc định.</p></div><div className="ais-voice-actions"><Button size="sm" variant="ghost" disabled={busy||working} onClick={()=>void run(async()=>{setNotice('Đã tải lại danh sách giọng')})}>Tải lại danh sách</Button><Button size="sm" disabled={busy||working} leftIcon={<Plus size={15}/>} onClick={()=>setEditor('new')}>Tạo giọng</Button></div></div>
  {!profiles.length?<EmptyState title="Chưa có giọng mẫu" description="Tạo giọng bằng một WAV rõ tiếng, nên dài 3–10 giây."/>:<div className="voice-profile-list">{profiles.map(p=><article className="voice-profile" key={p.id}>
   <div className="voice-section-head"><strong>{p.name} {p.id===selected&&<Badge tone="primary">Đang chọn</Badge>}</strong><Badge tone={p.sync.up_to_date?'success':p.sync.state==='FAILED'?'danger':'warning'}>{p.sync.up_to_date?'Đã đồng bộ':p.sync.state==='FAILED'?'Đồng bộ lỗi':'Chờ đồng bộ'}</Badge></div>
   <small>{p.wav.duration_s.toFixed(1)} giây · {p.transcript.source==='asr'?'Auto Transcript':'Manual Transcript'} · revision {p.revision} · {new Date(p.updated_at).toLocaleString('vi-VN')}</small>
   {qcWarnings(p).map(w=><p className="voice-warning" key={w}>{w}</p>)}
   {p.sync.error&&<p className="voice-warning">{p.sync.error}</p>}
   <div className="voice-profile-actions">
    <Button size="sm" variant="ghost" disabled={working||busy} leftIcon={<Pencil size={14}/>} onClick={()=>setEditor(p)}>Chỉnh sửa</Button>
    <Button size="sm" variant="ghost" disabled={working||busy} leftIcon={<RefreshCw size={14}/>} onClick={()=>void run(async()=>{await voiceApi.sync(p.id);setNotice('Đã đồng bộ giọng '+p.name)})}>Đồng bộ lại</Button>
    <Button size="sm" variant="ghost" disabled={working||busy||!p.sync.up_to_date} leftIcon={<Play size={14}/>} onClick={()=>onPreview(p)}>Nghe thử giọng</Button>
    <Button size="sm" variant="ghost" disabled={working||busy||p.id===selected} title={p.id===selected?'Đổi giọng mặc định trước khi xóa':undefined} leftIcon={<Trash2 size={14}/>} onClick={()=>setRemove(p)}>Xóa</Button>
   </div>
  </article>)}</div>}
  {error&&<Alert tone="danger">{error}</Alert>}{notice&&<Alert tone="success">{notice}</Alert>}
  {editor&&<ProfileEditor profile={editor} onClose={()=>setEditor(null)} onSaved={async()=>{await onReload();setEditor(null);setNotice('Đã lưu giọng mẫu')}}/>}
  <ConfirmModal open={!!remove} tone="danger" title="Xóa giọng mẫu?" description={`Xóa “${remove?.name||''}” và WAV gốc của giọng này. Thao tác không thể hoàn tác.`} loading={working} onClose={()=>!working&&setRemove(null)} onConfirm={()=>void run(async()=>{await requireVoiceIdle();await voiceApi.remove(remove!.id);setRemove(null);setNotice('Đã xóa giọng mẫu')})}/>
 </div>
}

function ProfileEditor({profile,onClose,onSaved}:{profile:VoiceProfile|'new';onClose:()=>void;onSaved:()=>Promise<void>}){
 const current=profile==='new'?null:profile
 const [name,setName]=useState(current?.name||'')
 const [manual,setManual]=useState(current?.transcript.source==='manual')
 const [transcript,setTranscript]=useState(current?.transcript.effective||'')
 const [file,setFile]=useState<File|null>(null)
 const [busy,setBusy]=useState(false)
 const [error,setError]=useState('')
 const [audio,setAudio]=useState('')
 const lock=useRef(false)
 useEffect(()=>()=>{if(audio)URL.revokeObjectURL(audio)},[audio])
 const validFile=(wav:File)=>{if(!wav.name.toLowerCase().endsWith('.wav'))throw new Error('Chỉ nhận file WAV PCM.');if(wav.size>5*1024*1024)throw new Error('WAV tối đa 5 MB.')}
 const run=async(action:()=>Promise<void>)=>{if(lock.current)return;lock.current=true;setBusy(true);setError('');try{await action()}catch(e){setError(errorText(e,'Không lưu được giọng mẫu'))}finally{lock.current=false;setBusy(false)}}
 const save=async()=>{
  if(file)validFile(file)
  if(manual&&!transcript.trim())throw new Error('Nhập transcript thủ công hoặc chọn Auto Transcript.')
  if(!current){const form=new FormData();form.set('name',name.trim());form.set('wav',file!);if(manual)form.set('transcript',transcript.trim());await voiceApi.create(form)}
  else{
   const changedTranscript=manual!== (current.transcript.source==='manual') || (manual&&transcript.trim()!==current.transcript.manual)
   if(file||changedTranscript){await requireVoiceIdle();if(!window.confirm('Thay WAV hoặc transcript sẽ tăng revision và tạo lại cache giọng. Tiếp tục?'))return}
   if(file){const form=new FormData();form.set('wav',file);await voiceApi.replace(current.id,form)}
   await voiceApi.patch(current.id,{name:name.trim(),...(changedTranscript||file?{transcript:manual?transcript.trim():null}:{})})
  }
  await onSaved()
 }
 return <Modal open title={current?'Chỉnh sửa giọng mẫu':'Tạo giọng mẫu'} size="md" onClose={()=>!busy&&onClose()} footer={<><Button variant="ghost" disabled={busy} onClick={onClose}>Hủy</Button><Button loading={busy} disabled={!name.trim()||(!current&&!file)} onClick={()=>void run(save)}>{current?'Lưu giọng mẫu':'Tạo giọng'}</Button></>}>
  <div className="voice-editor">
   <label>Tên giọng<input autoFocus maxLength={100} value={name} onChange={e=>setName(e.target.value)} disabled={busy}/></label>
   <label>{current?'Thay WAV (tùy chọn)':'WAV giọng mẫu'}<input aria-label="WAV giọng mẫu" type="file" accept=".wav,audio/wav" disabled={busy} onChange={e=>{setFile(e.target.files?.[0]||null);if(current){setManual(false);setTranscript('')}}}/></label>
   <p>WAV PCM, 1–20 giây, tối đa 5 MB. Nên dùng 3–10 giây, có khoảng lặng tự nhiên ở đầu và cuối.</p>
   {current&&<><Button size="sm" variant="ghost" disabled={busy} onClick={()=>void run(async()=>{setAudio(URL.createObjectURL(await voiceApi.wav(current.id)))})}>Nghe WAV gốc</Button>{audio&&<audio controls src={audio}/>}</>}
   <label>Transcript<select aria-label="Transcript" disabled={busy} value={manual?'manual':'auto'} onChange={e=>setManual(e.target.value==='manual')}><option value="auto">Auto — Whisper nhận dạng</option><option value="manual">Manual — nhập thủ công (nâng cao)</option></select></label>
   {manual?<label>Nội dung WAV (tối đa 1000 ký tự)<textarea rows={5} disabled={busy} value={transcript} onChange={e=>setTranscript(e.target.value)} maxLength={1000}/></label>:<p>{current?.transcript.asr?`Whisper: ${current.transcript.asr}`:'Không cần nhập transcript. Whisper nhận dạng khi đồng bộ giọng.'}</p>}
   {current&&<p>Thay WAV sẽ quay về Auto Transcript. WAV hoặc transcript mới làm tăng revision và cập nhật cache; các job đã tạo giữ revision cũ.</p>}
   {error&&<Alert tone="danger">{error}</Alert>}
  </div>
 </Modal>
}
