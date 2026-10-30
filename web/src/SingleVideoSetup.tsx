import { useEffect, useMemo, useState } from 'react'
import { Badge, Button, Card, EmptyState, Modal, Select, Switch, useToast } from './ui/core'
import { ArrowDown, ArrowUp, CircleAlert, Clapperboard, Film, ListPlus, Package, Repeat, Video, X } from 'lucide-react'
import { errorText } from './mediaApi'
import { useLibraryVideos } from './libraryVideos'
import VideoPicker from './VideoPicker'
import { TIMELINE_STATUS, VIDEO_ORDER_LABEL, hms, sessionsApi, timelineHref, timelinesApi, type LiveProduct, type LiveSession, type PlaybackOrder, type Timeline } from './liveSessionsApi'
import VideoThumb from './VideoThumb'

/**
 * Setup of a Single Video Timeline session on the planner page: its videos (in playback order), their order and loop,
 * and the pin timeline it runs (edited in the Timeline Editor, markers per video). Replaces the product lineup of the
 * advanced mode.
 */
export default function SingleVideoSetup({session, productById, onChanged}:{session:LiveSession; productById:Map<number,LiveProduct>; onChanged:()=>Promise<void>}){
  const toast = useToast()
  const videos = useLibraryVideos()
  const [timelines,setTimelines] = useState<Timeline[]>([])
  const [busy,setBusy] = useState(false)
  const [picking,setPicking] = useState(false)
  const [choice,setChoice] = useState<string[]>([])
  const draft = session.status === 'DRAFT'
  const ids = session.media_ids
  const byId = useMemo(()=>new Map(videos.map(v=>[v.id, v])),[videos])
  const total = ids.reduce((sum, id)=>sum + (byId.get(id)?.duration_seconds ?? 0), 0)
  const active = timelines.find(t=>t.status === 'ACTIVE')
  const drafts = timelines.filter(t=>t.status === 'DRAFT').length
  useEffect(()=>{
    let alive = true
    void timelinesApi.list(session.id).then(list=>{if (alive) setTimelines(list)}).catch(()=>{if (alive) setTimelines([])})
    return ()=>{alive = false}
  },[session.id, session.active_timeline_id, ids.join()])

  const update = async (body:Parameters<typeof sessionsApi.update>[1], success:string) => {
    setBusy(true)
    try {await sessionsApi.update(session.id, body);toast.push({tone:'success', title:success});await onChanged()}
    catch(e){toast.push({tone:'danger', title:'Không lưu được', description:errorText(e, '')})}
    finally{setBusy(false)}
  }
  const move = (from:number, to:number) => {
    const next = [...ids];const [item] = next.splice(from, 1);next.splice(to, 0, item)
    void update({media_ids:next}, 'Đã đổi thứ tự video')
  }
  const productName = (id:number) => productById.get(id)?.name
  const openPicker = () => {setChoice(ids);setPicking(true)}
  const toggle = (id:string) => setChoice(prev=>prev.includes(id) ? prev.filter(v=>v !== id) : [...prev, id])
  const pinned = useMemo(()=>session.product_ids.map(id=>productById.get(id)?.name ?? `Sản phẩm #${id}`),[session.product_ids, productById])
  const random = session.video_order !== 'sequential'

  return <Card className="ls-lineup sv-setup" padding={false} title={<span className="ls-card-title"><Clapperboard size={17}/>Video & lịch ghim</span>}
    description="Các video phát nối tiếp nhau; sản phẩm được ghim tự động theo mốc thời gian của từng video.">
    <div className="sv-body">
      <div className="sv-videos-head">
        <strong>{ids.length ? `${ids.length} video` : 'Chưa chọn video'}</strong>
        {total > 0 && <span className="ls-muted"><Video size={13}/>Tổng {hms(total * 1000)}</span>}
        {draft && <Button size="sm" variant={ids.length ? 'outline' : 'primary'} leftIcon={<ListPlus size={14}/>} disabled={busy || !videos.length} onClick={openPicker}>
          {ids.length ? 'Thêm / bỏ video' : 'Chọn video'}</Button>}
      </div>
      {ids.length > 0 && <ol className="sv-videos">
        {ids.map((id, index)=>{
          const video = byId.get(id)
          return <li key={id} className="sv-video">
            <span className="sv-index">{random ? '•' : index + 1}</span>
            <div className="sv-thumb"><VideoThumb src={video?.thumb_url}/></div>
            <div className="sv-video-info">
              <strong className="sv-video-name">{video ? video.display_name || video.filename : 'Video không còn trong thư viện'}</strong>
              {video?.duration_seconds != null && <span className="ls-muted"><Video size={13}/>{hms(video.duration_seconds * 1000)}</span>}
            </div>
            {draft && <span className="sv-video-actions">
              <Button size="sm" variant="ghost" aria-label="Lên trên" disabled={busy || index === 0} onClick={()=>move(index, index - 1)}><ArrowUp size={14}/></Button>
              <Button size="sm" variant="ghost" aria-label="Xuống dưới" disabled={busy || index === ids.length - 1} onClick={()=>move(index, index + 1)}><ArrowDown size={14}/></Button>
              <Button size="sm" variant="ghost" aria-label="Bỏ khỏi phiên" disabled={busy} onClick={()=>void update({media_ids:ids.filter(v=>v !== id)}, 'Đã bỏ video khỏi phiên')}><X size={14}/></Button>
            </span>}
          </li>
        })}
      </ol>}
      <div className="sv-meta">
        <Select label="Thứ tự phát" value={session.video_order} disabled={busy || !draft || ids.length < 2}
          onChange={v=>void update({video_order:v as PlaybackOrder}, 'Đã đổi thứ tự phát')}
          options={(Object.keys(VIDEO_ORDER_LABEL) as PlaybackOrder[]).map(value=>({value, label:VIDEO_ORDER_LABEL[value]}))}/>
        <Switch label={session.video_loop ? 'Phát lặp lại' : 'Phát một lượt'} checked={session.video_loop} disabled={busy || session.status === 'ENDED'}
          onChange={e=>void update({video_loop:e.target.checked}, e.target.checked ? 'Danh sách video sẽ phát lặp lại' : 'Danh sách video sẽ phát một lượt')}/>
        <span className="ls-muted"><Repeat size={13}/>Sang video mới vẫn giữ sản phẩm đang ghim tới mốc đầu tiên của video đó</span>
      </div>

      <div className="sv-timeline">
        <div className="sv-timeline-head">
          <strong>Lịch ghim</strong>
          {active ? <Badge tone={TIMELINE_STATUS.ACTIVE.tone}>Phiên bản {active.version} · {active.marker_count} mốc</Badge>
            : <Badge tone="warning">Chưa kích hoạt</Badge>}
          {drafts > 0 && <Badge>{drafts} bản nháp</Badge>}
          <Button size="sm" disabled={!ids.length || session.status === 'ENDED'} title={ids.length ? undefined : 'Chọn video trước'}
            onClick={()=>{window.location.hash = timelineHref(session.id).slice(1)}}>Mở Timeline Editor</Button>
        </div>
        {!active ? <p className="sv-note"><CircleAlert size={14}/>Chưa có lịch ghim đang dùng: video vẫn phát nhưng không tự ghim sản phẩm.</p>
          : !pinned.length ? <p className="sv-note"><CircleAlert size={14}/>Lịch ghim không ghim sản phẩm nào.</p>
          : <ul className="sv-products">{pinned.map(name=><li key={name}><Package size={13}/>{name}</li>)}</ul>}
      </div>
      {picking && <Modal open title="Chọn video cho phiên" size="lg" onClose={()=>setPicking(false)} footer={<>
        <span className="ls-picker-count">Đã chọn {choice.length} video · phát theo thứ tự chọn</span>
        <Button variant="ghost" onClick={()=>setPicking(false)}>Hủy</Button>
        <Button loading={busy} disabled={choice.join() === ids.join()}
          onClick={()=>void update({media_ids:choice}, 'Đã cập nhật danh sách video').then(()=>setPicking(false))}>Lưu danh sách</Button>
      </>}>
        <div className="sv-picker">
          <VideoPicker videos={videos} value={choice} onChange={toggle} productName={productName} maxHeight={460}/>
        </div>
      </Modal>}
      {!videos.length && <EmptyState icon={<Film size={24}/>} title="Thư viện chưa có video" description="Tải video lên Thư viện video để chọn cho phiên." actionLabel="Mở Thư viện" onAction={()=>{window.location.hash = '/cloud-media'}}/>}
    </div>
  </Card>
}
