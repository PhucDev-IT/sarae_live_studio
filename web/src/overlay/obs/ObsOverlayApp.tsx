import { useEffect, useRef, useState } from 'react'
import OverlayRenderer from '../OverlayRenderer'
import { safeImageUrl, type OverlayScene } from '../overlayModel'

/**
 * The OBS Browser Source page ("Sarae Overlay"), served by the Windows Agent on 127.0.0.1 with everything it needs
 * (no Internet during LIVE). No editor UI: it draws the shared OverlayRenderer on a transparent 1080×1920 page.
 *
 * Protocol with the Agent (agent-core/src/sarae_agent/overlay/server.py):
 * - poll `state` (revision to show, `show`, playback clock) every POLL_MS;
 * - a new revision: fetch `scene`, preload every image (decode) and the fonts, then POST `ready` {revision, ok, missing};
 *   the old scene stays drawn until the Agent accepts the new one;
 * - time is the PlaybackEngine's position: between two reports it may advance only while PLAYING, by at most
 *   MAX_AHEAD_MS (a paused video holds its frame; a stale clock never runs on).
 */
const POLL_MS = 250
const MAX_AHEAD_MS = 1500

type Clock = {session_id:number; media_id:string|null; position_ms:number; media_state:string|null; iteration:number; seq:number; age_ms:number; playing:boolean}
type AgentState = {revision:string|null; show:boolean; clock:Clock|null}

const token = new URLSearchParams(location.search).get('t') ?? ''
const api = (path:string, extra = '') => `${path}?t=${encodeURIComponent(token)}${extra}`

/** Every image the scene can show, as the renderer will request it. */
export function sceneImages(scene:OverlayScene, apiBase:string){
  const urls = [...Object.values(scene.assets).map(a=>a.url), ...Object.values(scene.products).map(p=>p.image_url)]
  return [...new Set(urls.map(u=>safeImageUrl(u, apiBase)).filter((u):u is string=>!!u))]
}

const IMAGE_TIMEOUT_MS = 8000
const FONTS_TIMEOUT_MS = 3000
const LOAD_STUCK_MS = 15000

const within = <T,>(promise:Promise<T>, ms:number, fallback:T) => Promise.race([promise, new Promise<T>(resolve=>window.setTimeout(()=>resolve(fallback), ms))])

/** Load every image fully (onload, not decode(): OBS keeps a hidden Browser Source's page in the background, where
 * decode() may wait until it is shown). Each image has a time limit; one that fails or times out is reported. */
async function preload(urls:string[]){
  const missing:string[] = []
  await Promise.all(urls.map(async url=>{
    const loaded = await within(new Promise<boolean>(resolve=>{
      const img = new Image()
      img.crossOrigin = 'anonymous'
      img.onload = () => resolve(img.naturalWidth > 0)
      img.onerror = () => resolve(false)
      img.src = url
    }), IMAGE_TIMEOUT_MS, false)
    if (!loaded) missing.push(url.replace(/\?.*$/, ''))
  }))
  await within(document.fonts.ready.then(()=>true), FONTS_TIMEOUT_MS, false)
  return missing
}

export default function ObsOverlayApp(){
  const [scene,setScene] = useState<{revision:string; scene:OverlayScene}|null>(null)
  const [state,setState] = useState<AgentState|null>(null)
  const received = useRef(0)
  const loading = useRef<{revision:string; since:number}|null>(null)
  const [, setFrame] = useState(0)

  useEffect(()=>{
    let alive = true
    const poll = async () => {
      try {
        const response = await fetch(api('state'), {cache:'no-store'})
        if (!response.ok) return
        const next = await response.json() as AgentState
        if (!alive) return
        received.current = performance.now()
        setState(next)
      } catch {/* the Agent restarts: keep polling */}
    }
    void poll()
    const timer = window.setInterval(()=>void poll(), POLL_MS)
    return ()=>{alive = false;window.clearInterval(timer)}
  },[])

  // A revision the Agent wants and we do not draw yet: load, preload, report.
  const wanted = state?.revision ?? null
  useEffect(()=>{
    if (!wanted || wanted === scene?.revision) return
    // One load at a time; a load stuck for LOAD_STUCK_MS is abandoned (the Agent re-offers the revision).
    if (loading.current && performance.now() - loading.current.since < LOAD_STUCK_MS) return
    const attempt = {revision:wanted, since:performance.now()}
    loading.current = attempt
    void (async ()=>{
      let ok = false, missing:string[] = []
      let loaded:OverlayScene|null = null
      try {
        const response = await fetch(api('scene', `&revision=${encodeURIComponent(wanted)}`), {cache:'no-store'})
        if (response.ok) {
          loaded = await response.json() as OverlayScene
          missing = await preload(sceneImages(loaded, location.origin))
          ok = missing.length === 0
        } else missing = [`scene HTTP ${response.status}`]
      } catch (e) {missing = [String(e)]}
      try {
        const answer = await fetch(api('ready'), {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({revision:wanted, ok, missing})})
        const accepted = answer.ok && (await answer.json()).accepted === true
        if (accepted && loaded && loading.current === attempt) setScene({revision:wanted, scene:loaded})
      } catch {/* the Agent is restarting: it offers the revision again */}
      finally {if (loading.current === attempt) loading.current = null}
    })()
  },[wanted, scene?.revision, state])

  // Repaint every frame while the video plays (animations); a paused or stale clock does not move.
  const playing = !!state?.clock?.playing && !!state.show
  useEffect(()=>{
    if (!playing) return
    let id = requestAnimationFrame(function loop(){setFrame(f=>f + 1);id = requestAnimationFrame(loop)})
    return ()=>cancelAnimationFrame(id)
  },[playing])

  const clock = state?.clock
  if (!state?.show || !scene || !clock?.media_id) return null
  const ahead = clock.playing ? Math.min(clock.age_ms + performance.now() - received.current, MAX_AHEAD_MS) : 0
  return <div style={{position:'fixed', inset:0, background:'transparent'}} data-revision={scene.revision} data-media={clock.media_id}>
    <OverlayRenderer scene={scene.scene} mediaId={clock.media_id} positionMs={clock.position_ms + Math.max(0, ahead)} apiBase={location.origin} animate/>
  </div>
}
