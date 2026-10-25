import {useEffect,useRef,useState} from 'react'
import {KeyRound,Play,Save,Trash2,Volume2} from 'lucide-react'
import {api,apiRequest,errorText} from './mediaApi'
import {authHeaders} from './authToken'

type Provider='edge'|'elevenlabs'
type VoiceConfig={provider:Provider;model:string;voice:string;rate:number;settings:Record<string,number|boolean>;has_api_key?:boolean}
type Choice={id:string;name:string;can_use_style?:boolean;can_use_speaker_boost?:boolean}
type Catalog={models:Choice[];voices:Choice[];error?:string}
const initial:VoiceConfig={provider:'edge',model:'edge-neural',voice:'vi-VN-HoaiMyNeural',rate:5,settings:{}}
const edgeVoices=[{id:'vi-VN-HoaiMyNeural',name:'Hoài My (nữ)'},{id:'vi-VN-NamMinhNeural',name:'Nam Minh (nam)'}]
const json=(method:string,body:unknown):RequestInit=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})

export default function VoiceSettings(){
 const [config,setConfig]=useState<VoiceConfig>(initial)
 const [catalog,setCatalog]=useState<Catalog>({models:[],voices:[]})
 const [key,setKey]=useState('')
 const [sample,setSample]=useState('Xin chào, đây là giọng nói thử của Sarae.')
 const [busy,setBusy]=useState(false)
 const [message,setMessage]=useState('')
 const [error,setError]=useState('')
 const audio=useRef<HTMLAudioElement|null>(null)
 const load=async()=>{
  const c=await apiRequest<VoiceConfig>('/ai/voice/config')
  setConfig(c)
  if(c.has_api_key)setCatalog(await apiRequest<Catalog>('/ai/voice/catalog'))
 }
 useEffect(()=>{void load().catch(e=>setError(errorText(e,'Không tải được cấu hình giọng nói')));return()=>audio.current?.pause()},[])
 const run=async(action:()=>Promise<void>)=>{setBusy(true);setError('');setMessage('');try{await action()}catch(e){setError(errorText(e,'Thao tác giọng nói thất bại'))}finally{setBusy(false)}}
 const update=(patch:Partial<VoiceConfig>)=>setConfig(c=>({...c,...patch}))
 const setting=(name:string,value:number|boolean)=>setConfig(c=>({...c,settings:{...c.settings,[name]:value}}))
 const save=async()=>{
  const c=await apiRequest<VoiceConfig>('/ai/voice/config',json('PUT',config))
  setConfig(c);setMessage('Đã lưu cấu hình giọng nói cho Agent')
 }
 const test=async()=>{
  await save()
  audio.current?.pause()
  const response=await fetch(api+'/api/ai/voice/preview',{method:'POST',headers:{'Content-Type':'application/json',...authHeaders()},body:JSON.stringify({text:sample})})
  if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(body.detail||`HTTP ${response.status}`)}
  const url=URL.createObjectURL(await response.blob())
  const player=new Audio(url);audio.current=player
  player.onended=()=>URL.revokeObjectURL(url)
  player.onerror=()=>URL.revokeObjectURL(url)
  await player.play();setMessage('Đang phát giọng thử')
 }
 const models=config.provider==='edge'?[{id:'edge-neural',name:'Edge Neural'}]:catalog.models
 const voices=config.provider==='edge'?edgeVoices:catalog.voices
 const selected=models.find(m=>m.id===config.model)
 return <section className="ais-panel ais-voice-panel">
  <h3><Volume2 size={19}/> Voice Settings</h3>
  <p>Cấu hình này áp dụng cho câu trả lời LIVE, âm thanh chuẩn bị trước và phần nghe thử. Đổi provider sẽ áp dụng cho câu mới gửi tới Agent.</p>
  <div className="ais-fields">
   <label>Provider<select disabled={busy} value={config.provider} onChange={e=>{const provider=e.target.value as Provider;update(provider==='edge'?{provider,model:'edge-neural',voice:edgeVoices[0].id,settings:{},rate:5}:{provider,model:'eleven_flash_v2_5',voice:catalog.voices[0]?.id||'',settings:{stability:0.5,similarity_boost:0.75,speed:1},rate:0})}}><option value="edge">Microsoft Edge TTS</option><option value="elevenlabs">ElevenLabs</option></select></label>
   <label>Model<select disabled={busy} value={models.some(m=>m.id===config.model)?config.model:'custom'} onChange={e=>{const next=models.find(m=>m.id===e.target.value);const settings={...config.settings};if(next&&!next.can_use_style)delete settings.style;if(next&&!next.can_use_speaker_boost)delete settings.use_speaker_boost;update({model:e.target.value==='custom'?'':e.target.value,settings})}}>{models.map(m=><option key={m.id} value={m.id}>{m.name}</option>)}<option value="custom">Nhập model ID khác…</option></select></label>
   {!models.some(m=>m.id===config.model)&&<label>Model ID<input value={config.model} onChange={e=>update({model:e.target.value})} placeholder="eleven_flash_v2_5"/></label>}
   <label>Voice<select disabled={busy} value={voices.some(v=>v.id===config.voice)?config.voice:'custom'} onChange={e=>update({voice:e.target.value==='custom'?'':e.target.value})}>{voices.map(v=><option key={v.id} value={v.id}>{v.name}</option>)}<option value="custom">Nhập voice ID khác…</option></select></label>
   {!voices.some(v=>v.id===config.voice)&&<label>Voice ID<input value={config.voice} onChange={e=>update({voice:e.target.value})} placeholder="Voice ID từ ElevenLabs"/></label>}
   {config.provider==='edge'&&<label>Tốc độ (%)<input type="number" min={-30} max={50} value={config.rate} onChange={e=>update({rate:Number(e.target.value)})}/></label>}
  </div>
  {config.provider==='elevenlabs'&&<>
   <p>Danh sách model và voice được tải từ tài khoản ElevenLabs. Bạn có thể nhập ID khác nếu danh sách chưa có.</p>
   <div className="ais-voice-options">
    {(['stability','similarity_boost','speed',...(selected?.can_use_style?['style']:[])] as const).map(name=><label key={name}>{name.replace('_',' ')}<input type="number" step="0.05" min={name==='speed'?0.7:0} max={name==='speed'?1.2:1} value={Number(config.settings[name]??(name==='speed'?1:0.5))} onChange={e=>setting(name,Number(e.target.value))}/></label>)}
    {selected?.can_use_speaker_boost&&<label className="ais-toggle"><input type="checkbox" checked={Boolean(config.settings.use_speaker_boost)} onChange={e=>setting('use_speaker_boost',e.target.checked)}/> Speaker boost</label>}
   </div>
   <p><KeyRound size={14}/> API key: {config.has_api_key?'Đã cấu hình':'Chưa cấu hình'}. Key chỉ được gửi tới Backend để lưu mã hóa; Agent lấy qua kết nối đã xác thực.</p>
   <form className="ais-key-form" onSubmit={e=>{e.preventDefault();void run(async()=>{await apiRequest('/ai/voice/key',json('PUT',{api_key:key.trim()}));setKey('');await load();setMessage('Đã lưu ElevenLabs API key')})}}>
    <label>ElevenLabs API key<input type="password" autoComplete="off" value={key} onChange={e=>setKey(e.target.value)} placeholder="Nhập API key"/></label>
    <button disabled={busy||key.trim().length<8}><Save size={16}/> Lưu key</button>
   </form>
   <div className="ais-voice-actions"><button type="button" disabled={busy||!config.has_api_key} onClick={()=>void run(async()=>{setCatalog(await apiRequest<Catalog>('/ai/voice/catalog'));setMessage('Đã tải lại danh sách model và voice')})}>Tải danh sách</button><button type="button" disabled={busy||!config.has_api_key} onClick={()=>void run(async()=>{await apiRequest('/ai/voice/key',{method:'DELETE'});setCatalog({models:[],voices:[]});await load();setMessage('Đã xóa key')})}><Trash2 size={15}/> Xóa key</button></div>
   {catalog.error&&<span className="ais-voice-hint">{catalog.error}</span>}
  </>}
  <label>Câu nghe thử<input maxLength={1000} value={sample} onChange={e=>setSample(e.target.value)}/></label>
  <div className="ais-voice-actions"><button type="button" disabled={busy||!config.model||!config.voice} onClick={()=>void run(save)}><Save size={16}/> Lưu giọng nói</button><button type="button" disabled={busy||!config.model||!config.voice||!sample.trim()} onClick={()=>void run(test)}><Play size={16}/> Test Voice</button></div>
  {(error||message)&&<div className={'ais-alert '+(error?'is-error':'')}>{error||message}</div>}
 </section>
}
