/** Media library API shared by the library page and the per-product LIVE video setup. */
/** A video on one of the OWNER's Windows Agents (id = the Agent's 64-hex file id; the MP4 stays on that PC). */
export type Media = { id:string; agent_id:string; filename:string; size_bytes:number; duration_seconds:number|null; available:boolean; thumb_url:string|null; tags:string; product_ids:number[]; presenter_ids:number[] }
/** A Windows Agent of the logged-in OWNER (GET /api/owner/agents), with a summary of its last heartbeat. */
export type OwnerAgent = { id:string; hostname:string; online:boolean; last_seen:string|null; obs_connected?:boolean; playing?:boolean; session_id?:number|null; monitor_running?:boolean }
export type CatalogItem = { id:number; name:string }
export type Product = CatalogItem & { sku?:string; image_url?:string }
export type Presenter = CatalogItem & { description?:string; avatar_url?:string }

import { authHeaders, clearAuth } from './authToken'

export const api = ((import.meta.env.VITE_API_URL as string|undefined) || 'http://127.0.0.1:8000').replace(/\/$/, '')
/** Absolute URL for a server-relative asset path such as an Agent thumbnail. */
export const assetUrl = (path?:string|null) => !path ? null : /^https?:/.test(path) ? path : api + path

export const sizeLabel = (bytes:number) => bytes >= 1024 ** 3 ? (bytes / 1024 ** 3).toFixed(2) + ' GB' : (bytes / 1024 ** 2).toFixed(1) + ' MB'
export const durationLabel = (seconds?:number|null) => seconds == null || !Number.isFinite(seconds) ? null : `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
export const errorText = (e:unknown, fallback:string) => e instanceof Error && e.message ? e.message : fallback

/** Calls the Backend as the logged-in user; a 401 (expired/revoked session) signs the tab out. */
export async function apiRequest<T>(url:string, options?:RequestInit):Promise<T>{
  const headers = new Headers(options?.headers)
  for (const [key, value] of Object.entries(authHeaders())) headers.set(key, value)
  const response = await fetch(api + '/api' + url, {...options, headers})
  if (response.status === 401) clearAuth()
  if (!response.ok) {
    const result = await response.json().catch(()=>({}))
    throw new Error(typeof result.detail === 'string' ? result.detail : 'HTTP ' + response.status)
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>
}

/** `path` with the tab's Agent: every Agent-scoped call names its Agent (the Backend checks it is the caller's). */
export const onAgent = (path:string, agentId:string) => path + (path.includes('?') ? '&' : '?') + 'agent_id=' + encodeURIComponent(agentId)

/** One key per user action: a retried request with the same key never runs the action twice on the Agent. */
export const actionKey = () => (globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`)

export type CommandStatus = 'PENDING'|'RUNNING'|'SUCCESS'|'FAILED'|'TIMEOUT'|'CANCELLED'
/** A command sent to a Windows Agent (lifecycle PENDING → RUNNING → SUCCESS / FAILED / TIMEOUT / CANCELLED). */
export type AgentCommand = { command_id:string; agent_id:string; kind:string; status:CommandStatus; attempts:number; result:unknown; error:string|null; late_ack:boolean; created_at:string; completed_at:string|null; expires_at:string|null }
const FINAL:CommandStatus[] = ['SUCCESS','FAILED','TIMEOUT','CANCELLED']
const FAILURE:Partial<Record<CommandStatus,string>> = {FAILED:'Agent báo lỗi khi thực hiện', TIMEOUT:'Agent không phản hồi kịp (lệnh đã hết hạn)', CANCELLED:'Lệnh đã bị hủy'}

/** Polls a command until it is final; throws with the Agent's error unless it succeeded. */
export async function waitCommand(agentId:string, commandId:string, timeoutMs = 45000):Promise<AgentCommand>{
  const deadline = Date.now() + timeoutMs
  for (;;) {
    const view = await apiRequest<AgentCommand>(`/agents/${encodeURIComponent(agentId)}/commands/${encodeURIComponent(commandId)}`)
    if (FINAL.includes(view.status)) {
      if (view.status !== 'SUCCESS') throw new Error(view.error || FAILURE[view.status])
      return view
    }
    if (Date.now() > deadline) throw new Error('Agent chưa xử lý xong lệnh; trạng thái sẽ cập nhật khi Agent phản hồi')
    await new Promise(resolve=>window.setTimeout(resolve, 900))
  }
}

/**
 * An Agent action: the Backend answers 200 when the Agent already confirmed, or 202 with the command still on its way;
 * in that case wait for it here so the caller sees the real outcome (or the Agent's error).
 */
export async function agentAction<T = unknown>(agentId:string, url:string, init?:RequestInit):Promise<T>{
  const headers = new Headers(init?.headers)
  if (!headers.has('Idempotency-Key')) headers.set('Idempotency-Key', actionKey())
  const result = await apiRequest<T & {command?:AgentCommand; command_id?:string; status?:CommandStatus}>(url, {...init, headers})
  const command = result?.command ?? (result?.command_id && result.status ? result as unknown as AgentCommand : undefined)
  if (command && !FINAL.includes(command.status)) await waitCommand(agentId, command.command_id)
  return result
}

/** Ordered LIVE playlist of a product; the array order is the playback order. */
export const getProductVideos = (agentId:string, productId:number) =>
  apiRequest<{product_id:number;media_ids:string[]}>(onAgent(`/product-videos/${productId}`, agentId)).then(result=>result.media_ids)

export const putProductVideos = (agentId:string, productId:number, mediaIds:string[]) =>
  apiRequest<{product_id:number;media_ids:string[]}>(onAgent(`/product-videos/${productId}`, agentId), {
    method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({media_ids:mediaIds}),
  })

/**
 * Replaces the products that use a video (many-to-many) in one backend transaction.
 * Newly linked products get the video appended to the end of their playlist; existing order is kept.
 */
export const setVideoProducts = (agentId:string, mediaId:string, productIds:number[]) =>
  apiRequest<Media>(onAgent(`/media/${mediaId}/products`, agentId), {
    method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({product_ids:productIds}),
  })

/** Replaces the people who appear in a video (many-to-many, unordered). */
export const setVideoPresenters = (agentId:string, mediaId:string, presenterIds:number[]) =>
  apiRequest<Media>(onAgent(`/media/${mediaId}/presenters`, agentId), {
    method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({presenter_ids:presenterIds}),
  })

/** Videos a person appears in. */
export const getPresenterVideos = (agentId:string, presenterId:number) =>
  apiRequest<{presenter_id:number;media_ids:string[]}>(onAgent(`/presenter-videos/${presenterId}`, agentId)).then(result=>result.media_ids)

export const putPresenterVideos = (agentId:string, presenterId:number, mediaIds:string[]) =>
  apiRequest<{presenter_id:number;media_ids:string[]}>(onAgent(`/presenter-videos/${presenterId}`, agentId), {
    method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({media_ids:mediaIds}),
  })

/** Videos on an Agent (metadata only; the files stay on that PC). */
export const listVideos = (agentId:string) => apiRequest<Media[]>(onAgent('/media', agentId))
