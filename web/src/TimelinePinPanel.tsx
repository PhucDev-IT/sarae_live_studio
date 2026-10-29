import { useCallback, useEffect, useState } from 'react'
import { Badge, Button, Card, SegmentedControl, useToast } from './ui/core'
import { CircleAlert, Hand, Pencil, Pin, PlayCircle, ShieldCheck, ShieldQuestion } from 'lucide-react'
import { errorText } from './mediaApi'
import { hms, timelineHref, timelinesApi, type LiveSession, type PinStateView, type TimelineMarker, type TimelinePolicy } from './liveSessionsApi'

const POLL_MS = 3000

/**
 * LIVE console panel of a Single Video session: the pin the timeline wants at the current position vs what is pinned
 * on TikTok (as the Agent last reported), the next marker, the LIVE gate and dry-run state, and the manual-pin policy.
 */
export default function TimelinePinPanel({session, policy, onPolicy, productName}:{
  session:LiveSession; policy:TimelinePolicy; onPolicy:(policy:TimelinePolicy)=>void; productName:(id:number)=>string
}){
  const toast = useToast()
  const [state,setState] = useState<PinStateView|null>(null)
  const [markers,setMarkers] = useState<TimelineMarker[]>([])
  const [busy,setBusy] = useState(false)
  const poll = useCallback(async ()=>{try{setState(await timelinesApi.pinState(session.id))}catch{/* keep the last one */}},[session.id])
  useEffect(()=>{
    void poll()
    const timer = window.setInterval(()=>void poll(), POLL_MS)
    return ()=>window.clearInterval(timer)
  },[poll])
  useEffect(()=>{
    let alive = true
    if (session.active_timeline_id == null) {setMarkers([]);return}
    void timelinesApi.get(session.id, session.active_timeline_id).then(t=>{if (alive) setMarkers((t.markers ?? []).filter(m=>m.review_status === 'APPROVED' && m.action !== 'NO_ACTION'))})
      .catch(()=>{if (alive) setMarkers([])})
    return ()=>{alive = false}
  },[session.id, session.active_timeline_id])

  const resume = async () => {
    setBusy(true)
    try {await timelinesApi.resume(session);toast.push({tone:'success', title:'Đã tiếp tục ghim theo timeline'});await poll()}
    catch(e){toast.push({tone:'danger', title:'Không tiếp tục được timeline', description:errorText(e, '')})}
    finally{setBusy(false)}
  }

  const position = state?.position_ms ?? null
  const next = position == null ? undefined : markers.find(m=>m.at_ms > position)
  const hold = state?.mode === 'MANUAL_HOLD'
  const name = (id:number|null|undefined, fallback:string) => id == null ? fallback : productName(id)
  const actual = state?.actual_known === false ? 'Chưa xác định'
    : state?.actual_tiktok_product_id ? `Card TikTok ${state.actual_tiktok_product_id} (ngoài lịch)`
    : name(state?.actual_product_id, 'Không ghim')
  const versionMismatch = state?.reported && state.active_version !== state.agent_version

  return <Card className="lc-timeline" padding={false} title={<span className="lc-card-title"><Pin size={17}/>Lịch ghim theo video</span>}
    description={state?.active_version != null ? `Phiên bản ${state.active_version} · ${markers.length} mốc ghim/bỏ ghim` : 'Chưa kích hoạt lịch ghim: video phát nhưng không tự ghim'}
    actions={<a className="lc-icon-link" href={timelineHref(session.id)} title="Sửa lịch ghim" aria-label="Sửa lịch ghim"><Pencil size={15}/></a>}>
    <div className="lc-tl-body">
      <div className="lc-tl-badges">
        {hold ? <Badge tone="warning"><Hand size={12}/>Đang giữ ghim thủ công</Badge> : <Badge tone="success">Theo timeline</Badge>}
        {state?.live === true ? <Badge tone="success"><ShieldCheck size={12}/>Console: đang LIVE</Badge>
          : <Badge tone="warning"><ShieldQuestion size={12}/>{state?.live === false ? 'Console: chưa LIVE' : 'Console: chưa xác định LIVE'}</Badge>}
        {state?.dry_run && <Badge tone="info">Dry-run: không bấm trên TikTok</Badge>}
        {!state?.agent_online && <Badge tone="danger">Agent offline</Badge>}
        {state?.reported && state.iteration ? <Badge>Vòng lặp {state.iteration + 1}</Badge> : null}
      </div>
      <dl className="lc-tl-grid">
        <div><dt>Vị trí video</dt><dd>{hms(position)} / {hms(state?.media_duration_ms)}</dd></div>
        <div><dt>Cần ghim lúc này</dt><dd>{state?.reported ? name(state.desired_product_id, 'Không ghim') : '—'}</dd></div>
        <div><dt>Đang ghim trên TikTok</dt><dd className={state?.reported && state.actual_known && state.actual_product_id !== state.desired_product_id ? 'is-diff' : ''}>{state?.reported ? actual : '—'}</dd></div>
        <div><dt>Mốc kế tiếp</dt><dd>{next ? `${hms(next.at_ms)} · ${next.action === 'UNPIN_PRODUCT' ? 'Bỏ ghim' : name(next.product_id, '—')} (còn ${hms(next.at_ms - (position ?? 0))})` : '—'}</dd></div>
      </dl>
      {state?.in_flight && <p className="lc-tl-note">Đang {state.in_flight.action === 'UNPIN_PRODUCT' ? 'bỏ ghim' : `ghim ${name(state.in_flight.product_id, '')}`} (lần thử {state.in_flight.attempt})…</p>}
      {state?.last_error && <p className="lc-pin-error"><CircleAlert size={13}/> {state.last_error}</p>}
      {versionMismatch && <p className="lc-tl-note">Agent đang chạy phiên bản {state?.agent_version ?? 'trống'}; Sarae đang gửi lại phiên bản {state?.active_version}.</p>}
      {hold && <div className="lc-tl-hold">
        <span>{state?.manual_policy === 'RESUME_AT_NEXT_MARKER' ? 'Timeline sẽ tự quay lại ở mốc kế tiếp.' : 'Timeline tạm dừng cho tới khi bạn bấm tiếp tục.'}</span>
        <Button size="sm" leftIcon={<PlayCircle size={15}/>} loading={busy} onClick={()=>void resume()}>Tiếp tục timeline</Button>
      </div>}
      <div className="lc-tl-policy">
        <small>Khi ghim / bỏ ghim thủ công:</small>
        <SegmentedControl<TimelinePolicy> value={policy} onChange={onPolicy} items={[
          {value:'HOLD_UNTIL_RESUME', label:'Giữ đến khi tiếp tục'}, {value:'RESUME_AT_NEXT_MARKER', label:'Quay lại ở mốc kế tiếp'},
        ]}/>
      </div>
    </div>
  </Card>
}
