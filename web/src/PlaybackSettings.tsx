import { useEffect, useState } from 'react'
import { Button, Card, SegmentedControl, useToast } from './ui/core'
import { ChevronDown, ChevronUp, Shuffle, RotateCw } from 'lucide-react'
import { errorText } from './mediaApi'
import { sessionsApi, type PlaybackOrder, type PlaybackPolicy, type PolicySaved, type RotationStatus } from './liveSessionsApi'

type Rotation = PlaybackPolicy['product_rotation']
type SwitchMode = PlaybackPolicy['switch_mode']
/** Editable copy of the policy: the limits stay as typed text so a half-typed number does not jump. */
type Form = { video_order:PlaybackOrder; product_rotation:Rotation; switch_mode:SwitchMode; videosOn:boolean; videos:string; minutesOn:boolean; minutes:string }

const ORDER_LABEL:Record<PlaybackOrder,string> = {sequential:'Tuần tự', random:'Ngẫu nhiên', shuffle:'Ngẫu nhiên không lặp'}
const ORDER_HINT:Record<PlaybackOrder,string> = {
  sequential:'Phát lần lượt từ video đầu đến video cuối rồi quay lại.',
  random:'Chọn bất kỳ video nào, không phát 1 video 2 lần liền nhau.',
  shuffle:'Phát hết mọi video một lượt theo thứ tự xáo trộn, rồi xáo lại.',
}
const ROTATION_LABEL:Record<Rotation,string> = {off:'Tắt', ...ORDER_LABEL, sequential:'Theo thứ tự'}
const ROTATION_HINT:Record<Rotation,string> = {
  off:'Chỉ đổi sản phẩm khi bạn bấm chuyển.',
  sequential:'Đổi lần lượt theo danh sách sản phẩm của phiên.',
  random:'Đổi sang một sản phẩm bất kỳ khác trong phiên.',
  shuffle:'Đi qua mọi sản phẩm một lượt theo thứ tự xáo trộn, rồi xáo lại.',
}
const ORDERS = (Object.keys(ORDER_LABEL) as PlaybackOrder[]).map(value=>({value, label:ORDER_LABEL[value]}))
const ROTATIONS = (['off','sequential','random','shuffle'] as Rotation[]).map(value=>({value, label:ROTATION_LABEL[value]}))
const LIMITS = {videos:[1, 50], minutes:[1, 240]} as const

const toForm = (p:PlaybackPolicy):Form => ({
  video_order:p.video_order, product_rotation:p.product_rotation, switch_mode:p.switch_mode,
  videosOn:p.rotate_after_videos != null, videos:String(p.rotate_after_videos ?? 3),
  minutesOn:p.rotate_after_minutes != null, minutes:String(p.rotate_after_minutes ?? 10),
})
/** Whole number inside [min, max], else null. */
const parseLimit = (text:string, [min, max]:readonly [number, number]) => {
  const n = Number(text.trim())
  return text.trim() && Number.isInteger(n) && n >= min && n <= max ? n : null
}
/** The policy the form describes, or an error message for the shop staff. */
function fromForm(f:Form, auto:boolean):{policy:PlaybackPolicy}|{error:string}{
  const off = f.product_rotation === 'off'
  const videos = f.videosOn ? parseLimit(f.videos, LIMITS.videos) : null
  const minutes = f.minutesOn ? parseLimit(f.minutes, LIMITS.minutes) : null
  if (!off){
    if (!f.videosOn && !f.minutesOn) return {error:'Chọn ít nhất một mốc: số video hoặc số phút.'}
    if (f.videosOn && videos == null) return {error:'Số video phải là số nguyên từ 1 đến 50.'}
    if (f.minutesOn && minutes == null) return {error:'Số phút phải là số nguyên từ 1 đến 240.'}
  }
  return {policy:{
    video_order:f.video_order, product_rotation:f.product_rotation, switch_mode:f.switch_mode, auto,
    rotate_after_videos:off ? null : videos, rotate_after_minutes:off ? null : minutes,
  }}
}
const limitText = (p:PlaybackPolicy) => [
  p.rotate_after_videos != null ? `${p.rotate_after_videos} video` : null,
  p.rotate_after_minutes != null ? `${p.rotate_after_minutes} phút` : null,
].filter(Boolean).join(' hoặc ')
const summary = (p:PlaybackPolicy) => `Video: ${ORDER_LABEL[p.video_order]} · Tự đổi sản phẩm: ${p.product_rotation === 'off' ? 'Tắt' : `${ROTATION_LABEL[p.product_rotation]}, sau ${limitText(p)}`}`
/** Same settings, ignoring `auto` (that one is changed by the resume button, not by saving). */
const sameSettings = (a:PlaybackPolicy, b:PlaybackPolicy) => JSON.stringify({...a, auto:true}) === JSON.stringify({...b, auto:true})

/**
 * "Cài đặt phát" card of the running product session: video order and automatic product rotation
 * (docs/PLAYBACK_POLICY.md). The Agent applies them; the live status shows the rotation progress.
 */
export default function PlaybackSettings({sessionId,rotation,agentPolicy,productName}:{
  sessionId:number; rotation?:RotationStatus|null; agentPolicy?:PlaybackPolicy|null; productName:(id:number)=>string
}){
  const toast = useToast()
  const [open,setOpen] = useState(false)
  const [saved,setSaved] = useState<PlaybackPolicy|null>(null)
  const [form,setForm] = useState<Form|null>(null)
  const [busy,setBusy] = useState(false)
  const [loadError,setLoadError] = useState('')

  useEffect(()=>{
    let alive = true
    sessionsApi.playbackPolicy(sessionId)
      .then(p=>{if (alive){setSaved(p);setForm(toForm(p))}})
      .catch(e=>{if (alive) setLoadError(errorText(e, 'Không tải được cài đặt phát'))})
    return ()=>{alive = false}
  },[sessionId])

  const applied = (r:PolicySaved) => {setSaved(r.policy);setForm(toForm(r.policy))}
  // What the Agent runs right now wins for the status line; the Cloud copy until the Agent reports one.
  const rotating = (agentPolicy ?? saved)?.product_rotation !== undefined && (agentPolicy ?? saved)?.product_rotation !== 'off'
  const auto = rotation?.auto ?? saved?.auto ?? true

  const built = form ? fromForm(form, saved?.product_rotation === 'off' ? true : auto) : null
  const error = built && 'error' in built ? built.error : ''
  const draft = built && 'policy' in built ? built.policy : null
  const changed = !!draft && !!saved && !sameSettings(draft, saved)

  const save = async () => {
    if (!draft) return
    setBusy(true)
    try{
      const r = await sessionsApi.savePlaybackPolicy(sessionId, draft)
      applied(r)
      toast.push(r.sent
        ? {tone:'success', title:'Đã lưu và áp dụng ngay'}
        : {tone:r.agent_error ? 'warning' : 'info', title:'Đã lưu, sẽ áp dụng khi máy phát online', description:r.agent_error ?? undefined})
    }catch(e){toast.push({tone:'danger', title:'Không lưu được cài đặt phát', description:errorText(e, '')})}
    finally{setBusy(false)}
  }
  const resume = async () => {
    setBusy(true)
    try{
      const r = await sessionsApi.resumeAuto(sessionId)
      setSaved(r.policy)
      toast.push(r.sent
        ? {tone:'success', title:'Đã bật lại tự đổi sản phẩm'}
        : {tone:'warning', title:'Đã bật lại, sẽ áp dụng khi máy phát online', description:r.agent_error ?? undefined})
    }catch(e){toast.push({tone:'danger', title:'Không bật lại được', description:errorText(e, '')})}
    finally{setBusy(false)}
  }
  const set = <K extends keyof Form>(key:K, value:Form[K]) => setForm(f=>f ? {...f, [key]:value} : f)

  const statusLine = rotating && rotation ? (auto
    ? <div className="lc-ps-state is-on"><RotateCw size={14}/>
      <span>Đang tự đổi sản phẩm · đã phát {rotation.videos_in_product} video / {Math.floor(rotation.product_seconds / 60)} phút của sản phẩm này{rotation.next_product_id != null ? <> · tiếp theo: <b>{productName(rotation.next_product_id)}</b></> : null}</span>
    </div>
    : <div className="lc-ps-state is-paused">
      <span>Đã tạm dừng tự đổi vì bạn chuyển sản phẩm bằng tay</span>
      <Button size="sm" variant="outline" disabled={busy} onClick={()=>void resume()}>Bật lại tự đổi</Button>
    </div>) : null

  return <Card className="lc-ps" padding={false}
    title={<span className="lc-card-title"><Shuffle size={17}/>Cài đặt phát</span>}
    description={saved ? summary(saved) : loadError || 'Đang tải…'}
    actions={<Button variant="ghost" size="sm" rightIcon={open ? <ChevronUp size={15}/> : <ChevronDown size={15}/>} aria-expanded={open} onClick={()=>setOpen(o=>!o)}>{open ? 'Thu gọn' : 'Thay đổi'}</Button>}>
    {statusLine}
    {open && form && <div className="lc-ps-body">
      <div className="lc-ps-field">
        <span className="lc-ps-label">Thứ tự video</span>
        <SegmentedControl<PlaybackOrder> fullWidth value={form.video_order} onChange={v=>set('video_order', v)} items={ORDERS}/>
        <small className="lc-ps-hint">{ORDER_HINT[form.video_order]}</small>
      </div>

      <div className="lc-ps-field">
        <span className="lc-ps-label">Tự đổi sản phẩm</span>
        <SegmentedControl<Rotation> fullWidth value={form.product_rotation} onChange={v=>set('product_rotation', v)} items={ROTATIONS}/>
        <small className="lc-ps-hint">{ROTATION_HINT[form.product_rotation]}</small>
      </div>

      {form.product_rotation !== 'off' && <>
        <div className="lc-ps-field">
          <span className="lc-ps-label">Đổi sau</span>
          <div className="lc-ps-limits">
            <label className="lc-ps-limit">
              <input type="checkbox" checked={form.videosOn} onChange={e=>set('videosOn', e.target.checked)}/>
              <input type="number" min={LIMITS.videos[0]} max={LIMITS.videos[1]} step={1} value={form.videos} disabled={!form.videosOn}
                aria-label="Số video" onChange={e=>set('videos', e.target.value)}/>
              <span>video</span>
            </label>
            <label className="lc-ps-limit">
              <input type="checkbox" checked={form.minutesOn} onChange={e=>set('minutesOn', e.target.checked)}/>
              <input type="number" min={LIMITS.minutes[0]} max={LIMITS.minutes[1]} step={1} value={form.minutes} disabled={!form.minutesOn}
                aria-label="Số phút" onChange={e=>set('minutes', e.target.value)}/>
              <span>phút</span>
            </label>
          </div>
          {error ? <small className="lc-ps-error" role="alert">{error}</small>
            : <small className="lc-ps-hint">Chọn cả hai thì mốc nào đến trước sẽ đổi trước.</small>}
        </div>

        <div className="lc-ps-field">
          <span className="lc-ps-label">Khi đến lúc đổi</span>
          <SegmentedControl<SwitchMode> fullWidth value={form.switch_mode} onChange={v=>set('switch_mode', v)}
            items={[{value:'after_current', label:'Chờ hết video đang phát'}, {value:'immediate', label:'Chuyển ngay'}]}/>
          <small className="lc-ps-hint">Áp dụng cho mốc số phút. Mốc số video luôn đổi khi hết video.</small>
        </div>
      </>}

      <div className="lc-ps-actions">
        {changed && <Button variant="ghost" disabled={busy} onClick={()=>saved && setForm(toForm(saved))}>Hoàn tác</Button>}
        <Button disabled={busy || !changed || !draft} onClick={()=>void save()}>Lưu cài đặt phát</Button>
      </div>
    </div>}
  </Card>
}
