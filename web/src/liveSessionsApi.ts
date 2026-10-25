/** Managed LIVE sessions shared by the session planner (#/sessions) and the operator console (#/live). */
import { agentAction, apiRequest, onAgent, type AgentCommand } from './mediaApi'

export type SessionStatus = 'DRAFT' | 'RUNNING' | 'ENDED'
/** A LIVE session is bound to the Windows Agent it was created on; every action goes to that Agent, never another. */
export type LiveSession = {
  id:number; name:string; status:SessionStatus; agent_id:string|null; shop_id:number|null; tiktok_account_id:number|null; product_ids:number[]; current_product_id:number|null
  created_at:string; started_at:string|null; ended_at:string|null
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
}

/** Turns the backend's `key=value;…` event detail into a short Vietnamese note. */
export function eventDetail(detail:string){
  if (!detail) return ''
  const values = Object.fromEntries(detail.split(';').map(part=>part.split('=') as [string,string]))
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
  create:(name:string, accountId:number) => apiRequest<LiveSession>('/live/sessions', json({name, product_ids:[], tiktok_account_id:accountId})),
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
  resume:(session:LiveSession, productId?:number) => agentAction<SessionAction>(session.agent_id!, `/live/sessions/${session.id}/resume`, json(productId ? {product_id:productId} : {})),
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
