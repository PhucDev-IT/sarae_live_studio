/** Managed LIVE sessions shared by the session planner (#/sessions) and the operator console (#/live). */
import { agentAction, apiRequest, onAgent, type AgentCommand } from './mediaApi'

export type SessionStatus = 'DRAFT' | 'RUNNING' | 'ENDED'
/**
 * SINGLE_VIDEO_TIMELINE: one main video plays continuously and products are pinned at its timestamps (default for new
 * sessions). ADVANCED_LEGACY: the product lineup / video queue / rotation of older sessions.
 */
export type PlaybackMode = 'SINGLE_VIDEO_TIMELINE' | 'ADVANCED_LEGACY'
/** A LIVE session is bound to the Windows Agent it was created on; every action goes to that Agent, never another. */
export type LiveSession = {
  id:number; name:string; status:SessionStatus; agent_id:string|null; shop_id:number|null; tiktok_account_id:number|null; product_ids:number[]; current_product_id:number|null
  playback_mode:PlaybackMode; main_media_id:string|null; video_loop:boolean; active_timeline_id:number|null
  created_at:string; started_at:string|null; ended_at:string|null
}
export const isSingle = (s?:Pick<LiveSession,'playback_mode'>|null) => s?.playback_mode === 'SINGLE_VIDEO_TIMELINE'
export const MODE_META:Record<PlaybackMode,{label:string;hint:string}> = {
  SINGLE_VIDEO_TIMELINE:{label:'Một video + lịch ghim', hint:'Phát liên tục một video chính, tự ghim sản phẩm theo mốc thời gian của video.'},
  ADVANCED_LEGACY:{label:'Nâng cao (nhiều video)', hint:'Danh sách sản phẩm, hàng đợi nhiều video, chuyển sản phẩm và xoay vòng như trước.'},
}
export type SessionEvent = { id:number; event_type:string; product_id:number|null; detail:string; created_at:string }
/** Catalog product with the number of videos linked for LIVE (GET /live/products). */
export type LiveProduct = { id:number; shop_id:number|null; tiktok_product_id?:string|null; name:string; sku:string; image_url?:string; status?:'ACTIVE'|'INACTIVE'; price?:number|null; video_count:number }

export const STATUS_META:Record<SessionStatus,{label:string;tone:'default'|'danger'|'primary'}> = {
  DRAFT:{label:'Chuẩn bị', tone:'primary'},
  // Sarae's own playback state; the actual TikTok LIVE state is tracked separately (it can differ).
  RUNNING:{label:'Sarae đang phát', tone:'danger'},
  ENDED:{label:'Đã kết thúc', tone:'default'},
}

export const EVENT_LABEL:Record<string,string> = {
  CREATED:'Tạo phiên',
  QUEUE_UPDATED:'Cập nhật danh sách sản phẩm',
  PRODUCT_ADDED:'Thêm sản phẩm khi đang LIVE',
  STARTED:'Sarae bắt đầu phát',
  SWITCH_QUEUED:'Hẹn chuyển sau video hiện tại',
  SWITCH_CANCELLED:'Hủy hẹn chuyển',
  PRODUCT_SWITCHED:'Chuyển sản phẩm',
  ENDED:'Kết thúc phiên',
  RESUMED:'Tiếp tục phát sau khi bị ngắt',
  RECONCILED:'Đồng bộ lại với Agent',
  AGENT_CHANGED:'Đổi Windows Agent',
  START_FAILED:'Bắt đầu thất bại',
  START_TIMEOUT:'Agent không phản hồi khi bắt đầu',
  SWITCH_FAILED:'Chuyển sản phẩm thất bại',
  SWITCH_TIMEOUT:'Agent không phản hồi khi chuyển',
  RESUME_FAILED:'Tiếp tục thất bại',
  RESUME_TIMEOUT:'Agent không phản hồi khi tiếp tục',
  RECONCILE_STOP:'Dừng phát thừa trên Agent',
  PLAYBACK_ENDED:'Agent dừng phát',
  ERROR:'Lỗi trên Agent',
  ACCOUNT_CHANGED:'Đổi TikTok Account',
  AGENT_STOP_CONFIRMED:'Agent xác nhận đã dừng phát',
  AGENT_STOP_UNCONFIRMED:'Chưa xác nhận Agent đã dừng',
  CLAIM_RELEASED:'Nhả khóa tài khoản / Agent',
  START_ABANDONED:'Hủy lần bắt đầu chưa rõ kết quả',
  AUTO_PAUSED:'Tạm dừng tự đổi sản phẩm',
  AUTO_RESUMED:'Bật lại tự đổi sản phẩm',
  MODE_CHANGED:'Đổi chế độ phát',
  MAIN_VIDEO_CHANGED:'Đổi video chính',
  TIMELINE_CREATED:'Tạo bản nháp timeline',
  TIMELINE_APPROVED:'Duyệt timeline',
  TIMELINE_ACTIVATED:'Kích hoạt timeline',
  TIMELINE_DEACTIVATED:'Gỡ timeline (đã đổi video)',
  TIMELINE_LOADED:'Agent nạp timeline',
  TIMELINE_STOPPED:'Agent dừng timeline',
  TIMELINE_RESUMED:'Tiếp tục ghim theo timeline',
  TIMELINE_RESUME_REQUESTED:'Yêu cầu tiếp tục timeline',
  TIMELINE_RECONCILED:'Gửi lại timeline cho Agent',
  PIN_APPLIED:'Đã ghim theo timeline',
  PIN_FAILED:'Ghim theo timeline thất bại',
  MANUAL_OVERRIDE:'Ghim thủ công · tạm giữ timeline',
  PIN_DRIFT:'Ghim bị đổi ngoài Sarae · ghim lại theo lịch',
  LIVE_GATE_CHANGED:'Trạng thái LIVE trên Console',
}

/** Turns the backend's `key=value;…` event detail into a short Vietnamese note. */
export function eventDetail(detail:string){
  if (!detail) return ''
  const values = Object.fromEntries(detail.split(';').map(part=>part.split('=') as [string,string]))
  if (values.action && values.marker_id != null) return `${values.action === 'UNPIN_PRODUCT' ? 'Bỏ ghim' : 'Ghim'} · mốc ${hms(Number(values.position_ms ?? 0))}${values.dry_run === 'True' ? ' · thử (dry-run)' : ''}`
  if (values.final != null) return `${values.error ?? ''}${values.final === 'True' ? ' · đã ngừng thử lại' : ''}`
  if (values.state && detail.startsWith('state=')) return values.state === 'LIVE' ? 'Đã xác nhận đang LIVE' : values.state === 'OFFLINE' ? 'Chưa LIVE' : 'Chưa xác định'
  if (values.policy) return values.policy === 'RESUME_AT_NEXT_MARKER' ? 'Tự quay lại ở mốc kế tiếp' : 'Giữ đến khi bấm Tiếp tục timeline'
  if (values.media) return ''  // MAIN_VIDEO_CHANGED: the label says it, the id means nothing to a person
  if (values.mode) return MODE_META[values.mode as PlaybackMode]?.label ?? values.mode
  if (values.version && !values.agent) return `Phiên bản ${values.version}`
  if (values.code === 'video_skipped' || detail.includes('video_skipped')) return 'Bỏ qua video lỗi' + (values.message ? `: ${values.message}` : '')
  if (values.trigger === 'auto_rotation') return 'Tự đổi sản phẩm'
  if (values.during_live) return 'Đưa lên LIVE ngoài danh sách chuẩn bị'
  if (values.stale) return 'Đóng phiên bị treo sau khi Backend khởi động lại'
  if (values.duplicate_of) return `Nhân bản từ phiên #${values.duplicate_of}`
  if (values.agent) return 'Phiên được chuyển sang máy Windows khác'
  if (values.command) return values.reason ?? values.error ?? ''
  if (values.transitions != null) return `${values.transitions} lần chuyển video · ${values.errors ?? 0} lỗi · ${values.recoveries ?? 0} lần phục hồi`
  return detail
}

/** Backend timestamps are naive UTC. */
export const toDate = (value:string|null) => value ? new Date(value.endsWith('Z') ? value : value + 'Z') : null
export const dateTime = (value:string|null) => toDate(value)?.toLocaleString('vi-VN', {hour:'2-digit', minute:'2-digit', day:'2-digit', month:'2-digit', year:'numeric'}) ?? '—'
export const timeOnly = (value:string|null) => toDate(value)?.toLocaleTimeString('vi-VN', {hour:'2-digit', minute:'2-digit', second:'2-digit'}) ?? '—'
export function durationText(from:string|null, to:string|null){
  const start = toDate(from), end = to ? toDate(to) : from ? new Date() : null
  if (!start || !end) return '—'
  const minutes = Math.max(0, Math.round((end.getTime() - start.getTime()) / 60000))
  return minutes >= 60 ? `${Math.floor(minutes / 60)} giờ ${minutes % 60} phút` : `${minutes} phút`
}

const json = (data:unknown, method = 'POST'):RequestInit => ({method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)})
/** Video order and automatic product rotation of a LIVE session (docs/PLAYBACK_POLICY.md). */
export type PlaybackOrder = 'sequential' | 'random' | 'shuffle'
export type PlaybackPolicy = {
  video_order:PlaybackOrder; product_rotation:'off' | PlaybackOrder
  rotate_after_videos:number|null; rotate_after_minutes:number|null
  switch_mode:'after_current' | 'immediate'; auto:boolean
}
/** Rotation progress reported by the Agent with the playback status. */
export type RotationStatus = { auto:boolean; videos_in_product:number; product_seconds:number; next_product_id:number|null }
/** Answer of saving / resuming a policy: `sent` when the running Agent got it right away. */
export type PolicySaved = { policy:PlaybackPolicy; sent:boolean; agent_error:string|null }

/** Answer of start / resume / switch: the Agent command and the session after it. */
export type SessionAction = { command?:AgentCommand; session?:LiveSession; unchanged?:boolean }
export const sessionsApi = {
  list:() => apiRequest<LiveSession[]>('/live/sessions'),
  events:(id:number) => apiRequest<SessionEvent[]>(`/live/sessions/${id}/events`),
  create:(name:string, accountId:number, mode:PlaybackMode = 'SINGLE_VIDEO_TIMELINE', mainMediaId?:string|null) =>
    apiRequest<LiveSession>('/live/sessions', json({name, product_ids:[], tiktok_account_id:accountId, playback_mode:mode, ...(mainMediaId ? {main_media_id:mainMediaId} : {})})),
  /** DRAFT only: mode and main video; loop can change during LIVE. */
  update:(id:number, body:Partial<Pick<LiveSession,'playback_mode'|'main_media_id'|'video_loop'>>) => apiRequest<LiveSession>(`/live/sessions/${id}`, json(body, 'PATCH')),
  rename:(id:number, name:string) => apiRequest<LiveSession>(`/live/sessions/${id}`, json({name}, 'PATCH')),
  remove:(id:number) => apiRequest<void>(`/live/sessions/${id}`, {method:'DELETE'}),
  duplicate:(id:number) => apiRequest<LiveSession>(`/live/sessions/${id}/duplicate`, {method:'POST'}),
  saveProducts:(id:number, productIds:number[]) => apiRequest<LiveSession>(`/live/sessions/${id}/products`, json({product_ids:productIds}, 'PUT')),
  prepare:(session:LiveSession, agentId:string) =>
    agentAction(agentId, `/live/sessions/${session.id}/prepare`, json({agent_id:agentId})),
  // Sent to the session's own Agent; resolved once the Agent confirmed (or rejected with its error).
  start:(session:LiveSession, agentId:string, productId?:number) => agentAction<SessionAction>(agentId, `/live/sessions/${session.id}/start`, json({agent_id:agentId, ...(productId ? {product_id:productId} : {})})),
  switchTo:(session:LiveSession, productId:number, mode:'immediate'|'after_current') => agentAction<SessionAction>(session.agent_id!, `/live/sessions/${session.id}/switch`, json({product_id:productId, mode})),
  /** Plays a RUNNING session again on its Agent after playback was lost (Agent or OBS restarted). */
  resume:(session:LiveSession, productId?:number, positionMs?:number) => agentAction<SessionAction>(session.agent_id!, `/live/sessions/${session.id}/resume`,
    json(isSingle(session) ? {position_ms:Math.max(0, Math.round(positionMs ?? 0))} : productId ? {product_id:productId} : {})),
  finish:(id:number) => apiRequest<LiveSession>(`/live/sessions/${id}/finish`, {method:'POST'}),
  /** The owner's products with how many LIVE videos each has on that Agent. */
  products:(agentId:string) => apiRequest<LiveProduct[]>(onAgent('/live/products', agentId)),
  playbackPolicy:(id:number) => apiRequest<{policy:PlaybackPolicy}>(`/live/sessions/${id}/playback-policy`).then(r=>r.policy),
  savePlaybackPolicy:(id:number, policy:PlaybackPolicy) => apiRequest<PolicySaved>(`/live/sessions/${id}/playback-policy`, json(policy, 'PUT')),
  /** Turns automatic rotation back on after a manual product switch paused it. */
  resumeAuto:(id:number) => apiRequest<PolicySaved>(`/live/sessions/${id}/playback-policy/resume`, {method:'POST'}),
}

/** Hash route helpers: `#/sessions?id=3` opens a session in the planner. */
export const sessionHref = (id?:number) => id == null ? '#/sessions' : `#/sessions?id=${id}`
export const sessionIdFromHash = () => {
  const id = Number(new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('id'))
  return Number.isInteger(id) && id > 0 ? id : null
}

/** HH:MM:SS of a position in milliseconds (marker times are whole seconds in the editor). */
export function hms(ms?:number|null){
  if (ms == null || !Number.isFinite(ms) || ms < 0) return '--:--:--'
  const total = Math.floor(ms / 1000)
  return [Math.floor(total / 3600), Math.floor(total % 3600 / 60), total % 60].map(n=>String(n).padStart(2, '0')).join(':')
}
/** "1:02:03", "02:03" or "123" (seconds) → milliseconds; null when it is not a valid time. */
export function parseHms(text:string){
  const parts = text.trim().split(':')
  if (!parts.length || parts.length > 3 || parts.some(p=>!/^\d{1,3}$/.test(p))) return null
  const [h, m, sec] = [0, 0, 0, ...parts.map(Number)].slice(-3)
  if (parts.length > 1 && (sec > 59 || (parts.length === 3 && m > 59))) return null
  return ((h * 60 + m) * 60 + sec) * 1000
}

export type MarkerAction = 'PIN_PRODUCT' | 'UNPIN_PRODUCT' | 'NO_ACTION'
export type ReviewStatus = 'APPROVED' | 'NEEDS_REVIEW' | 'REJECTED'
export type TimelineMarker = {
  id?:number; at_ms:number; action:MarkerAction; product_id:number|null; seq?:number; label:string
  source:'MANUAL'|'AI'; review_status:ReviewStatus; confidence:number|null; evidence:string|null
}
export type TimelineStatus = 'DRAFT' | 'APPROVED' | 'ACTIVE' | 'ARCHIVED'
export type TimelineIssue = { level:'error'|'warning'; code:string; message:string; marker?:number }
export type Timeline = {
  id:number; session_id:number; version:number; status:TimelineStatus; media_id:string; duration_ms:number|null; note:string
  marker_count:number; created_at:string; updated_at:string; approved_at:string|null; activated_at:string|null
  markers?:TimelineMarker[]; issues?:TimelineIssue[]; sent?:boolean; agent_error?:string|null
}
export const TIMELINE_STATUS:Record<TimelineStatus,{label:string;tone:'default'|'primary'|'success'|'warning'}> = {
  DRAFT:{label:'Bản nháp', tone:'warning'}, APPROVED:{label:'Đã duyệt', tone:'primary'}, ACTIVE:{label:'Đang dùng', tone:'success'}, ARCHIVED:{label:'Lưu trữ', tone:'default'},
}
export const ACTION_LABEL:Record<MarkerAction,string> = {PIN_PRODUCT:'Ghim sản phẩm', UNPIN_PRODUCT:'Bỏ ghim', NO_ACTION:'Ghi chú (không ghim)'}

/** Wanted vs actual pin of a running Single Video session, as its Agent last reported. */
export type PinStateView = {
  session_id:number; status:SessionStatus; active_version:number|null; agent_online:boolean; reported:boolean
  position_ms:number|null; media_duration_ms:number|null; iteration:number|null; agent_version:number|null
  desired_product_id:number|null; desired_product_name:string|null; actual_product_id:number|null; actual_product_name:string|null
  actual_known:boolean|null; actual_tiktok_product_id:string|null
  live:boolean|null; in_flight:{action:string;product_id:number|null;attempt:number}|null
  last_applied:{action:string;product_id:number|null;position_ms:number|null}|null; last_error:string|null; dry_run:boolean|null
}

const timelinePath = (sessionId:number, rest = '') => `/live/sessions/${sessionId}/timelines${rest}`
export const timelinesApi = {
  list:(sessionId:number) => apiRequest<Timeline[]>(timelinePath(sessionId)),
  get:(sessionId:number, id:number) => apiRequest<Timeline>(timelinePath(sessionId, `/${id}`)),
  create:(sessionId:number, copyFrom?:number|null) => apiRequest<Timeline>(timelinePath(sessionId), json(copyFrom ? {copy_from:copyFrom} : {})),
  saveMarkers:(sessionId:number, id:number, markers:TimelineMarker[]) =>
    apiRequest<Timeline>(timelinePath(sessionId, `/${id}/markers`), json({markers:markers.map(({id:_id, seq:_seq, ...m})=>m)}, 'PUT')),
  approve:(sessionId:number, id:number) => apiRequest<Timeline>(timelinePath(sessionId, `/${id}/approve`), {method:'POST'}),
  activate:(sessionId:number, id:number) => apiRequest<Timeline>(timelinePath(sessionId, `/${id}/activate`), {method:'POST'}),
  remove:(sessionId:number, id:number) => apiRequest<void>(timelinePath(sessionId, `/${id}`), {method:'DELETE'}),
  pinState:(sessionId:number) => apiRequest<PinStateView>(`/live/sessions/${sessionId}/pin-state`),
}
/** `#/timeline?session=3` opens the pin timeline editor of a Single Video session. */
export const timelineHref = (sessionId:number) => `#/timeline?session=${sessionId}`
