import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button } from './ui/core'
import { CheckCircle2, Download, RotateCcw } from 'lucide-react'
import { apiRequest, errorText, sizeLabel } from './mediaApi'

export type MediaReadiness = {
  session_id:number; agent_id:string; state:'NO_VIDEO'|'NOT_STARTED'|'QUEUED'|'DOWNLOADING'|'FAILED'|'INCOMPLETE'|'READY'
  ready:boolean; total:number; ready_count:number; bytes_total:number; bytes_done:number; missing:string[]
  failed:{media_id:string; filename:string|null; error:string|null}[]; products:{product_id:number; total:number; ready:number}[]
  products_without_video:number[]; reported_at:string|null; agent_online?:boolean; hostname?:string
}

const POLL_MS = 2000
const json = (body:unknown):RequestInit => ({method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})

const mediaApi = {
  prepare:(sessionId:number, agentId:string) => apiRequest<MediaReadiness>(`/live/sessions/${sessionId}/prepare-media`, json({agent_id:agentId})),
  readiness:(sessionId:number, agentId:string) =>
    apiRequest<MediaReadiness>(`/live/sessions/${sessionId}/media-readiness?agent_id=${encodeURIComponent(agentId)}`),
}

/**
 * Downloads every video of the session onto the chosen Agent before LIVE (random playback may pick any of them)
 * and follows the progress the Agent reports. `onReady` tells the start dialog whether playback may begin.
 */
export default function MediaPrepare({sessionId, agentId, hostname, onReady}:{sessionId:number; agentId:string; hostname:string; onReady:(ready:boolean)=>void}){
  const [status,setStatus] = useState<MediaReadiness|null>(null)
  const [error,setError] = useState('')
  const [busy,setBusy] = useState(false)
  const started = useRef(false)

  const start = useCallback(async () => {
    setBusy(true);setError('')
    try { setStatus(await mediaApi.prepare(sessionId, agentId)) }
    catch (e) { setError(errorText(e, 'Không gửi được yêu cầu tải video')) }
    finally { setBusy(false) }
  }, [sessionId, agentId])

  // Ask once when the step opens, unless the Agent already holds every video.
  useEffect(()=>{
    if (started.current) return
    started.current = true
    void mediaApi.readiness(sessionId, agentId).then(current=>{
      setStatus(current)
      if (!current.ready && current.state !== 'DOWNLOADING' && current.state !== 'NO_VIDEO') void start()
    }).catch(e=>setError(errorText(e, 'Không đọc được trạng thái video')))
  }, [sessionId, agentId, start])

  // Follow the download until it is ready or failed.
  const state = status?.state
  useEffect(()=>{
    if (!state || state === 'READY' || state === 'FAILED' || state === 'NO_VIDEO') return
    const timer = window.setInterval(()=>{
      mediaApi.readiness(sessionId, agentId).then(next=>setStatus(prev=>prev?.state === 'QUEUED' && next.state === 'NOT_STARTED' ? prev : next)).catch(()=>undefined)
    }, POLL_MS)
    return ()=>window.clearInterval(timer)
  }, [state, sessionId, agentId])

  useEffect(()=>onReady(!!status?.ready), [status?.ready, onReady])

  if (error) return <Alert tone="danger" title="Chưa tải được video về máy">{error}<div className="ls-media-actions"><Button size="sm" variant="outline" leftIcon={<RotateCcw size={14}/>} loading={busy} onClick={()=>void start()}>Thử lại</Button></div></Alert>
  if (!status) return <p className="ls-muted">Đang kiểm tra video trên máy {hostname}…</p>
  if (status.state === 'NO_VIDEO') return <Alert tone="warning">Các sản phẩm đã chọn chưa có video trong Thư viện video.</Alert>

  const percent = status.bytes_total ? Math.min(100, Math.round(status.bytes_done / status.bytes_total * 100)) : 0
  if (status.ready) return <div className="ls-media is-ready"><CheckCircle2 size={18}/>
    <span>Đã tải đủ <b>{status.total}</b> video ({sizeLabel(status.bytes_total)}) về máy {hostname}. Sẵn sàng phát, kể cả khi mất mạng.</span></div>

  const failed = status.state === 'FAILED'
  return <div className={'ls-media' + (failed ? ' is-failed' : '')}>
    <div className="ls-media-head"><Download size={16}/>
      <strong>{failed ? 'Tải video bị lỗi' : status.state === 'QUEUED' ? 'Đang chờ Agent nhận lệnh…' : `Đang tải video về máy ${hostname}`}</strong>
      <span>{status.ready_count}/{status.total} video · {sizeLabel(status.bytes_done)} / {sizeLabel(status.bytes_total)}</span>
    </div>
    <div className="ls-media-bar" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}><span style={{width:`${percent}%`}}/></div>
    {status.agent_online === false && <p className="ls-muted">Máy {hostname} đang offline; việc tải sẽ tiếp tục khi máy kết nối lại.</p>}
    {failed && <ul className="ls-media-failed">{status.failed.map(f=><li key={f.media_id}><b>{f.filename ?? f.media_id}</b>: {f.error ?? 'lỗi tải'}</li>)}</ul>}
    {(failed || status.state === 'INCOMPLETE') && <div className="ls-media-actions">
      <Button size="sm" variant="outline" leftIcon={<RotateCcw size={14}/>} loading={busy} onClick={()=>void start()}>Tải lại phần còn thiếu</Button>
    </div>}
    <p className="ls-muted">Phải tải đủ mọi video của phiên trước khi phát, vì chế độ ngẫu nhiên có thể chọn bất kỳ video nào. Video đã có trên máy sẽ không tải lại.</p>
  </div>
}
