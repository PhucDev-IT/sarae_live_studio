import {useEffect,useRef,useState} from 'react'
import {KeyRound,Play,Save,Trash2,Volume2} from 'lucide-react'
import {apiRequest,errorText} from './mediaApi'
import {voiceApi,requireVoiceIdle,noFallback,type VoiceConfig,type VoiceProvider as Provider,type VoiceProfile,type TtsReadiness} from './voiceApi'
import VoiceProfiles from './VoiceProfiles'
import {TtsReadinessView} from './TtsReadiness'
import {useAgent} from './agentContext'
import './voice-settings.css'

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
 const [audioUrl,setAudioUrl]=useState('')
 const [profiles,setProfiles]=useState<VoiceProfile[]>([])
 const [readiness,setReadiness]=useState<{key:string;value:TtsReadiness}|null>(null)
 const [loading,setLoading]=useState(true)
 const [loaded,setLoaded]=useState(false)
 const lock=useRef(false)
 const {agentId,agent}=useAgent()
 const readinessKey=`${config.provider}:${config.voice}:${agentId}`
 const ready=readiness?.key===readinessKey?readiness.value:null
 const reloadProfiles=async()=>setProfiles((await voiceApi.profiles()).profiles)
 useEffect(()=>{const player=audio.current;return()=>{player?.pause();if(audioUrl)URL.revokeObjectURL(audioUrl)}},[audioUrl])
 const load=async()=>{
  const c=await voiceApi.config()
  setConfig(c)
  await reloadProfiles()
  setLoading(false);setLoaded(true)
  if(c.has_api_key)setCatalog(await apiRequest<Catalog>('/ai/voice/catalog'))
 }
 useEffect(()=>{void load().catch(e=>{setError(errorText(e,'Không tải được cấu hình giọng nói'));setLoading(false)})},[])
 const run=async(action:()=>Promise<void>)=>{if(lock.current)return;lock.current=true;setBusy(true);setError('');setMessage('');try{await action()}catch(e){setError(errorText(e,'Thao tác giọng nói thất bại'))}finally{lock.current=false;setBusy(false)}}
 const update=(patch:Partial<VoiceConfig>)=>setConfig(c=>({...c,...patch}))
 const setting=(name:string,value:number|boolean)=>setConfig(c=>({...c,settings:{...c.settings,[name]:value}}))
 const save=async(next=config)=>{
  const current=await voiceApi.config()
  const fields=(v:VoiceConfig)=>JSON.stringify([v.provider,v.model,v.voice,v.rate,v.settings,v.fallback||noFallback])
  if(fields(current)===fields(next)){setConfig(current);return}
  await requireVoiceIdle()
  const c=await voiceApi.save({...next,...(next.provider==='omnivoice'?{model:'demo_default',rate:0,settings:{}}:{})})
  setConfig(c);setMessage('Đã lưu cấu hình giọng nói cho Agent')
 }
 const test=async(next=config)=>{
  await save(next)
  audio.current?.pause()
  const url=URL.createObjectURL(await voiceApi.preview(sample))
  setAudioUrl(url);setMessage('Giọng thử đã sẵn sàng. Bấm phát ở trình nghe bên dưới.')
 }
 const previewProfile=(profile:VoiceProfile)=>{if(!window.confirm('Nghe thử giọng này sẽ lưu làm giọng mặc định. Tiếp tục?'))return;void run(()=>test({...config,provider:'omnivoice',voice:profile.id,model:'demo_default',rate:0,settings:{}}))}
 const fallback=config.fallback||noFallback
 const setFallback=(patch:Partial<typeof fallback>)=>update({fallback:{...fallback,...patch}})
 const models=config.provider==='edge'?[{id:'edge-neural',name:'Edge Neural'}]:catalog.models
 const voices=config.provider==='edge'?edgeVoices:catalog.voices
 const selected=models.find(m=>m.id===config.model)
 if(!loading&&!loaded)return <section className="ais-panel"><h3>Giọng nói</h3><div role="alert">{error}</div><button onClick={()=>{setLoading(true);void load().catch(e=>{setError(errorText(e,'Không tải được cấu hình'));setLoading(false)})}}>Thử tải lại</button></section>
 if(loading)return <section className="ais-panel"><h3><Volume2 size={19}/>Giọng nói</h3><p role="status">Đang tải cấu hình giọng nói…</p></section>
 return <section className="ais-panel ais-voice-panel">
  <h3><Volume2 size={19}/> Voice Settings</h3>
  <p>Cấu hình này áp dụng cho câu trả lời LIVE, âm thanh chuẩn bị trước và phần nghe thử. Đổi provider sẽ áp dụng cho câu mới gửi tới Agent.</p>
  <fieldset disabled={busy} className="voice-fieldset"><legend>Provider và giọng đọc</legend><div className="ais-fields">
   <label>Provider<select aria-label="Provider" disabled={busy} value={config.provider} onChange={e=>{const provider=e.target.value as Provider;setReadiness(null);update(provider==='omnivoice'?{provider,model:'demo_default',voice:profiles[0]?.id||'',rate:0,settings:{}}:provider==='edge'?{provider,model:'edge-neural',voice:edgeVoices[0].id,settings:{},rate:5}:{provider,model:'eleven_flash_v2_5',voice:catalog.voices[0]?.id||'',settings:{stability:0.5,similarity_boost:0.75,speed:1},rate:0})}}><option value="edge">Microsoft Edge TTS</option><option value="elevenlabs">ElevenLabs</option><option value="omnivoice">G-OmniVoice (Local AI)</option></select></label>
   {config.provider!=='omnivoice'&&<><label>Model<select disabled={busy} value={models.some(m=>m.id===config.model)?config.model:'custom'} onChange={e=>{const next=models.find(m=>m.id===e.target.value);const settings={...config.settings};if(next&&!next.can_use_style)delete settings.style;if(next&&!next.can_use_speaker_boost)delete settings.use_speaker_boost;update({model:e.target.value==='custom'?'':e.target.value,settings})}}>{models.map(m=><option key={m.id} value={m.id}>{m.name}</option>)}<option value="custom">Nhập model ID khác…</option></select></label>
   {!models.some(m=>m.id===config.model)&&<label>Model ID<input value={config.model} onChange={e=>update({model:e.target.value})} placeholder="eleven_flash_v2_5"/></label>}
   <label>Voice<select disabled={busy} value={voices.some(v=>v.id===config.voice)?config.voice:'custom'} onChange={e=>update({voice:e.target.value==='custom'?'':e.target.value})}>{voices.map(v=><option key={v.id} value={v.id}>{v.name}</option>)}<option value="custom">Nhập voice ID khác…</option></select></label>
   {!voices.some(v=>v.id===config.voice)&&<label>Voice ID<input value={config.voice} onChange={e=>update({voice:e.target.value})} placeholder="Voice ID từ ElevenLabs"/></label>}
   </>}
   {config.provider==='omnivoice'&&<label>Giọng mặc định<select aria-label="Giọng mặc định" value={profiles.some(p=>p.id===config.voice)?config.voice:''} onChange={e=>update({voice:e.target.value})}><option value="">{profiles.length?'Chọn giọng mẫu…':'Chưa có giọng — tạo bên dưới'}</option>{profiles.map(p=><option key={p.id} value={p.id}>{p.name} · {p.sync.up_to_date?'đã đồng bộ':p.sync.state==='FAILED'?'đồng bộ lỗi':'chờ đồng bộ'}</option>)}</select></label>}
   {config.provider==='edge'&&<label>Tốc độ (%)<input type="number" min={-30} max={50} value={config.rate} onChange={e=>update({rate:Number(e.target.value)})}/></label>}
  </div></fieldset>
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
  {config.provider==='omnivoice'&&<>
   <p>Chất lượng demo_default đã kiểm chứng. Không có tùy chỉnh inference. {config.omnivoice_configured?'Backend đã cấu hình địa chỉ TTS.':'Backend chưa cấu hình địa chỉ TTS; vẫn có thể lưu giọng mẫu và chờ đồng bộ.'}</p>
   <VoiceProfiles profiles={profiles} selected={config.voice} busy={busy} onReload={async()=>{setReadiness(null);await reloadProfiles()}} onPreview={previewProfile}/>
   <div className="voice-section-head"><h4>Dịch vụ và GPU</h4><button type="button" disabled={busy||!agentId||agent?.platform==='ANDROID'} onClick={()=>void run(async()=>{if(!agentId)return;await save();const r=await voiceApi.readiness(agentId);await reloadProfiles();setReadiness({key:readinessKey,value:r})})}>Kiểm tra & đồng bộ TTS</button></div>
   {!agentId&&<p>Chọn Windows Agent trên thanh trên cùng để kiểm tra chủ động.</p>}
   <TtsReadinessView report={ready} active/>
   <details className="voice-fallback"><summary>Fallback · provider dự phòng</summary><p>Mặc định không dùng fallback. Chuyển provider có thể làm thay đổi giọng giữa phiên LIVE.</p>
    <div className="ais-fields">{(['live_reply','prepare','manual'] as const).map(context=><label key={context}>{({live_reply:'Trả lời LIVE',prepare:'Chuẩn bị âm thanh',manual:'Speak thủ công'})[context]}<select aria-label={'Fallback '+context} value={fallback[context]} onChange={e=>{const mode=e.target.value;setFallback({[context]:mode,...(mode==='provider'&&!fallback.provider?{provider:{provider:'edge',model:'edge-neural',voice:edgeVoices[0].id,rate:5,settings:{}}}:{})})}}><option value="none">Không dùng fallback</option><option value="provider">Dùng provider dự phòng</option>{context==='live_reply'&&<><option value="comment_only">Chỉ trả lời bằng comment</option><option value="hold_for_operator">Giữ để operator duyệt</option></>}</select></label>)}</div>
    {Object.values({live_reply:fallback.live_reply,prepare:fallback.prepare,manual:fallback.manual}).includes('provider')&&<div className="ais-fields">
     <label>Provider dự phòng<select value={fallback.provider?.provider||'edge'} onChange={e=>setFallback({provider:e.target.value==='edge'?{provider:'edge',model:'edge-neural',voice:edgeVoices[0].id,rate:5,settings:{}}:{provider:'elevenlabs',model:'eleven_flash_v2_5',voice:catalog.voices[0]?.id||'',rate:0,settings:{speed:1}}})}><option value="edge">Edge TTS</option><option value="elevenlabs">ElevenLabs</option></select></label>
     {fallback.provider?.provider==='edge'?<label>Giọng dự phòng<select value={fallback.provider.voice} onChange={e=>setFallback({provider:{...fallback.provider!,voice:e.target.value}})}>{edgeVoices.map(v=><option key={v.id} value={v.id}>{v.name}</option>)}</select></label>:<><label>Voice ID ElevenLabs<input value={fallback.provider?.voice||''} onChange={e=>setFallback({provider:{...fallback.provider!,voice:e.target.value}})}/></label><label>Model ID ElevenLabs<input value={fallback.provider?.model||''} onChange={e=>setFallback({provider:{...fallback.provider!,model:e.target.value}})}/></label><p>Cần lưu ElevenLabs API key ở phần provider ElevenLabs trước khi dùng dự phòng.</p></>}
     {fallback.provider&&<label>{fallback.provider.provider==='edge'?'Tốc độ dự phòng (%)':'Tốc độ ElevenLabs dự phòng'}<input type="number" min={fallback.provider.provider==='edge'?-30:0.7} max={fallback.provider.provider==='edge'?50:1.2} step={fallback.provider.provider==='edge'?1:0.05} value={fallback.provider.provider==='edge'?fallback.provider.rate:Number(fallback.provider.settings.speed??1)} onChange={e=>{const p=fallback.provider!;setFallback({provider:p.provider==='edge'?{...p,rate:Number(e.target.value)}:{...p,settings:{...p.settings,speed:Number(e.target.value)}}})}}/></label>}
    </div>}
   </details>
  </>}
  <label>Câu nghe thử<input maxLength={config.provider==='omnivoice'?450:1000} value={sample} onChange={e=>setSample(e.target.value)}/></label>
  <div className="ais-voice-actions"><button type="button" disabled={busy||!config.model||!config.voice} onClick={()=>void run(()=>save())}><Save size={16}/> Lưu giọng nói</button><button type="button" disabled={busy||!config.model||!config.voice||!sample.trim()||(config.provider==='omnivoice'&&!profiles.find(p=>p.id===config.voice)?.sync.up_to_date)} onClick={()=>void run(()=>test())}><Play size={16}/>{busy?'Đang xử lý…':'Lưu & nghe thử'}</button></div>
  <p>Thay đổi giọng được lưu cho toàn owner. Khi có phiên RUNNING, hãy kết thúc phiên trước khi đổi hoặc nghe thử cấu hình khác.</p>
  {audioUrl&&<audio aria-label="Giọng nghe thử" ref={audio} src={audioUrl} controls/>}
  {(error||message)&&<div role={error?'alert':'status'} className={'ais-alert '+(error?'is-error':'')}>{error||message}</div>}
 </section>
}
