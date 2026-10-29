import { useEffect, useMemo, useRef, useState, type PointerEvent } from 'react'
import { Alert, Button, Input, SegmentedControl, Select, Switch } from './ui/core'
import { Check, ChevronsLeft, ChevronsRight, CircleAlert, CircleCheck, Clock, EyeOff, Loader2, Minus, Pause, Play, Plus, RotateCcw, Scissors, WifiOff, X, ZoomIn } from 'lucide-react'
import { errorText } from './mediaApi'
import { assetUrl } from './mediaApi'
import { createClips, type ClipMode, type ClipSpec, type CloudMedia } from './cloudMediaApi'
import { clipJobs, isFinal, useClipJobs, type TrackedClipJob } from './clipJobStore'
import { clearClipDraft, readClipDraft, writeClipDraft } from './clipDraft'
import { filmstrip } from './videoFrame'
import './media-clipper.css'

/** A piece of the video between two cut points; `key` is its start, so names/kept state survive new cuts elsewhere. */
type Segment = { key:string; start:number; end:number }
/** A clip as it was sent to the server, with what the progress screen shows for it. */
type SentClip = ClipSpec & { label:string; thumb:string|null }

const MIN_LENGTH = 0.5
const MAX_CLIPS = 50
const FRAME = 1 / 30
const ZOOMS = [1, 2, 4, 8, 16]
const TICKS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200]
const EVERY = [{value:'15', label:'15 giây'}, {value:'30', label:'30 giây'}, {value:'60', label:'1 phút'}, {value:'120', label:'2 phút'}, {value:'300', label:'5 phút'}]

const keyOf = (seconds:number) => seconds.toFixed(3)
/** 75.4 → "1:15.4", 3725 → "1:02:05.0" */
const clipTime = (seconds:number) => {
  const s = Math.max(0, seconds), h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), rest = (s % 60).toFixed(1).padStart(4, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${rest}` : `${m}:${rest}`
}
const isTyping = (target:EventTarget|null) => target instanceof HTMLElement && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))

/**
 * Full-screen cutter, like a phone video editor: scrub the frame strip, press ✂ to cut where the playhead is, tap a
 * piece to keep or drop it. Every kept piece becomes a new video of the library (the server cuts the original file).
 */
export default function MediaClipper({media,src,onClose}:{media:CloudMedia;src:string;onClose:()=>void}){
  const [draft] = useState(()=>readClipDraft(media.id))
  const [previousJob] = useState(()=>clipJobs.latestForMedia(media.id))
  const player = useRef<HTMLVideoElement>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const track = useRef<HTMLDivElement>(null)
  const timeline = useRef<HTMLElement>(null)
  const [now,setNow] = useState(0)
  const [playing,setPlaying] = useState(false)
  const [duration,setDuration] = useState(media.duration_seconds ?? 0)
  const [cuts,setCuts] = useState<number[]>(draft.cuts)
  const [dropped,setDropped] = useState<Set<string>>(()=>new Set(draft.dropped))
  const [names,setNames] = useState<Record<string, string>>(draft.names)
  const [frames,setFrames] = useState<(string|null)[]>([])
  const [zoom,setZoom] = useState(1)
  const [hover,setHover] = useState<{time:number; x:number}|null>(null)  // x: px inside the timeline section
  const [mode,setMode] = useState<ClipMode>(draft.mode)
  const [keepLinks,setKeepLinks] = useState(draft.keepLinks)
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const [notice,setNotice] = useState(draft.cuts.length ? 'Đã khôi phục các điểm cắt và lựa chọn của lần trước.' : '')
  const stopAt = useRef<number|null>(null)
  const scrubbing = useRef(false)
  const dragging = useRef<number|null>(null)
  const [jobId,setJobId] = useState<string|null>(previousJob?.job.id ?? null)
  const [sent,setSent] = useState<SentClip[]>(()=>previousJob?.request.clips.map((clip,i)=>({
    ...clip, label:clip.name || `${media.display_name} - đoạn ${i+1}`, thumb:null,
  })) ?? [])
  const tracked = useClipJobs()
  const job = tracked.find(t=>t.job.id === jobId)
  const running = tracked.some(t=>t.job.id !== jobId && !isFinal(t.job) && !t.lost)  // another cut still on the server

  useEffect(()=>{
    if (!jobId || job) return
    const next = tracked.findLast(t=>t.job.media_id === media.id)
    if (next) {
      setJobId(next.job.id)
      setSent(next.request.clips.map((clip,i)=>({...clip, label:clip.name || `${media.display_name} - đoạn ${i+1}`, thumb:null})))
    }
  },[jobId, job, tracked, media.id, media.display_name])

  const segments = useMemo<Segment[]>(()=>{
    const bounds = [0, ...cuts, duration]
    return bounds.slice(0, -1).map((start, i)=>({key:keyOf(start), start, end:bounds[i + 1]}))
  },[cuts, duration])
  const kept = segments.filter(s=>!dropped.has(s.key))
  const keptLength = kept.reduce((sum, s)=>sum + s.end - s.start, 0)
  const current = segments.find(s=>now >= s.start && now < s.end) ?? segments.at(-1)

  useEffect(()=>{
    writeClipDraft(media.id, {cuts, dropped:[...dropped], names, mode, keepLinks})
  },[media.id, cuts, dropped, names, mode, keepLinks])

  // ----- player: smooth playhead while playing, stop at the end of a previewed piece
  useEffect(()=>{
    const video = player.current
    if (!video) return
    let frame = 0
    const tick = () => {
      setNow(video.currentTime)
      if (stopAt.current != null && video.currentTime >= stopAt.current) {video.pause();stopAt.current = null}
      if (!video.paused) frame = requestAnimationFrame(tick)
    }
    const onPlay = () => {setPlaying(true);frame = requestAnimationFrame(tick)}
    const onPause = () => {setPlaying(false);cancelAnimationFrame(frame);setNow(video.currentTime)}
    const onMeta = () => {if (Number.isFinite(video.duration) && video.duration > 0) setDuration(video.duration)}
    const onSeek = () => setNow(video.currentTime)
    video.addEventListener('play', onPlay);video.addEventListener('pause', onPause)
    video.addEventListener('loadedmetadata', onMeta);video.addEventListener('seeked', onSeek)
    return ()=>{cancelAnimationFrame(frame);video.removeEventListener('play', onPlay);video.removeEventListener('pause', onPause)
      video.removeEventListener('loadedmetadata', onMeta);video.removeEventListener('seeked', onSeek)}
  },[])

  // ----- frame strip, read progressively from the stored video
  const frameCount = duration ? Math.min(60, Math.max(12, Math.ceil(duration))) : 0
  useEffect(()=>{
    if (!frameCount) return
    const controller = new AbortController()
    setFrames(Array.from({length:frameCount}, ()=>null))
    void filmstrip(src, frameCount, 96, (i, image)=>setFrames(list=>list.map((f, j)=>j === i ? image : f)), controller.signal)
    return ()=>controller.abort()
  },[src, frameCount])

  const seek = (seconds:number) => {
    const video = player.current
    if (!video || !duration) return
    stopAt.current = null
    video.currentTime = Math.max(0, Math.min(seconds, duration - 0.01))
    setNow(video.currentTime)
  }
  const toggle = () => {
    const video = player.current
    if (!video) return
    if (video.paused) {stopAt.current = null;void video.play()} else video.pause()
  }
  const previewPiece = (segment:Segment) => {
    const video = player.current
    if (!video) return
    video.currentTime = segment.start
    stopAt.current = segment.end
    void video.play()
  }

  // ----- cuts
  const cutHere = () => {
    const t = now
    if (t < MIN_LENGTH || t > duration - MIN_LENGTH) return setError('Không cắt sát đầu hoặc cuối video được.')
    if (cuts.some(c=>Math.abs(c - t) < MIN_LENGTH)) return setError('Đã có điểm cắt ở gần đây.')
    if (cuts.length + 1 >= MAX_CLIPS * 2) return setError('Quá nhiều điểm cắt.')
    setError('')
    setNotice(mode === 'COPY' ? 'Khi lưu, máy chủ sẽ căn điểm bắt đầu về khung hình gốc gần nhất ở phía trước.' : '')
    const owner = segments.find(s=>t > s.start && t < s.end)
    setCuts([...cuts, t].sort((a, b)=>a - b))
    if (owner && dropped.has(owner.key)) setDropped(set=>new Set(set).add(keyOf(t)))  // the new right part stays dropped too
  }
  const removeCut = (t:number) => {
    const key = keyOf(t)
    setCuts(list=>list.filter(c=>c !== t))
    setDropped(set=>{const next = new Set(set);next.delete(key);return next})
    setNames(map=>{const next = {...map};delete next[key];return next})
  }
  const moveCut = (from:number, to:number) => {
    const i = cuts.indexOf(from)
    if (i < 0) return from
    const low = (i ? cuts[i - 1] : 0) + MIN_LENGTH, high = (i < cuts.length - 1 ? cuts[i + 1] : duration) - MIN_LENGTH
    const value = Math.max(low, Math.min(high, to))
    if (value === from) return from
    const a = keyOf(from), b = keyOf(value)
    setCuts(list=>list.map(c=>c === from ? value : c))
    setDropped(set=>{if (!set.has(a)) return set;const next = new Set(set);next.delete(a);next.add(b);return next})
    setNames(map=>{if (!(a in map)) return map;const next = {...map, [b]:map[a]};delete next[a];return next})
    return value
  }
  const splitEvery = (value:string) => {
    const step = Number(value)
    if (!duration || !step) return
    const list:number[] = []
    for (let t = step; t <= duration - MIN_LENGTH; t += step) {
      if (t >= MIN_LENGTH && t <= duration - MIN_LENGTH && !list.some(c=>Math.abs(c - t) < MIN_LENGTH)) list.push(t)
    }
    if (list.length + 1 > MAX_CLIPS) return setError(`Sẽ tạo ${list.length + 1} đoạn, tối đa ${MAX_CLIPS}. Hãy chọn đoạn dài hơn.`)
    setError('');setCuts(list);setDropped(new Set());setNames({})
  }
  const flip = (segment:Segment) => setDropped(set=>{const next = new Set(set);if (!next.delete(segment.key)) next.add(segment.key);return next})

  // ----- keyboard: Space play, S cut, ←/→ 1 s (Shift 5 s), ,/. one frame
  useEffect(()=>{
    const onKey = (event:KeyboardEvent) => {
      if (isTyping(event.target) || event.ctrlKey || event.metaKey || event.altKey) return
      if (jobId && event.key !== 'Escape') return  // the progress screen covers the editor
      const actions:Record<string, ()=>void> = {
        ' ':toggle, s:cutHere, S:cutHere, ArrowLeft:()=>seek(now - (event.shiftKey ? 5 : 1)), ArrowRight:()=>seek(now + (event.shiftKey ? 5 : 1)),
        ',':()=>seek(now - FRAME), '.':()=>seek(now + FRAME), Escape:()=>{if (!busy) onClose()},
      }
      const action = actions[event.key]
      if (action) {event.preventDefault();action()}
    }
    window.addEventListener('keydown', onKey)
    return ()=>window.removeEventListener('keydown', onKey)
  })

  // ----- timeline pointer: drag to scrub, hover shows the exact frame
  const timeAt = (clientX:number) => {
    const box = track.current?.getBoundingClientRect()
    return box && duration ? Math.max(0, Math.min(duration, (clientX - box.left) / box.width * duration)) : 0
  }
  const onTrackDown = (event:PointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId)
    scrubbing.current = true
    player.current?.pause()
    seek(timeAt(event.clientX))
  }
  const onTrackMove = (event:PointerEvent<HTMLDivElement>) => {
    const t = timeAt(event.clientX)
    if (dragging.current != null) {dragging.current = moveCut(dragging.current, t);seek(dragging.current);return}
    // The preview is drawn in the timeline section (the scroller would clip it), at the mouse position.
    const box = timeline.current?.getBoundingClientRect()
    if (box) setHover({time:t, x:Math.max(60, Math.min(box.width - 60, event.clientX - box.left))})
    if (scrubbing.current) seek(t)
  }
  const onTrackUp = () => {scrubbing.current = false;dragging.current = null}

  // keep the playhead in view when zoomed in
  useEffect(()=>{
    const box = scroller.current
    if (!box || zoom === 1 || !duration) return
    const x = now / duration * box.scrollWidth
    if (x < box.scrollLeft + 40 || x > box.scrollLeft + box.clientWidth - 40) box.scrollLeft = x - box.clientWidth / 2
  },[now, zoom, duration])

  /** Send the clips; the cutter then shows the job's progress (it keeps running on the server if the cutter closes). */
  const start = async (clips:SentClip[]) => {
    setBusy(true);setError('')
    player.current?.pause()
    try{
      const created = await createClips(media.id, clips.map(({start, end, name})=>({start, end, name})), mode, keepLinks)
      clipJobs.track(created, media.display_name, {clips:clips.map(({start,end,name})=>({start,end,name})), mode, keepLinks})
      setSent(clips);setJobId(created.id)
    }catch(e){setError(errorText(e, 'Không bắt đầu cắt được video'))}
    finally{setBusy(false)}
  }
  const submit = () => start(kept.map((s, i)=>{
    const name = (names[s.key] ?? '').trim()
    return {start:Number(s.start.toFixed(3)), end:Number(s.end.toFixed(3)), name, label:name || `${media.display_name} - đoạn ${i + 1}`, thumb:thumbAt(s.start + 0.01)}
  }))
  const cutMore = () => {clearClipDraft(media.id);setJobId(null);setSent([]);setCuts([]);setDropped(new Set());setNames({})}
  const retry = async () => {
    if (!job) return
    const remaining = sent.slice(job.job.done)
    try{
      const next = await clipJobs.retry(job.job.id)
      setSent(remaining)
      setJobId(next.id)
    }catch{/* The progress panel displays the retry error. */}
  }

  const pct = (t:number) => duration ? `${t / duration * 100}%` : '0%'
  const tick = TICKS.find(step=>duration / step <= 8 * zoom) ?? TICKS.at(-1) as number
  const ticks = duration ? Array.from({length:Math.floor(duration / tick) + 1}, (_, i)=>i * tick) : []
  const thumbAt = (t:number) => frames[Math.min(frames.length - 1, Math.floor(t / (duration || 1) * frames.length))] ?? null
  const keptNumber = (segment:Segment) => kept.indexOf(segment) + 1
  const canSubmit = cuts.length > 0 && kept.length > 0 && kept.length <= MAX_CLIPS && !running

  return <div className="mce" role="dialog" aria-modal="true" aria-label={'Cắt video ' + media.display_name}>
    <header className="mce-head">
      <Scissors size={18}/><strong>Cắt video</strong><span className="mce-title" title={media.display_name}>{media.display_name}</span>
      <button type="button" className="mce-close" aria-label="Đóng trình cắt" onClick={()=>{if (!busy) onClose()}}><X size={18}/></button>
    </header>

    {job && <ClipProgress tracked={job} clips={sent} onClose={onClose} onEdit={()=>{clipJobs.dismiss(job.job.id);setJobId(null)}} onCutMore={cutMore}
      onRetry={()=>void retry()}/>}

    <div className="mce-body">
      <section className="mce-stage">
        <div className="mce-player">
          <video ref={player} src={src} crossOrigin="anonymous" playsInline preload="auto" onClick={toggle}/>
          {current && cuts.length > 0 && <span className={'mce-badge' + (dropped.has(current.key) ? ' is-dropped' : '')}>
            {dropped.has(current.key) ? 'Đoạn bị bỏ' : `Đoạn ${keptNumber(current)}`}</span>}
        </div>
        <div className="mce-controls">
          <button type="button" className="mce-icon" title="Lùi 1 khung hình (,)" aria-label="Lùi 1 khung hình" onClick={()=>seek(now - FRAME)}><ChevronsLeft size={18}/></button>
          <button type="button" className="mce-play" aria-label={playing ? 'Tạm dừng' : 'Phát'} title="Phát / dừng (Space)" onClick={toggle}>
            {playing ? <Pause size={20} fill="currentColor"/> : <Play size={20} fill="currentColor"/>}</button>
          <button type="button" className="mce-icon" title="Tiến 1 khung hình (.)" aria-label="Tiến 1 khung hình" onClick={()=>seek(now + FRAME)}><ChevronsRight size={18}/></button>
          <span className="mce-clock"><b>{clipTime(now)}</b> / {clipTime(duration)}</span>
          <button type="button" className="mce-cut" onClick={cutHere} title="Cắt tại vị trí đang xem (phím S)"><Scissors size={18}/>Cắt tại đây</button>
        </div>
      </section>

      <aside className="mce-side">
        <div className="mce-side-head">
          <strong>{cuts.length ? `${kept.length} đoạn sẽ được lưu` : 'Cách cắt'}</strong>
          {cuts.length > 0 && <span>tổng {clipTime(keptLength)}</span>}
        </div>
        {cuts.length === 0 ? <ol className="mce-steps">
            <li><b>Tua</b> tới chỗ muốn cắt: kéo trên dải hình bên dưới (rê chuột để xem trước khung hình), hoặc phím ← →.</li>
            <li>Bấm <b>✂ Cắt tại đây</b> (phím S). Lặp lại ở mỗi chỗ muốn cắt.</li>
            <li><b>Bấm vào đoạn</b> không cần để bỏ đi (ví dụ phần đầu/cuối thừa).</li>
            <li>Bấm <b>Lưu</b>: mỗi đoạn giữ lại thành một video mới, video gốc vẫn còn.</li>
          </ol>
        : <ul className="mce-pieces">{segments.map(s=>{
          const off = dropped.has(s.key), thumb = thumbAt(s.start + 0.01)
          return <li key={s.key} className={'mce-piece' + (off ? ' is-dropped' : '') + (current === s ? ' is-current' : '')}>
            <button type="button" className="mce-piece-thumb" onClick={()=>previewPiece(s)} title="Xem đoạn này">
              {thumb ? <img src={thumb} alt=""/> : <span/>}<Play size={14} fill="currentColor"/></button>
            <div className="mce-piece-main">
              <div className="mce-piece-top">
                <strong>{off ? 'Bỏ' : `Đoạn ${keptNumber(s)}`}</strong>
                <span>{clipTime(s.start)} → {clipTime(s.end)} · {clipTime(s.end - s.start)}</span>
              </div>
              {!off && <Input placeholder={`${media.display_name} - đoạn ${keptNumber(s)}`} maxLength={200} value={names[s.key] ?? ''}
                onChange={e=>setNames(map=>({...map, [s.key]:e.target.value}))} aria-label="Tên video mới"/>}
            </div>
            <button type="button" className={'mce-keep' + (off ? '' : ' is-on')} onClick={()=>flip(s)} title={off ? 'Giữ lại đoạn này' : 'Bỏ đoạn này'}
              aria-label={off ? 'Giữ lại đoạn này' : 'Bỏ đoạn này'}>{off ? <EyeOff size={15}/> : <Check size={15}/>}</button>
          </li>
        })}</ul>}
      </aside>
    </div>

    <section className="mce-timeline" ref={timeline}>
      <div className="mce-timeline-bar">
        <span className="mce-hint">Kéo trên dải hình để tua · bấm thanh màu để giữ/bỏ đoạn · kéo nút ✂ để chỉnh điểm cắt</span>
        <span className="mce-tools">
          <span className="mce-every"><Select value="" onChange={splitEvery} options={[{value:'', label:'Chia đều mỗi…'}, ...EVERY]}/></span>
          {cuts.length > 0 && <Button size="sm" variant="ghost" onClick={()=>{setCuts([]);setDropped(new Set());setNames({})}}>Bỏ hết điểm cắt</Button>}
          <span className="mce-zoom"><ZoomIn size={15}/>
            <button type="button" aria-label="Thu nhỏ" disabled={zoom === ZOOMS[0]} onClick={()=>setZoom(ZOOMS[Math.max(0, ZOOMS.indexOf(zoom) - 1)])}><Minus size={14}/></button>
            <span>{zoom}×</span>
            <button type="button" aria-label="Phóng to" disabled={zoom === ZOOMS.at(-1)} onClick={()=>setZoom(ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS.indexOf(zoom) + 1)])}><Plus size={14}/></button>
          </span>
        </span>
      </div>
      <div className="mce-scroll" ref={scroller}>
        <div className="mce-track" ref={track} style={{width:`${zoom * 100}%`}}
          onPointerDown={onTrackDown} onPointerMove={onTrackMove} onPointerUp={onTrackUp} onPointerCancel={onTrackUp} onPointerLeave={()=>setHover(null)}>
          <div className="mce-ruler">{ticks.map(t=><span key={t} style={{left:pct(t)}}>{clipTime(t).replace(/\.0$/, '')}</span>)}</div>
          <div className="mce-strip">{frames.map((f, i)=><span key={i} style={f ? {backgroundImage:`url(${f})`} : undefined}/>)}</div>
          <div className="mce-lane">{segments.map(s=>{
            const off = dropped.has(s.key)
            return <button key={s.key} type="button" className={'mce-seg' + (off ? ' is-dropped' : '')} style={{left:pct(s.start), width:`calc(${pct(s.end)} - ${pct(s.start)})`}}
              onPointerDown={e=>e.stopPropagation()} onClick={()=>flip(s)} title={off ? 'Bấm để giữ lại' : 'Bấm để bỏ đoạn này'}>
              {off ? <><EyeOff size={12}/>Bỏ</> : cuts.length > 0 ? <b>Đoạn {keptNumber(s)}</b> : <span>Toàn bộ video</span>}</button>
          })}</div>
          {cuts.map(c=><div key={c} className="mce-cutline" style={{left:pct(c)}}>
            <button type="button" className="mce-cutgrip" title="Kéo để chỉnh điểm cắt" aria-label={'Điểm cắt ' + clipTime(c)}
              onPointerDown={e=>{e.stopPropagation();track.current?.setPointerCapture(e.pointerId);dragging.current = c;player.current?.pause()}}><Scissors size={11}/></button>
            <button type="button" className="mce-cutx" title="Xóa điểm cắt" aria-label="Xóa điểm cắt" onPointerDown={e=>e.stopPropagation()} onClick={()=>removeCut(c)}><X size={10}/></button>
          </div>)}
          <div className="mce-head-line" style={{left:pct(now)}}/>
        </div>
      </div>
      <HoverFrame src={src} time={hover?.time ?? null} left={`${hover?.x ?? 0}px`}/>
    </section>

    <footer className="mce-foot">
      <div className="mce-opts">
        <SegmentedControl<ClipMode> value={mode} onChange={setMode} items={[
          {value:'COPY', label:'Giữ nguyên 100% chất lượng'}, {value:'PRECISE', label:'Cắt chính xác từng khung hình'}]}/>
        <Switch checked={keepLinks} onChange={e=>setKeepLinks(e.target.checked)} label="Chép sản phẩm, người, tags sang video mới"/>
        <span className="mce-mode-hint">{mode === 'COPY'
          ? 'Không mã hóa lại: chất lượng y hệt gốc. Điểm bắt đầu sẽ được căn về khung hình gốc gần nhất ở phía trước khi lưu; đoạn mới có thể bắt đầu sớm hơn điểm bạn chọn. Muốn đúng vị trí, chọn "Cắt chính xác".'
          : 'Bắt đầu đúng khung hình đã cắt; mã hóa lại ở chất lượng gần như không đổi nên chậm hơn.'}</span>
      </div>
      {error ? <Alert tone="warning" onClose={()=>setError('')}>{error}</Alert>
        : notice && <Alert tone="info" onClose={()=>setNotice('')}>{notice}</Alert>}
      <div className="mce-actions">
        <Button variant="ghost" disabled={busy} onClick={onClose}>Hủy</Button>
        <Button leftIcon={<Scissors size={16}/>} loading={busy} disabled={!canSubmit} onClick={()=>void submit()}>
          {running ? 'Đang cắt video khác, chờ xong…' : cuts.length ? `Lưu ${kept.length} đoạn thành video mới` : 'Hãy cắt ít nhất 1 lần'}</Button>
      </div>
    </footer>
  </div>
}

/** Exact frame under the mouse, read by a second hidden player that seeks only once its previous seek finished.
 * It stays mounted (hidden when `time` is null) so the video is loaded once. */
function HoverFrame({src,time,left}:{src:string;time:number|null;left:string}){
  const video = useRef<HTMLVideoElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const want = useRef(0)
  const seeking = useRef(false)
  useEffect(()=>{
    if (time == null) return
    want.current = time
    const v = video.current
    if (v && v.readyState >= 1 && !seeking.current) {seeking.current = true;v.currentTime = time}
  },[time])
  const draw = () => {
    const v = video.current, c = canvas.current
    seeking.current = false
    if (!v || !c) return
    if (v.videoWidth && c.width !== Math.round(c.height * v.videoWidth / v.videoHeight)) c.width = Math.round(c.height * v.videoWidth / v.videoHeight)
    try{c.getContext('2d')?.drawImage(v, 0, 0, c.width, c.height)}catch{/* frame not readable: keep the last one */}
    if (Math.abs(v.currentTime - want.current) > 0.05) {seeking.current = true;v.currentTime = want.current}
  }
  return <div className="mce-hover" style={{left, visibility:time == null ? 'hidden' : 'visible'}}>
    <canvas ref={canvas} width={90} height={160}/>
    <span>{clipTime(time ?? 0)}</span>
    <video ref={video} src={src} crossOrigin="anonymous" muted preload="auto" onSeeked={draw} hidden/>
  </div>
}

const STEPS = [
  {label:'Lấy video gốc từ kho', steps:['QUEUED', 'DOWNLOAD', 'ANALYZE']},
  {label:'Cắt và lưu từng đoạn', steps:['CUT', 'SAVE']},
  {label:'Hoàn tất, thêm vào thư viện', steps:['DONE']},
]
const minutes = (ms:number) => {const s = Math.max(0, Math.round(ms / 1000));return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`}

/** What the server is doing right now, step by step and clip by clip; the result (or the error) once it ends. */
function ClipProgress({tracked,clips,onClose,onEdit,onCutMore,onRetry}:{tracked:TrackedClipJob;clips:SentClip[];onClose:()=>void;onEdit:()=>void;
  onCutMore:()=>void;onRetry:()=>void}){
  const {job, lost, retrying, retryError} = tracked
  const done = job.status === 'DONE', failed = job.status === 'FAILED', active = !done && !failed && !lost
  const [now,setNow] = useState(()=>Date.now())
  useEffect(()=>{
    if (!active) return
    const timer = window.setInterval(()=>setNow(Date.now()), 1000)
    return ()=>window.clearInterval(timer)
  },[active])
  const elapsed = now - tracked.startedAt
  const percent = Math.round(job.progress * 100)
  const left = active && job.progress > 0.2 && elapsed > 4000 ? elapsed / job.progress * (1 - job.progress) : null
  const stepIndex = STEPS.findIndex(s=>s.steps.includes(job.step))
  const items = new Map((job.items ?? []).map(m=>[m.id, m]))

  return <div className="mcp">
    <div className="mcp-card">
      <div className={'mcp-hero' + (done ? ' is-done' : failed || lost ? ' is-bad' : '')}>
        {done ? <CircleCheck size={40}/> : failed ? <CircleAlert size={40}/> : lost ? <WifiOff size={40}/> : <Loader2 size={40} className="mcp-spin"/>}
        <div>
          <h2>{done ? `Đã cắt xong ${job.done} video` : failed ? 'Cắt video thất bại' : lost ? 'Mất kết nối với máy chủ' : 'Đang cắt video…'}</h2>
          <p>{done ? 'Các video mới đã có trong thư viện, video gốc vẫn giữ nguyên.'
            : failed ? (job.error || 'Có lỗi khi cắt video.') + (job.done ? ` ${job.done} đoạn đầu đã lưu xong vẫn được giữ trong thư viện.` : '')
            : lost ? `Không hỏi được tiến độ (${lost}). Máy chủ có thể vẫn đang cắt.`
            : job.phase}</p>
        </div>
      </div>

      <div className="mcp-bar"><span className={failed || lost ? 'is-bad' : done ? 'is-done' : ''} style={{width:`${Math.max(done ? 100 : 3, percent)}%`}}/></div>
      <div className="mcp-meta">
        <span><b>{done ? 100 : percent}%</b> · {job.done}/{job.total} đoạn xong</span>
        <span><Clock size={13}/> {minutes(elapsed)}{left != null ? ` · còn khoảng ${minutes(left)}` : ''}</span>
      </div>
      {retryError && <Alert tone="danger">{retryError}</Alert>}

      <ol className="mcp-steps">{STEPS.map((step, i)=>{
        const state = done || i < stepIndex ? 'done' : i === stepIndex ? (failed ? 'bad' : 'active') : 'wait'
        return <li key={step.label} className={'is-' + state}>
          <span className="mcp-dot">{state === 'done' ? <Check size={13}/> : state === 'bad' ? <X size={13}/> : state === 'active' && active ? <Loader2 size={13} className="mcp-spin"/> : i + 1}</span>
          {step.label}{i === 1 && stepIndex === 1 && !done ? ` (${Math.min(job.current, job.total)}/${job.total})` : ''}
        </li>
      })}</ol>

      <ul className="mcp-clips">{clips.map((clip, i)=>{
        const n = i + 1, saved = items.get(job.media_ids[i]), working = n === job.current && (job.step === 'CUT' || job.step === 'SAVE')
        const state = n <= job.done ? 'done' : failed && n === job.current ? 'bad' : active && working ? 'active' : 'wait'
        const thumb = assetUrl(saved?.thumb_url) ?? clip.thumb
        return <li key={i} className={'is-' + state}>
          <span className="mcp-thumb">{thumb ? <img src={thumb} alt=""/> : null}</span>
          <span className="mcp-clip-main"><strong title={clip.label}>{clip.label}</strong>
            <small>{clipTime(clip.start)} → {clipTime(clip.end)} · {clipTime(clip.end - clip.start)}{saved?.duration_seconds ? ` · file thật ${clipTime(saved.duration_seconds)}` : ''}</small></span>
          <span className="mcp-clip-state">{state === 'done' ? <><CircleCheck size={15}/>Đã lưu</>
            : state === 'bad' ? <><CircleAlert size={15}/>Lỗi</>
            : state === 'active' ? <><Loader2 size={15} className="mcp-spin"/>{job.step === 'CUT' ? 'Đang cắt' : 'Đang lưu'}</>
            : failed ? 'Chưa làm' : 'Đang chờ'}</span>
        </li>
      })}</ul>

      <div className="mcp-actions">
        {active && <>
          <span className="mcp-note">Có thể đóng cửa sổ này: máy chủ vẫn tiếp tục cắt, tiến độ hiện ở góc dưới bên phải mọi trang.</span>
          <Button variant="outline" onClick={onClose}>Chạy nền và đóng</Button>
        </>}
        {lost && <Button leftIcon={<RotateCcw size={15}/>} onClick={()=>clipJobs.resume(job.id)}>Kiểm tra lại</Button>}
        {failed && <>
          <Button variant="ghost" onClick={onEdit}>Quay lại chỉnh</Button>
          <Button leftIcon={<RotateCcw size={15}/>} loading={retrying} onClick={onRetry}>Thử lại đoạn chưa xong</Button>
        </>}
        {done && <>
          <Button variant="ghost" onClick={onCutMore}>Cắt tiếp video này</Button>
          <Button leftIcon={<Check size={16}/>} onClick={()=>{clearClipDraft(job.media_id);clipJobs.dismiss(job.id);onClose()}}>Xong</Button>
        </>}
      </div>
    </div>
  </div>
}
