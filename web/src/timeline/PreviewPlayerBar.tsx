import type { CSSProperties } from 'react'
import { Maximize, Pause, Play, Volume2, VolumeX } from 'lucide-react'
import { hms } from '../liveSessionsApi'

/** Player controls under the 9:16 preview: play / pause, mute, seek bar, time, fullscreen. */
export default function PreviewPlayerBar({playing, muted, now, duration, disabled, onToggle, onMute, onSeek, onFullscreen}:{
  playing:boolean; muted:boolean; now:number; duration:number|null; disabled:boolean
  onToggle:()=>void; onMute:()=>void; onSeek:(ms:number)=>void; onFullscreen:()=>void
}){
  const total = duration ?? 0
  const progress = total ? Math.min(100, now / total * 100) : 0
  return <div className="ts-player">
    <button type="button" className="ts-player-btn" disabled={disabled} onClick={onToggle} aria-label={playing ? 'Tạm dừng' : 'Phát'} title={playing ? 'Tạm dừng (Space)' : 'Phát (Space)'}>
      {playing ? <Pause size={18}/> : <Play size={18}/>}</button>
    <button type="button" className="ts-player-btn" disabled={disabled} onClick={onMute} aria-label={muted ? 'Bật tiếng' : 'Tắt tiếng'} title={muted ? 'Bật tiếng' : 'Tắt tiếng'}>
      {muted ? <VolumeX size={17}/> : <Volume2 size={17}/>}</button>
    <input className="ts-player-seek" type="range" min={0} max={Math.max(1, Math.round(total))} step={100} value={Math.round(now)} disabled={disabled || !total}
      style={{'--ts-progress':`${progress}%`} as CSSProperties} aria-label="Tua video" aria-valuetext={hms(now)} onChange={e=>onSeek(Number(e.target.value))}/>
    <span className="ts-player-time"><b>{hms(now)}</b> / {hms(duration)}</span>
    <button type="button" className="ts-player-btn" disabled={disabled} onClick={onFullscreen} aria-label="Toàn màn hình" title="Toàn màn hình"><Maximize size={16}/></button>
  </div>
}
