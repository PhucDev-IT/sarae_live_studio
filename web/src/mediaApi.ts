/** Shared Backend client helpers (auth, errors, Agent commands) and common catalog types. */
/** A Windows Agent of the logged-in OWNER (GET /api/owner/agents), with a summary of its last heartbeat. */
export type OwnerAgent = { id:string; hostname:string; platform?:'WINDOWS'|'ANDROID'; online:boolean; last_seen:string|null; obs_connected?:boolean; playing?:boolean; playback_state?:string|null; playback_media_id?:string|null; playback_position_ms?:number|null; playback_duration_ms?:number|null; playback_error?:string|null; mock_live?:boolean; session_id?:number|null; monitor_running?:boolean }
export type CatalogItem = { id:number; name:string }
export type Product = CatalogItem & { sku?:string; image_url?:string }
export type Presenter = CatalogItem & { description?:string; avatar_url?:string }

import { authHeaders, clearAuth } from './authToken'

export const api = ((import.meta.env.VITE_API_URL as string|undefined) || 'http://127.0.0.1:8000').replace(/\/$/, '')
/** Absolute URL for a server-relative asset path such as an Agent thumbnail. */
export const assetUrl = (path?:string|null) => !path ? null : /^(https?|data|blob):/.test(path) ? path : api + path

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
    // FastAPI validation errors (422) come as a list: keep their messages and where they apply.
    const detail = Array.isArray(result.detail) ? result.detail.slice(0, 3).map((d:{msg?:string;loc?:unknown[]})=>`${d.msg ?? ''}${d.loc ? ` (${d.loc.slice(1).join('.')})` : ''}`).join('; ') : result.detail
    throw new Error(typeof detail === 'string' && detail ? detail : 'HTTP ' + response.status)
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
