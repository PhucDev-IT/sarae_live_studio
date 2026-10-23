import { useEffect, useMemo, useRef, useState, type PointerEvent } from 'react'
import { Alert, Button, Input, SegmentedControl, Select, Switch } from './ui/core'
import { Check, ChevronsLeft, ChevronsRight, EyeOff, Minus, Pause, Play, Plus, Scissors, X, ZoomIn } from 'lucide-react'
import { errorText } from './mediaApi'
import { createClips, getKeyframes, type ClipJob, type ClipMode, type CloudMedia } from './cloudMediaApi'
import { filmstrip } from './videoFrame'
import './media-clipper.css'

/** A piece of the video between two cut points; `key` is its start, so names/kept state survive new cuts elsewhere. */
type Segment = { key:string; start:number; end:number }

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
export default function MediaClipper({media,src,running,onClose,onStarted}:{media:CloudMedia;src:string;running:boolean;
  onClose:()=>void;onStarted:(job:ClipJob)=>void}){
  const player = useRef<HTMLVideoElement>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const track = useRef<HTMLDivElement>(null)
  const timeline = useRef<HTMLElement>(null)
  const [now,setNow] = useState(0)
  const [playing,setPlaying] = useState(false)
  const [duration,setDuration] = useState(media.duration_seconds ?? 0)
  const [cuts,setCuts] = useState<number[]>([])
  const [dropped,setDropped] = useState<Set<string>>(new Set())
  const [names,setNames] = useState<Record<string, string>>({})
  const [frames,setFrames] = useState<(string|null)[]>([])
  const [zoom,setZoom] = useState(1)
  const [hover,setHover] = useState<{time:number; x:number}|null>(null)  // x: px inside the timeline section
  const [mode,setMode] = useState<ClipMode>('COPY')
  const [keepLinks,setKeepLinks] = useState(true)
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const [notice,setNotice] = useState('')
  const [keyframes,setKeyframes] = useState<number[]|null>(null)  // null while the server reads them
  const stopAt = useRef<number|null>(null)
  const scrubbing = useRef(false)
  const dragging = useRef<number|null>(null)

  const segments = useMemo<Segment[]>(()=>{
    const bounds = [0, ...cuts, duration]
    return bounds.slice(0, -1).map((start, i)=>({key:keyOf(start), start, end:bounds[i + 1]}))
  },[cuts, duration])
  const kept = segments.filter(s=>!dropped.has(s.key))
  const keptLength = kept.reduce((sum, s)=>sum + s.end - s.start, 0)
  const current = segments.find(s=>now >= s.start && now < s.end) ?? segments.at(-1)

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

  // ----- keyframes: the only places where COPY mode can cut (no re-encoding)
  useEffect(()=>{
    let alive = true
    getKeyframes(media.id).then(r=>{if (alive) setKeyframes(r.keyframes)}).catch(()=>{if (alive) setKeyframes([])})
    return ()=>{alive = false}
  },[media.id])
  const snapping = mode === 'COPY' && !!keyframes?.length
  /** In COPY mode a cut moves to the nearest keyframe. */
  const snap = (t:number, list = keyframes) => {
    if (mode !== 'COPY' || !list?.length) return t
    return list.reduce((best, k)=>Math.abs(k - t) < Math.abs(best - t) ? k : best, list[0])
  }

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
    if (mode === 'COPY' && keyframes == null) return setError('Đang đọc vị trí khung hình gốc, chờ vài giây rồi cắt lại.')
    const t = snap(now)
    if (t < MIN_LENGTH || t > duration - MIN_LENGTH) return setError('Không cắt sát đầu hoặc cuối video được.')
    if (cuts.some(c=>Math.abs(c - t) < MIN_LENGTH)) return setError('Đã có điểm cắt ở gần đây.')
    if (cuts.length + 1 >= MAX_CLIPS * 2) return setError('Quá nhiều điểm cắt.')
    setError('')
    setNotice(Math.abs(t - now) > 0.05 ? `Đã cắt tại khung hình gốc gần nhất ${clipTime(t)} (chế độ giữ nguyên 100%).` : '')
    if (t !== now) seek(t)
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
    const value = Math.max(low, Math.min(high, snap(to)))
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
      const k = snap(t)
      if (k >= MIN_LENGTH && k <= duration - MIN_LENGTH && !list.some(c=>Math.abs(c - k) < MIN_LENGTH)) list.push(k)
    }
    if (list.length + 1 > MAX_CLIPS) return setError(`Sẽ tạo ${list.length + 1} đoạn, tối đa ${MAX_CLIPS}. Hãy chọn đoạn dài hơn.`)
    setError('');setCuts(list);setDropped(new Set());setNames({})
  }
  /** Back to COPY: every cut moves to its nearest keyframe (names/kept state follow their piece). */
  const changeMode = (next:ClipMode) => {
    setMode(next)
    if (next !== 'COPY' || !keyframes?.length) return
    const moved = new Map(cuts.map(c=>[c, keyframes.reduce((best, k)=>Math.abs(k - c) < Math.abs(best - c) ? k : best, keyframes[0])]))
    const list = [...new Set(moved.values())].filter(k=>k >= MIN_LENGTH && k <= duration - MIN_LENGTH).sort((a, b)=>a - b)
    const remap = (key:string) => {const c = cuts.find(x=>keyOf(x) === key);return c == null ? key : keyOf(moved.get(c) as number)}
    setCuts(list)
    setDropped(set=>new Set([...set].map(remap)))
    setNames(map=>Object.fromEntries(Object.entries(map).map(([k, v])=>[remap(k), v])))
    if (list.length !== cuts.length || cuts.some(c=>moved.get(c) !== c)) setNotice('Các điểm cắt đã được dời về khung hình gốc gần nhất.')
  }
  const flip = (segment:Segment) => setDropped(set=>{const next = new Set(set);if (!next.delete(segment.key)) next.add(segment.key);return next})

  // ----- keyboard: Space play, S cut, ←/→ 1 s (Shift 5 s), ,/. one frame
  useEffect(()=>{
    const onKey = (event:KeyboardEvent) => {
      if (isTyping(event.target) || event.ctrlKey || event.metaKey || event.altKey) return
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

  const submit = async () => {
    setBusy(true);setError('')
    try{
      const clips = kept.map(s=>({start:Number(s.start.toFixed(3)), end:Number(s.end.toFixed(3)), name:(names[s.key] ?? '').trim()}))
      onStarted(await createClips(media.id, clips, mode, keepLinks))
    }catch(e){setError(errorText(e, 'Không bắt đầu cắt được video'))}
    finally{setBusy(false)}
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
          {snapping && <div className="mce-keys" aria-hidden="true">{keyframes?.map(k=><i key={k} style={{left:pct(k)}}/>)}</div>}
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
        <SegmentedControl<ClipMode> value={mode} onChange={changeMode} items={[
          {value:'COPY', label:'Giữ nguyên 100% chất lượng'}, {value:'PRECISE', label:'Cắt chính xác từng khung hình'}]}/>
        <Switch checked={keepLinks} onChange={e=>setKeepLinks(e.target.checked)} label="Chép sản phẩm, người, tags sang video mới"/>
        <span className="mce-mode-hint">{mode === 'COPY'
          ? keyframes == null ? 'Đang đọc vị trí khung hình gốc của video…'
            : 'Không mã hóa lại: chất lượng và cấu hình y hệt gốc. Chỉ cắt được tại các vạch vàng trên dải hình (khung hình gốc), điểm cắt tự hút vào vạch gần nhất. Muốn cắt ở chỗ bất kỳ, chọn "Cắt chính xác".'
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
