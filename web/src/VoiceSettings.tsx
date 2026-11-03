import {useEffect,useRef,useState} from 'react'
import {Play,Save,Volume2} from 'lucide-react'
import {apiRequest,errorText} from './mediaApi'
import {voiceApi,requireVoiceIdle,noFallback,type VoiceConfig,type VoiceProvider as Provider,type VoiceProfile,type TtsReadiness} from './voiceApi'
import VoiceProfiles from './VoiceProfiles'
import VoiceUtterances from './VoiceUtterances'
import {TtsReadinessView} from './TtsReadiness'
import {useAgent} from './agentContext'
import './voice-settings.css'

const initial:VoiceConfig={provider:'edge',model:'edge-neural',voice:'vi-VN-HoaiMyNeural',rate:5,settings:{}}
const edgeVoices=[{id:'vi-VN-HoaiMyNeural',name:'Hoài My (nữ)'},{id:'vi-VN-NamMinhNeural',name:'Nam Minh (nam)'}]
const json=(method:string,body:unknown):RequestInit=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})

export default function VoiceSettings(){
 const [config,setConfig]=useState<VoiceConfig>(initial)
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
  if(c.migration_notice)setMessage(c.migration_notice)
 }
 useEffect(()=>{void load().catch(e=>{setError(errorText(e,'Không tải được cấu hình giọng nói'));setLoading(false)})},[])
 const run=async(action:()=>Promise<void>)=>{if(lock.current)return;lock.current=true;setBusy(true);setError('');setMessage('');try{await action()}catch(e){setError(errorText(e,'Thao tác giọng nói thất bại'))}finally{lock.current=false;setBusy(false)}}
 const update=(patch:Partial<VoiceConfig>)=>setConfig(c=>({...c,...patch}))
 const save=async(next=config)=>{
  const current=await voiceApi.config()
  const fields=(v:VoiceConfig)=>JSON.stringify([v.provider,v.model,v.voice,v.rate,v.settings,v.fallback||noFallback])
  if(fields(current)===fields(next)){setConfig(current);return}
  await requireVoiceIdle()
  const c=await voiceApi.save({...next,...(next.provider==='omnivoice'?{model:'demo_default',rate:0,settings:{}}:{})})
  setConfig(c);setMessage('Đã lưu cấu hình giọng nói cho Agent')
 }
 const test=async(next=config)=>{
  if(next.provider!=='omnivoice')await save(next)
  audio.current?.pause()
  const url=URL.createObjectURL(await voiceApi.preview(sample,next.provider==='omnivoice'?next.voice:undefined))
  setAudioUrl(url);setMessage('Giọng thử đã sẵn sàng. Bấm phát ở trình nghe bên dưới.')
 }
 const previewProfile=(profile:VoiceProfile)=>void run(()=>test({...config,provider:'omnivoice',voice:profile.id,model:'demo_default',rate:0,settings:{}}))
 const fallback=config.fallback||noFallback
 const setFallback=(patch:Partial<typeof fallback>)=>update({fallback:{...fallback,...patch}})
 if(!loading&&!loaded)return <section className="ais-panel"><h3>Giọng nói</h3><div role="alert">{error}</div><button onClick={()=>{setLoading(true);void load().catch(e=>{setError(errorText(e,'Không tải được cấu hình'));setLoading(false)})}}>Thử tải lại</button></section>
 if(loading)return <section className="ais-panel"><h3><Volume2 size={19}/>Giọng nói</h3><p role="status">Đang tải cấu hình giọng nói…</p></section>
 return <section className="ais-panel ais-voice-panel">
  <h3><Volume2 size={19}/> Voice Settings</h3>
  <p>Cấu hình này áp dụng cho câu trả lời LIVE, âm thanh chuẩn bị trước và phần nghe thử. Đổi provider sẽ áp dụng cho câu mới gửi tới Agent.</p>
  <fieldset disabled={busy} className="voice-fieldset"><legend>Provider và giọng đọc</legend><div className="ais-fields">
   <label>Provider<select aria-label="Provider" disabled={busy} value={config.provider} onChange={e=>{const provider=e.target.value as Provider;setReadiness(null);update(provider==='omnivoice'?{provider,model:'demo_default',voice:profiles[0]?.id||'',rate:0,settings:{}}:{provider,model:'edge-neural',voice:edgeVoices[0].id,settings:{},rate:5})}}><option value="edge">Microsoft Edge TTS</option><option value="omnivoice">G-OmniVoice (Local AI)</option></select></label>
   {config.provider==='edge'&&<label>Voice<select value={config.voice} onChange={e=>update({voice:e.target.value})}>{edgeVoices.map(v=><option key={v.id} value={v.id}>{v.name}</option>)}</select></label>}
   {config.provider==='omnivoice'&&<label>Giọng mặc định<select aria-label="Giọng mặc định" value={profiles.some(p=>p.id===config.voice)?config.voice:''} onChange={e=>update({voice:e.target.value})}><option value="">{profiles.length?'Chọn giọng mẫu…':'Chưa có giọng — tạo bên dưới'}</option>{profiles.map(p=><option key={p.id} value={p.id}>{p.name} · {p.sync.up_to_date?'đã đồng bộ':p.sync.state==='FAILED'?'đồng bộ lỗi':'chờ đồng bộ'}</option>)}</select></label>}
   {config.provider==='edge'&&<label>Tốc độ (%)<input type="number" min={-30} max={50} value={config.rate} onChange={e=>update({rate:Number(e.target.value)})}/></label>}
  </div></fieldset>
  {config.provider==='omnivoice'&&<>
   <p>Chất lượng demo_default đã kiểm chứng. Không có tùy chỉnh inference. {config.omnivoice_configured?'Backend đã cấu hình địa chỉ TTS.':'Backend chưa cấu hình địa chỉ TTS; vẫn có thể lưu giọng mẫu và chờ đồng bộ.'}</p>
   <VoiceProfiles profiles={profiles} selected={config.voice} busy={busy} onReload={async()=>{setReadiness(null);await reloadProfiles()}} onPreview={previewProfile}/>
   <div className="voice-section-head"><h4>Dịch vụ và GPU</h4><button type="button" disabled={busy||!agentId||agent?.platform==='ANDROID'} onClick={()=>void run(async()=>{if(!agentId)return;await save();const r=await voiceApi.readiness(agentId);await reloadProfiles();setReadiness({key:readinessKey,value:r})})}>Kiểm tra & đồng bộ TTS</button></div>
   {!agentId&&<p>Chọn Windows Agent trên thanh trên cùng để kiểm tra chủ động.</p>}
   <TtsReadinessView report={ready} active/>
   <details className="voice-fallback"><summary>Fallback · provider dự phòng</summary><p>Mặc định không dùng fallback. Chuyển provider có thể làm thay đổi giọng giữa phiên LIVE.</p>
    <div className="ais-fields">{(['live_reply','prepare','manual'] as const).map(context=><label key={context}>{({live_reply:'Trả lời LIVE',prepare:'Chuẩn bị âm thanh',manual:'Speak thủ công'})[context]}<select aria-label={'Fallback '+context} value={fallback[context]} onChange={e=>{const mode=e.target.value;setFallback({[context]:mode,...(mode==='provider'&&!fallback.provider?{provider:{provider:'edge',model:'edge-neural',voice:edgeVoices[0].id,rate:5,settings:{}}}:{})})}}><option value="none">Không dùng fallback</option><option value="provider">Dùng provider dự phòng</option>{context==='live_reply'&&<><option value="comment_only">Chỉ trả lời bằng comment</option><option value="hold_for_operator">Giữ để operator duyệt</option></>}</select></label>)}</div>
    {Object.values({live_reply:fallback.live_reply,prepare:fallback.prepare,manual:fallback.manual}).includes('provider')&&<div className="ais-fields">
     <p>Provider dự phòng: Edge TTS</p>
     <label>Giọng dự phòng<select value={fallback.provider?.voice||edgeVoices[0].id} onChange={e=>setFallback({provider:{...fallback.provider!,voice:e.target.value}})}>{edgeVoices.map(v=><option key={v.id} value={v.id}>{v.name}</option>)}</select></label>
     {fallback.provider&&<label>Tốc độ dự phòng (%)<input type="number" min={-30} max={50} step={1} value={fallback.provider.rate} onChange={e=>setFallback({provider:{...fallback.provider!,rate:Number(e.target.value)}})}/></label>}

    </div>}
   </details>
  </>}
  <label>Câu nghe thử<input maxLength={config.provider==='omnivoice'?450:1000} value={sample} onChange={e=>setSample(e.target.value)}/></label>
  <div className="ais-voice-actions"><button type="button" disabled={busy||!config.model||!config.voice} onClick={()=>void run(()=>save())}><Save size={16}/> Lưu giọng nói</button><button type="button" disabled={busy||!config.model||!config.voice||!sample.trim()||(config.provider==='omnivoice'&&!profiles.find(p=>p.id===config.voice)?.sync.up_to_date)} onClick={()=>void run(()=>test())}><Play size={16}/>{busy?'Đang xử lý…':config.provider==='omnivoice'?'Nghe thử':'Lưu & nghe thử'}</button></div>
  <p>Thay đổi giọng được lưu cho toàn owner. Khi có phiên RUNNING, hãy kết thúc phiên trước khi lưu thay đổi. Nghe thử G-OmniVoice không đổi giọng mặc định.</p>
  {audioUrl&&<audio aria-label="Giọng nghe thử" ref={audio} src={audioUrl} controls/>}
  <button type="button" disabled={busy||!agent?.online||agent.platform==='ANDROID'||!sample.trim()||sample.length>450} onClick={()=>void run(async()=>{
   const stored=await voiceApi.config()
   if(stored.provider!==config.provider||stored.voice!==config.voice)throw new Error('Lưu cấu hình giọng trước khi phát qua OBS. Nghe thử trình duyệt không lưu mặc định.')
   if(!window.confirm('Phát câu này qua nguồn Sarae Voice trong OBS của Agent đang chọn?'))return
   await apiRequest('/ai/voice/speak',json('POST',{agent_id:agentId,text:sample}));setMessage('Đã gửi yêu cầu tới Agent. Xem trạng thái OBS bên dưới.')
  })}>Phát qua OBS (giọng đã lưu)</button>
  {agentId&&<VoiceUtterances agentId={agentId}/>}
  {(error||message)&&<div role={error?'alert':'status'} className={'ais-alert '+(error?'is-error':'')}>{error||message}</div>}
 </section>
}
