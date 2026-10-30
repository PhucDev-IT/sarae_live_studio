import { useCallback, useEffect, useState } from 'react'
import { Badge, Card } from './ui/core'
import { CircleAlert, Pencil, Pin, ShieldCheck, ShieldQuestion } from 'lucide-react'
import { hms, timelineHref, timelinesApi, type LiveSession, type PinStateView, type TimelineMarker } from './liveSessionsApi'
import { useLibraryVideos } from './libraryVideos'

const POLL_MS = 3000

/**
 * LIVE console panel of a Single Video session: the video playing, the pin the timeline wants at its current position vs
 * what is pinned on TikTok (as the Agent last reported), the next marker of that video, the LIVE gate and dry-run
 * state. Read only: Sarae pins by the timeline (it always wins); changing what is pinned means editing the timeline.
 */
export default function TimelinePinPanel({session, productName}:{session:LiveSession; productName:(id:number)=>string}){
  const [state,setState] = useState<PinStateView|null>(null)
  const [markers,setMarkers] = useState<TimelineMarker[]>([])
  const videos = useLibraryVideos(session.media_ids.length > 1)
  const poll = useCallback(async ()=>{try{setState(await timelinesApi.pinState(session.id))}catch{/* keep the last one */}},[session.id])
  useEffect(()=>{
    void poll()
    const timer = window.setInterval(()=>void poll(), POLL_MS)
    return ()=>window.clearInterval(timer)
  },[poll])
  useEffect(()=>{
    let alive = true
    if (session.active_timeline_id == null) return
    void timelinesApi.get(session.id, session.active_timeline_id).then(t=>{if (alive) setMarkers((t.markers ?? []).filter(m=>m.review_status === 'APPROVED' && m.action !== 'NO_ACTION'))})
      .catch(()=>{if (alive) setMarkers([])})
    return ()=>{alive = false}
  },[session.id, session.active_timeline_id])

  const position = state?.position_ms ?? null
  const playing = state?.media_id ?? null
  // The next marker of the video playing (a marker without video, from an older Agent report, belongs to any).
  const next = position == null ? undefined : markers.find(m=>m.at_ms > position && (playing == null || m.media_id === playing))
  const videoNo = playing ? session.media_ids.indexOf(playing) + 1 : 0
  const videoName = videos.find(v=>v.id === playing)?.display_name || videos.find(v=>v.id === playing)?.filename
  const name = (id:number|null|undefined, fallback:string) => id == null ? fallback : productName(id)
  const actual = state?.actual_known === false ? 'Chưa xác định'
    : state?.actual_tiktok_product_id ? `Card TikTok ${state.actual_tiktok_product_id} (ngoài lịch)`
    : name(state?.actual_product_id, 'Không ghim')
  const differs = !!state?.reported && !!state.actual_known && (state.actual_product_id !== state.desired_product_id || !!state.actual_tiktok_product_id)
  const versionMismatch = state?.reported && state.active_version !== state.agent_version

  return <Card className="lc-timeline" padding={false} title={<span className="lc-card-title"><Pin size={17}/>Ghim tự động theo lịch</span>}
    description={state?.active_version != null ? `Phiên bản ${state.active_version} · ${markers.length} mốc ghim/bỏ ghim` : 'Chưa kích hoạt lịch ghim: video phát nhưng không tự ghim'}
    actions={<a className="lc-icon-link" href={timelineHref(session.id)} title="Sửa lịch ghim" aria-label="Sửa lịch ghim"><Pencil size={15}/></a>}>
    <div className="lc-tl-body">
      <div className="lc-tl-badges">
        {state?.live === true ? <Badge tone="success"><ShieldCheck size={12}/>Console: đang LIVE</Badge>
          : <Badge tone="warning"><ShieldQuestion size={12}/>{state?.live === false ? 'Console: chưa LIVE' : 'Console: chưa xác định LIVE'}</Badge>}
        {state?.dry_run && <Badge tone="info">Dry-run: không bấm trên TikTok</Badge>}
        {!state?.agent_online && <Badge tone="danger">Agent offline</Badge>}
        {state?.reported && state.iteration ? <Badge>{session.media_ids.length > 1 ? `Lượt video thứ ${state.iteration + 1}` : `Vòng lặp ${state.iteration + 1}`}</Badge> : null}
      </div>
      <dl className="lc-tl-grid">
        {session.media_ids.length > 1 && <div><dt>Video đang phát</dt><dd>{videoNo ? `${videoNo}/${session.media_ids.length}${videoName ? ' · ' + videoName : ''}` : '—'}</dd></div>}
        <div><dt>Vị trí video</dt><dd>{hms(position)} / {hms(state?.media_duration_ms)}</dd></div>
        <div><dt>Cần ghim lúc này</dt><dd>{state?.reported ? name(state.desired_product_id, 'Không ghim') : '—'}</dd></div>
        <div><dt>Đang ghim trên TikTok</dt><dd className={differs ? 'is-diff' : ''}>{state?.reported ? actual : '—'}</dd></div>
        <div><dt>Mốc kế tiếp</dt><dd>{next ? `${hms(next.at_ms)} · ${next.action === 'UNPIN_PRODUCT' ? 'Bỏ ghim' : name(next.product_id, '—')} (còn ${hms(next.at_ms - (position ?? 0))})` : '—'}</dd></div>
      </dl>
      {state?.in_flight && <p className="lc-tl-note">Đang {state.in_flight.action === 'UNPIN_PRODUCT' ? 'bỏ ghim' : `ghim ${name(state.in_flight.product_id, '')}`} (lần thử {state.in_flight.attempt})…</p>}
      {differs && !state?.in_flight && state?.live && <p className="lc-tl-note">Ghim trên TikTok khác lịch: Sarae sẽ ghim lại đúng sản phẩm theo lịch.</p>}
      {state?.last_error && <p className="lc-pin-error"><CircleAlert size={13}/> {state.last_error}</p>}
      {versionMismatch && <p className="lc-tl-note">Agent đang chạy phiên bản {state?.agent_version ?? 'trống'}; Sarae đang gửi lại phiên bản {state?.active_version}.</p>}
    </div>
  </Card>
}
