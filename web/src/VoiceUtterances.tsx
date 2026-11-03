import {useEffect,useState} from 'react'
import {Alert} from './ui/core'
import {errorText} from './mediaApi'
import {utteranceLabel,voiceApi,voiceCodeText,type Utterance} from './voiceApi'

export default function VoiceUtterances({agentId}:{agentId:string}){
 const [result,setResult]=useState<{agent:string;items:Utterance[];error:string}|null>(null)
 const items=result?.agent===agentId?result.items:[]
 const error=result?.agent===agentId?result.error:''
 useEffect(()=>{let live=true;let pending=false;const load=async()=>{if(pending)return;pending=true;try{const r=await voiceApi.utterances(agentId);if(live){setResult({agent:agentId,items:r.utterances,error:''})}}catch(e){if(live)setResult({agent:agentId,items:[],error:errorText(e,'Không tải được trạng thái giọng nói')})}finally{pending=false}};void load();const timer=window.setInterval(()=>void load(),4000);return()=>{live=false;window.clearInterval(timer)}},[agentId])
 return <details className="voice-utterances"><summary>Trạng thái phát G-OmniVoice</summary><p>“Đang phát” là trạng thái OBS xác nhận, không xác nhận người xem TikTok đã nghe được.</p>{error&&<Alert tone="danger">{error}</Alert>}{!items.length&&!error?<p>Chưa có yêu cầu giọng nói trên Agent này.</p>:<ul>{items.map(u=><li key={u.id}><span><strong>{utteranceLabel(u)}</strong><small>{u.part==='intro'?'Lời gọi tên':u.part==='prepare'?'Chuẩn bị trước':'Nội dung'} · r{u.voice_rev} · {new Date(u.created_at).toLocaleTimeString('vi-VN')}</small></span>{u.error_code&&<span className="voice-warning">{voiceCodeText(u.error_code)}</span>}</li>)}</ul>}</details>
}
