import {api, apiRequest} from './mediaApi'
import {authHeaders, clearAuth} from './authToken'

export type VoiceProvider = 'edge'|'omnivoice'
export type ProviderSpec = {provider:VoiceProvider; model:string; voice:string; rate:number; settings:Record<string,number|boolean>}
export type FallbackMode = 'none'|'provider'|'comment_only'|'hold_for_operator'
export type VoiceFallback = {live_reply:FallbackMode; prepare:'none'|'provider'; manual:'none'|'provider'; provider:ProviderSpec|null}
export type VoiceConfig = ProviderSpec & {fallback?:VoiceFallback; migration_notice?:string|null; omnivoice_configured?:boolean}
export const noFallback:VoiceFallback = {live_reply:'none',prepare:'none',manual:'none',provider:null}
export type VoiceProfile = {id:string;name:string;revision:number;updated_at:string;
 wav:{duration_s:number;sample_rate:number;channels:number;bytes:number};
 transcript:{source:'asr'|'manual';manual:string|null;asr:string|null;effective:string|null};
 sync:{state:'PENDING'|'SYNCED'|'FAILED';up_to_date:boolean;error:string|null};
 qc:{local?:{warnings?:string[]};server?:{warnings?:string[]}|null}}
export type TtsReadiness = {provider:VoiceProvider;applicable:boolean;ready:boolean;warnings:string[];
 service?:{configured:boolean;online:boolean;state:string|null;error?:{code:string}|null};
 agent?:{agent_id:string|null;supports_omnivoice:boolean};voice?:{name:string;state:string;up_to_date:boolean;revision:number}|null;
 warmup?:{cache:string;ms:number};gpu?:{busy:boolean;note?:string;running?:{class?:string;estimated_remaining_s?:number}|null}}
export type Utterance = {id:string;state:string;tts_job_state:string|null;created_at:string;error_code:string|null;part:string;voice_rev:number}
export const voiceJson = (method:string,body:unknown):RequestInit => ({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
const messages:Record<string,string> = {
 TTS_BACKEND_UNREACHABLE:'Không kết nối được máy TTS. Giọng tự động hiện chưa sẵn sàng.',TTS_NOT_CONFIGURED:'Chưa cấu hình máy TTS trên Sarae Backend.',
 TTS_NOT_READY:'Model TTS chưa sẵn sàng; có thể đang tải hoặc gặp lỗi.',TTS_BUSY:'GPU đang bận hoặc hàng đợi đã đầy. Hãy thử lại sau.',
 TTS_GPU_FAILED:'GPU gặp lỗi CUDA. Cần khởi động lại dịch vụ TTS.',AGENT_NO_OMNIVOICE:'Windows Agent chưa hỗ trợ G-OmniVoice. Hãy cập nhật Agent.',
 VOICE_NOT_SYNCED:'Giọng chưa đồng bộ đúng revision.',VOICE_NOT_FOUND:'Chưa chọn giọng hoặc giọng đã bị xóa.',
 LONG_JOB_RUNNING:'GPU đang chạy một job dài. Câu trả lời LIVE phải chờ; thời gian còn lại chỉ là ước tính.',
 VOICE_ASR_FAILED:'Whisper chưa nhận dạng được giọng mẫu. Bạn có thể nhập transcript thủ công rồi đồng bộ lại.',
 TTS_EXPIRED:'Câu nói đã hết hạn và sẽ không phát.',TTS_CANCELLED:'Câu nói đã bị hủy.',TEXT_TOO_LONG:'Nội dung vượt giới hạn 450 ký tự.',
}
export const voiceCodeText = (code:string) => messages[code] || code
export async function voiceResponse(path:string,init?:RequestInit):Promise<Response>{
 const headers = new Headers(init?.headers)
 Object.entries(authHeaders()).forEach(([key,value])=>headers.set(key,value))
 const response = await fetch(api+'/api'+path,{...init,headers})
 if(response.status===401)clearAuth()
 if(!response.ok){
  const body = await response.json().catch(()=>({}))
  const code = response.headers.get('X-Sarae-Error') || ''
  const detail = typeof body.detail==='string'?body.detail:Array.isArray(body.detail)?body.detail.map((v:{msg:string})=>v.msg).join('; '):''
  throw new Error((messages[code] || detail || ({409:'Dữ liệu đang được sử dụng; hãy kiểm tra lựa chọn hiện tại.',422:'Dữ liệu chưa hợp lệ.',429:'Quá nhiều yêu cầu. Hãy thử lại sau.',503:'Dịch vụ TTS chưa sẵn sàng.'} as Record<number,string>)[response.status] || 'Thao tác thất bại')+(code?` (${code})`:''))
 }
 return response
}
async function request<T>(path:string,init?:RequestInit):Promise<T>{const r=await voiceResponse(path,init);return r.status===204?undefined as T:r.json()}
const profiles='/ai/voice/profiles'
export const voiceApi = {
 config:()=>request<VoiceConfig>('/ai/voice/config'),
 save:({provider,model,voice,rate,settings,fallback}:VoiceConfig)=>request<VoiceConfig>('/ai/voice/config',voiceJson('PUT',{provider,model,voice,rate,settings,...(fallback?{fallback}:{})})),
 profiles:()=>request<{profiles:VoiceProfile[];tts_configured:boolean}>(profiles),
 create:(form:FormData)=>request<VoiceProfile>(profiles,{method:'POST',body:form}),
 patch:(id:string,body:{name?:string;transcript?:string|null})=>request<VoiceProfile>(`${profiles}/${id}`,voiceJson('PATCH',body)),
 replace:(id:string,form:FormData)=>request<VoiceProfile>(`${profiles}/${id}/wav`,{method:'PUT',body:form}),
 sync:(id:string)=>request<VoiceProfile>(`${profiles}/${id}/sync`,{method:'POST'}),
 remove:(id:string)=>request<void>(`${profiles}/${id}`,{method:'DELETE'}),
 wav:(id:string)=>voiceResponse(`${profiles}/${id}/wav`).then(r=>r.blob()),
 preview:(text:string,voice_id?:string)=>voiceResponse('/ai/voice/preview',voiceJson('POST',{text,...(voice_id?{voice_id}:{})})).then(r=>r.blob()),
 readiness:(agent_id:string)=>request<TtsReadiness>('/ai/voice/readiness',voiceJson('POST',{agent_id})),
 passive:(session:number,agent:string)=>request<{tts:TtsReadiness}>(`/live/sessions/${session}/readiness?agent_id=${encodeURIComponent(agent)}`).then(r=>r.tts),
 utterances:(agent:string)=>request<{utterances:Utterance[]}>(`/ai/voice/utterances?agent_id=${encodeURIComponent(agent)}&limit=12`),
}
/** Protect the owner-wide configuration from changing the voice used by a running session. */
export async function requireVoiceIdle(){
 const sessions=await apiRequest<{status:string}[]>('/live/sessions')
 if(sessions.some(s=>s.status==='RUNNING'))throw new Error('Có phiên Sarae đang RUNNING. Hãy kết thúc phiên trước khi đổi giọng, WAV, transcript hoặc cấu hình fallback.')
}
export function utteranceLabel(u:Utterance){
 if(u.state==='SENT'&&['failed','cancelled','expired','lost','gone'].includes(u.tts_job_state||''))return ({failed:'Sinh âm thanh thất bại',cancelled:'Job âm thanh bị hủy',expired:'Job âm thanh hết hạn',lost:'Job âm thanh bị mất',gone:'Job âm thanh đã dọn'} as Record<string,string>)[u.tts_job_state!]
 if(u.state==='SENT')return u.tts_job_state==='done'?'Đã gửi · chờ OBS':'Đã gửi · đang chờ âm thanh'
 return ({NEW:'Đã tạo yêu cầu',JOB_CREATED:'Đang chờ âm thanh',PLAYING:'Đang phát',PLAYED:'Đã phát xong',CANCELLED:'Bị hủy',STOPPED:'Đã dừng',EXPIRED:'Hết hạn',FAILED:'Thất bại',ABORTED:'Đã hủy yêu cầu',ORPHANED:'Yêu cầu gián đoạn'} as Record<string,string>)[u.state] || u.state
}
export function qcWarnings(p:VoiceProfile){
 const labels:Record<string,string>={duration_not_recommended:'Nên dùng giọng mẫu dài 3–10 giây.',duration_out_of_range:'Thời lượng giọng mẫu ngoài giới hạn.',end_may_be_cut:'Đuôi WAV có thể bị cắt khi còn tiếng nói.',start_may_be_cut:'Đầu WAV có thể bị cắt.',clipping:'WAV có dấu hiệu clipping.',quiet:'Âm lượng giọng mẫu quá nhỏ.',long_pause:'WAV có khoảng nghỉ dài.'}
 return [...new Set([...(p.qc.local?.warnings||[]),...(p.qc.server?.warnings||[])])].map(w=>labels[w]||w)
}
