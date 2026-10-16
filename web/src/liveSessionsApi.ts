/** Managed LIVE sessions shared by the session planner (#/sessions) and the operator console (#/live). */
import { apiRequest } from './mediaApi'

export type SessionStatus = 'DRAFT' | 'RUNNING' | 'ENDED'
export type LiveSession = {
  id:number; name:string; status:SessionStatus; product_ids:number[]; current_product_id:number|null
  created_at:string; started_at:string|null; ended_at:string|null
}
export type SessionEvent = { id:number; event_type:string; product_id:number|null; detail:string; created_at:string }
/** Catalog product with the number of videos linked for LIVE (GET /live/products). */
export type LiveProduct = { id:number; name:string; sku:string; image_url?:string; video_count:number }

export const STATUS_META:Record<SessionStatus,{label:string;tone:'default'|'danger'|'primary'}> = {
  DRAFT:{label:'Chuẩn bị', tone:'primary'},
  RUNNING:{label:'Đang LIVE', tone:'danger'},
  ENDED:{label:'Đã kết thúc', tone:'default'},
}

export const EVENT_LABEL:Record<string,string> = {
  CREATED:'Tạo phiên',
  QUEUE_UPDATED:'Cập nhật danh sách sản phẩm',
  PRODUCT_ADDED:'Thêm sản phẩm khi đang LIVE',
  STARTED:'Bắt đầu LIVE',
  SWITCH_QUEUED:'Hẹn chuyển sau video hiện tại',
  SWITCH_CANCELLED:'Hủy hẹn chuyển',
  PRODUCT_SWITCHED:'Chuyển sản phẩm',
  ENDED:'Kết thúc phiên',
  RESUMED:'Tiếp tục phát sau khi bị ngắt',
}

/** Turns the backend's `key=value;…` event detail into a short Vietnamese note. */
export function eventDetail(detail:string){
  if (!detail) return ''
  const values = Object.fromEntries(detail.split(';').map(part=>part.split('=') as [string,string]))
  if (values.during_live) return 'Đưa lên LIVE ngoài danh sách chuẩn bị'
  if (values.stale) return 'Đóng phiên bị treo sau khi Backend khởi động lại'
  if (values.duplicate_of) return `Nhân bản từ phiên #${values.duplicate_of}`
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
export const sessionsApi = {
  list:() => apiRequest<LiveSession[]>('/live/sessions'),
  events:(id:number) => apiRequest<SessionEvent[]>(`/live/sessions/${id}/events`),
  create:(name:string) => apiRequest<LiveSession>('/live/sessions', json({name, product_ids:[]})),
  rename:(id:number, name:string) => apiRequest<LiveSession>(`/live/sessions/${id}`, json({name}, 'PATCH')),
  remove:(id:number) => apiRequest<void>(`/live/sessions/${id}`, {method:'DELETE'}),
  duplicate:(id:number) => apiRequest<LiveSession>(`/live/sessions/${id}/duplicate`, {method:'POST'}),
  saveProducts:(id:number, productIds:number[]) => apiRequest<LiveSession>(`/live/sessions/${id}/products`, json({product_ids:productIds}, 'PUT')),
  start:(id:number, productId?:number) => apiRequest(`/live/sessions/${id}/start`, json(productId ? {product_id:productId} : {})),
  switchTo:(id:number, productId:number, mode:'immediate'|'after_current') => apiRequest(`/live/sessions/${id}/switch`, json({product_id:productId, mode})),
  /** Re-attaches OBS to a RUNNING session whose playback was lost when the Backend restarted. */
  resume:(id:number, productId?:number) => apiRequest(`/live/sessions/${id}/resume`, json(productId ? {product_id:productId} : {})),
  finish:(id:number) => apiRequest<LiveSession>(`/live/sessions/${id}/finish`, {method:'POST'}),
  products:() => apiRequest<LiveProduct[]>('/live/products'),
}

/** Hash route helpers: `#/sessions?id=3` opens a session in the planner. */
export const sessionHref = (id?:number) => id == null ? '#/sessions' : `#/sessions?id=${id}`
export const sessionIdFromHash = () => {
  const id = Number(new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('id'))
  return Number.isInteger(id) && id > 0 ? id : null
}
