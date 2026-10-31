import { useEffect, useRef, useState } from 'react'
import OverlayRenderer from '../OverlayRenderer'
import { safeImageUrl, type OverlayScene } from '../overlayModel'
import { OVERLAY_FONT, OVERLAY_WEIGHTS } from '../overlay-fonts'

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

type Clock = {epoch:string; session_id:number; media_id:string|null; position_ms:number; media_state:string|null; iteration:number; seq:number; age_ms:number; playing:boolean}
type AgentState = {revision:string|null; show:boolean; clock:Clock|null; receivedAt:number}

const token = new URLSearchParams(location.search).get('t') ?? ''
/** This page's build (its script file name): the Agent reloads a page left over from an older Agent version. */
const BUILD = (document.querySelector('script[type="module"]') as HTMLScriptElement|null)?.src.split('/').pop() ?? ''
const api = (path:string, extra = '') => `${path}?t=${encodeURIComponent(token)}${extra}`

/** Every image the scene can show, as the renderer will request it. */
function sceneImages(scene:OverlayScene, apiBase:string){
  const urls = [...Object.values(scene.assets).map(a=>a.url), ...Object.values(scene.products).map(p=>p.image_url)]
  return [...new Set(urls.map(u=>safeImageUrl(u, apiBase)).filter((u):u is string=>!!u))]
}

const IMAGE_TIMEOUT_MS = 8000
const FONTS_TIMEOUT_MS = 3000
const LOAD_STUCK_MS = 15000

const FETCH_TIMEOUT_MS = 5000
/** fetch that gives up: a request to an Agent that restarted must never leave the page waiting forever. */
const timedFetch = (url:string, init:RequestInit = {}) => fetch(url, {...init, cache:'no-store', signal:AbortSignal.timeout(FETCH_TIMEOUT_MS)})

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
  // The bundled font in every weight (Vietnamese and Latin glyphs) is ready before the first frame is shown.
  await within(Promise.all(OVERLAY_WEIGHTS.map(w=>document.fonts.load(`${w} 40px "${OVERLAY_FONT}"`, 'Ăâêôơư Áo đẹp 123đ'))).then(()=>true), FONTS_TIMEOUT_MS, false)
  await within(document.fonts.ready.then(()=>true), FONTS_TIMEOUT_MS, false)
  return missing
}

export default function ObsOverlayApp(){
  const [scene,setScene] = useState<{revision:string; scene:OverlayScene}|null>(null)
  const [state,setState] = useState<AgentState|null>(null)
  const loading = useRef<{revision:string; since:number}|null>(null)
  // What is on screen (revision, clock seq): sent back with every poll (ACK), and used to drop late answers.
  const drawn = useRef<{revision:string; seq:number; session:number|null; epoch:string}>({revision:'', seq:0, session:null, epoch:''})
  // Time of the last animation frame (requestAnimationFrame), so render itself stays pure.
  const [frameAt,setFrameAt] = useState(0)

  useEffect(()=>{
    let alive = true
    const poll = async () => {
      try {
        const ack = `&rev=${encodeURIComponent(drawn.current.revision)}&seq=${drawn.current.seq}&build=${encodeURIComponent(BUILD)}`
        const response = await timedFetch(api('state', ack))
        if (!response.ok) return
        const next = await response.json() as AgentState
        if (!alive) return
        // A late answer (an older clock of the same Agent run and session) never moves the picture back. After an Agent
        // restart the seqs start again at 1: another epoch, never ignored.
        const c = next.clock
        if (c && c.epoch === drawn.current.epoch && c.session_id === drawn.current.session && c.seq < drawn.current.seq) return
        setState({...next, receivedAt:performance.now()})
      } catch {/* the Agent restarts: keep polling */}
    }
    void poll()
    // The beat comes from a worker: OBS marks a hidden Browser Source as a hidden page, where Chromium slows the page's
    // own timers down to once a minute after a few minutes; a worker's timer keeps the 250 ms poll (and the READY
    // handshake of a new version) on time while the overlay is hidden before LIVE.
    const beat = new Worker(URL.createObjectURL(new Blob([`setInterval(()=>postMessage(0), ${POLL_MS})`], {type:'text/javascript'})))
    beat.onmessage = () => void poll()
    return ()=>{alive = false;beat.terminate()}
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
        const response = await timedFetch(api('scene', `&revision=${encodeURIComponent(wanted)}`))
        if (response.ok) {
          loaded = await response.json() as OverlayScene
          missing = await preload(sceneImages(loaded, location.origin))
          ok = missing.length === 0
        } else missing = [`scene HTTP ${response.status}`]
      } catch (e) {missing = [String(e)]}
      try {
        const answer = await timedFetch(api('ready'), {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({revision:wanted, ok, missing})})
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
    let id = requestAnimationFrame(function loop(t){setFrameAt(t);id = requestAnimationFrame(loop)})
    return ()=>cancelAnimationFrame(id)
  },[playing])

  const clock = state?.clock
  useEffect(()=>{
    if (clock) drawn.current = {revision:scene?.revision ?? '', seq:clock.seq, session:clock.session_id, epoch:clock.epoch}
  },[clock, scene?.revision])
  if (!state?.show || !scene || !clock?.media_id) return null
  const ahead = clock.playing ? Math.min(clock.age_ms + Math.max(0, frameAt - state.receivedAt), MAX_AHEAD_MS) : 0
  return <div style={{position:'fixed', inset:0, background:'transparent'}} data-revision={scene.revision} data-media={clock.media_id}>
    <OverlayRenderer scene={scene.scene} mediaId={clock.media_id} positionMs={clock.position_ms + Math.max(0, ahead)} apiBase={location.origin} animate/>
  </div>
}
