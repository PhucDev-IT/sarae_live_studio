import { useEffect, useMemo, useState } from 'react'
import { Badge, Button, Card, EmptyState, Select, Switch, useToast } from './ui/core'
import { CircleAlert, Clapperboard, Film, Package, Repeat, Video } from 'lucide-react'
import { errorText } from './mediaApi'
import { useLibraryVideos, videoOptions } from './libraryVideos'
import { TIMELINE_STATUS, hms, sessionsApi, timelineHref, timelinesApi, type LiveProduct, type LiveSession, type Timeline } from './liveSessionsApi'
import VideoThumb from './VideoThumb'

/**
 * Setup of a Single Video Timeline session on the planner page: its main video, loop, and the pin timeline it runs
 * (edited in the Timeline Editor). Replaces the product lineup of the advanced mode.
 */
export default function SingleVideoSetup({session, productById, onChanged}:{session:LiveSession; productById:Map<number,LiveProduct>; onChanged:()=>Promise<void>}){
  const toast = useToast()
  const videos = useLibraryVideos()
  const [timelines,setTimelines] = useState<Timeline[]>([])
  const [busy,setBusy] = useState(false)
  const draft = session.status === 'DRAFT'
  const video = videos.find(v=>v.id === session.main_media_id)
  const active = timelines.find(t=>t.status === 'ACTIVE')
  const drafts = timelines.filter(t=>t.status === 'DRAFT').length
  useEffect(()=>{
    let alive = true
    void timelinesApi.list(session.id).then(list=>{if (alive) setTimelines(list)}).catch(()=>{if (alive) setTimelines([])})
    return ()=>{alive = false}
  },[session.id, session.active_timeline_id, session.main_media_id])

  const update = async (body:Parameters<typeof sessionsApi.update>[1], success:string) => {
    setBusy(true)
    try {await sessionsApi.update(session.id, body);toast.push({tone:'success', title:success});await onChanged()}
    catch(e){toast.push({tone:'danger', title:'Không lưu được', description:errorText(e, '')})}
    finally{setBusy(false)}
  }
  const pinned = useMemo(()=>session.product_ids.map(id=>productById.get(id)?.name ?? `Sản phẩm #${id}`),[session.product_ids, productById])

  return <Card className="ls-lineup sv-setup" padding={false} title={<span className="ls-card-title"><Clapperboard size={17}/>Video chính & lịch ghim</span>}
    description="Một video phát liên tục; sản phẩm được ghim tự động theo mốc thời gian của video.">
    <div className="sv-body">
      <div className="sv-video">
        <div className="sv-thumb">{session.main_media_id ? <VideoThumb src={video?.thumb_url}/> : <Film size={28}/>}</div>
        <div className="sv-video-info">
          {draft ? <Select label="Video chính" value={session.main_media_id ?? ''} disabled={busy}
              onChange={id=>{if (id && id !== session.main_media_id) void update({main_media_id:id}, 'Đã đổi video chính')}}
              options={[...(session.main_media_id ? [] : [{value:'', label:'Chọn video trong Thư viện…'}]), ...videoOptions(videos)]}
              hint={session.active_timeline_id ? 'Đổi video sẽ gỡ timeline đang dùng (mốc thời gian thuộc video cũ).' : undefined}/>
            : <><small className="ls-muted">Video chính</small><strong>{video?.display_name ?? video?.filename ?? session.main_media_id ?? '—'}</strong></>}
          <span className="sv-meta">
            {video?.duration_seconds != null && <span><Video size={13}/>{hms(video.duration_seconds * 1000)}</span>}
            <Switch label={session.video_loop ? 'Phát lặp lại' : 'Phát một lượt'} checked={session.video_loop} disabled={busy || session.status === 'ENDED'}
              onChange={e=>void update({video_loop:e.target.checked}, e.target.checked ? 'Video sẽ phát lặp lại' : 'Video sẽ phát một lượt')}/>
            <span className="ls-muted"><Repeat size={13}/>Mỗi vòng lặp chạy lại lịch ghim từ đầu</span>
          </span>
        </div>
      </div>

      <div className="sv-timeline">
        <div className="sv-timeline-head">
          <strong>Lịch ghim</strong>
          {active ? <Badge tone={TIMELINE_STATUS.ACTIVE.tone}>Phiên bản {active.version} · {active.marker_count} mốc</Badge>
            : <Badge tone="warning">Chưa kích hoạt</Badge>}
          {drafts > 0 && <Badge>{drafts} bản nháp</Badge>}
          <Button size="sm" disabled={!session.main_media_id || session.status === 'ENDED'} title={session.main_media_id ? undefined : 'Chọn video chính trước'}
            onClick={()=>{window.location.hash = timelineHref(session.id).slice(1)}}>Mở Timeline Editor</Button>
        </div>
        {!active ? <p className="sv-note"><CircleAlert size={14}/>Chưa có lịch ghim đang dùng: video vẫn phát nhưng không tự ghim sản phẩm.</p>
          : !pinned.length ? <p className="sv-note"><CircleAlert size={14}/>Lịch ghim không ghim sản phẩm nào.</p>
          : <ul className="sv-products">{pinned.map(name=><li key={name}><Package size={13}/>{name}</li>)}</ul>}
      </div>
      {!videos.length && <EmptyState icon={<Film size={24}/>} title="Thư viện chưa có video" description="Tải video lên Thư viện video để chọn làm video chính." actionLabel="Mở Thư viện" onAction={()=>{window.location.hash = '/cloud-media'}}/>}
    </div>
  </Card>
}
