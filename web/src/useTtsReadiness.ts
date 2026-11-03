import {useEffect,useRef,useState} from 'react'
import {errorText} from './mediaApi'
import {voiceApi,type TtsReadiness} from './voiceApi'

type Result={key:string;report:TtsReadiness|null;active:boolean;error:string}
export function useTtsReadiness(sessionId:number|null,agentId:string){
 const key=`${sessionId}:${agentId}`
 const [result,setResult]=useState<Result|null>(null)
 const generation=useRef(0)
 const pending=useRef<{key:string;work:Promise<void>}|null>(null)
 useEffect(()=>{
  const rev=++generation.current
  if(sessionId&&agentId)void voiceApi.passive(sessionId,agentId).then(report=>{
   if(generation.current===rev)setResult({key,report,active:false,error:''})
  }).catch(e=>{if(generation.current===rev)setResult({key,report:null,active:false,error:errorText(e,'Không tải được trạng thái TTS')})})
  return()=>{generation.current=rev+1}
 },[sessionId,agentId,key])
 const check=():Promise<void>=>{
  if(!agentId)return Promise.resolve()
  if(pending.current?.key===key)return pending.current.work
  // Invalidate a slower passive response so it cannot replace an active warmup result.
  const rev=++generation.current
  const work=(async()=>{try{const report=await voiceApi.readiness(agentId);if(generation.current===rev)setResult({key,report,active:true,error:''})}
   catch(e){if(generation.current===rev)setResult({key,report:null,active:true,error:errorText(e,'Không kiểm tra được TTS')})}
   finally{if(pending.current?.key===key)pending.current=null}})()
  pending.current={key,work}
  return work
 }
 const view=result?.key===key?result:null
 return {report:view?.report||null,active:view?.active||false,error:view?.error||'',check}
}
