// One contract for native IPC and the browser's loopback development adapter.
import { invoke, isTauri } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'

export interface Product {
  id: string
  name: string
  description: string
  price: number | null
  origin_price: number | null
  stock_num: number | null
  category: string
  image_url: string
  thumbnail_url: string | null
  source: 'tiktok' | 'manual'
  tiktok_seller_id: string | null
  tiktok_product_id: string | null
  last_synced_at: string | null
  facts: ProductFacts
  created_at: string
  updated_at: string
}

export interface SizeRow { size: string; weight_min: number | null; weight_max: number | null; height_min: number | null; height_max: number | null; note: string }
export interface ProductFacts { sizes: SizeRow[]; colors: string[]; material: string; shipping: string; notes: string; extras: Record<string, string> }

export type AIProvider = 'gemini' | 'openai_compatible' | 'seller_brain'
export interface AIConfig { provider: AIProvider; model: string; base_url: string; enabled: boolean; max_output_tokens: number; temperature: number }
export interface AIState {
  config: AIConfig
  keys: Partial<Record<AIProvider, { saved: boolean; last4: string | null }>>
  usage: { requests: number; input_tokens: number; output_tokens: number; errors: number }
  providers: Record<AIProvider, { needs_key: boolean; default_model: string; default_base_url: string }>
}
export interface AIUsage { provider: AIProvider; model: string; input_tokens: number; output_tokens: number; latency_ms: number }
export interface AIReply { intent: string; reply: string; needs_review: boolean; reason: string; product_id: string | null; usage: AIUsage }
export interface SalesScript { id: string; product_id: string; title: string; body: string; source: 'ai' | 'manual'; created_at: string; updated_at: string }
export type ScriptStyle = 'friendly' | 'energetic' | 'expert'

export interface TikTokAccount {
  user_id: string
  nick_name: string
  user_name: string
  avatar_url: string
  product_count: number
  last_synced_at: string
}

export interface PlaybackPolicy {
  video_order: 'sequential' | 'random' | 'shuffle'
  product_rotation: 'off' | 'sequential' | 'random' | 'shuffle'
  rotate_after_videos: number | null
  rotate_after_minutes: number | null
  switch_mode: 'after_current' | 'immediate'
  auto: boolean
}
export interface LiveSession {
  id: string
  title: string
  status: 'draft' | 'live' | 'ended'
  policy: PlaybackPolicy
  product_ids: string[]
  end_reason: string | null
  started_at: string | null
  ended_at: string | null
  created_at: string
  updated_at: string
}
export interface LiveEvent { id: number; kind: string; product_id: string | null; detail: Record<string, unknown>; created_at: string }
export interface LiveStatus {
  active: boolean
  busy: boolean
  paused: boolean
  session: LiveSession | null
  product_id: string | null
  pending_product_id: string | null
  media_id: string | null
  queue: string[]
  index: number
  media_state: string | null
  cursor_ms: number | null
  duration_ms: number | null
  obs_connected: boolean
  obs_error: string | null
  last_status: string
  rotation: { auto: boolean; videos_in_product: number; product_seconds: number; next_product_id: string | null } | null
  session_seconds: number
  errors: number
  recoveries: number
  events: LiveEvent[]
}
export interface ObsStatus {
  connected: boolean
  state: string
  error: { code: string; message: string } | null
  obs_version: string | null
  host: string
  port: number
  media_input: string
  voice_input: string
  password_set: boolean
  auto_connect: boolean
  media_input_ok?: boolean | null
  dual_mode: boolean
  source_a: string
  source_b: string
  dual_sources: string[]
}
export interface ObsSettingsInput { host?: string; port?: number; password?: string; clear_password?: boolean; media_input?: string; dual_mode?: boolean; source_a?: string; source_b?: string }

export interface ReplySettings {
  auto_voice: boolean; auto_comment: boolean; confirm_seconds: number; voice: string; rate: number
  call_name: boolean; duck_video: boolean; answer_all: boolean; max_age_seconds: number
  comment_min_interval_seconds: number; comment_max_per_minute: number; ignored_usernames: string[]
}
export type ReplyStage = 'ignored' | 'skipped' | 'queued' | 'thinking' | 'ready' | 'review' | 'done' | 'expired' | 'failed' | 'dismissed'
export interface ReplyEntry {
  id: number; msg_id: string; username: string; nickname: string; user_id: string; comment: string; simulated: boolean
  created_at: string; stage: ReplyStage; reason: string; product_id: string | null; intent: string | null; reply: string
  needs_review: boolean; voice: { status: string; detail: string } | null
  comment_delivery: { status: string; mode?: string | null; error: string | null } | null
  auto: ('voice' | 'comment')[]; auto_in: number | null; age_seconds: number
}
export interface ReplyDeskStatus {
  settings: ReplySettings
  chat: { username: string; status: 'idle' | 'connecting' | 'connected' | 'disconnected' | 'stopping' | 'error'; error: string }
  entries: ReplyEntry[]
  stats: Record<string, number>
  queued: number
  voice: Record<string, unknown> | null
  voices: string[]
  account_username: string | null
  product_id: string | null
}

export interface TikTokStatus {
  state: 'idle' | 'login' | 'logged_in' | 'login_required' | 'ready' | string
  browser_open: boolean
  login_open: boolean
  last_error: string
  syncing: boolean
  account: TikTokAccount | null
}

export interface TikTokSyncResult { created: number; updated: number; missing: number; total: number; account: TikTokAccount }

export interface Presenter {
  id: string
  name: string
  description: string
  active: boolean
  created_at: string
  updated_at: string
}

export interface Media {
  id: string
  product_id: string | null
  presenter_ids: string[]
  original_name: string
  size_bytes: number
  sha256: string
  storage_mode: 'managed'
  status: 'ready' | 'missing'
  mime_type: string
  created_at: string
}

export interface StudioStatus {
  version: string
  data_dir: string
  database_path: string
  capabilities: {
    native_dialog: boolean
    local_file_reference: boolean
    obs: boolean
    ai: boolean
  }
}

export interface StudioEvent { event: string; data: unknown }
export interface PresenterInput { name: string; description?: string; active?: boolean }
export interface MediaLinks { product_id?: string | null; presenter_ids?: string[] }
interface ErrorInfo { code: string; message: string; details?: Record<string, unknown> }
interface Envelope<T> { ok: boolean; result: T; error: ErrorInfo | null }
interface Session { token: string; base_url: string }

export class StudioError extends Error {
  readonly code: string
  readonly details?: Record<string, unknown>
  constructor(info: ErrorInfo) {
    super(info.message)
    this.name = 'StudioError'
    this.code = info.code
    this.details = info.details
  }
}

function asError(raw: unknown): StudioError {
  if (raw instanceof StudioError) return raw
  if (raw && typeof raw === 'object' && 'code' in raw && 'message' in raw) return new StudioError(raw as ErrorInfo)
  return new StudioError({ code: 'CORE_UNAVAILABLE', message: 'Chưa kết nối được bộ máy local. Hãy chạy npm run studio rồi thử lại.' })
}

let sessionPromise: Promise<Session> | undefined
function session(): Promise<Session> {
  sessionPromise ??= fetch('/studio/session', { headers: { 'X-Studio-Client': '1' }, signal: AbortSignal.timeout(5000) })
    .then(async (response) => {
      if (!response.ok) throw new Error('Core unavailable')
      return await response.json() as Session
    }).catch((error: unknown) => { sessionPromise = undefined; throw asError(error) })
  return sessionPromise
}

function unwrap<T>(envelope: Envelope<T>): T {
  if (!envelope.ok) throw new StudioError(envelope.error ?? { code: 'CORE_ERROR', message: 'Thao tác local thất bại.' })
  return envelope.result
}

// A TikTok sync reloads the LIVE Console and waits for its product list.
const slowMethods: Record<string, number> = {
  'studio.media.import': 120_000, 'studio.tiktok.sync': 170_000, 'studio.tiktok.connect': 90_000,
  'studio.ai.reply': 100_000, 'studio.ai.test': 100_000, 'studio.scripts.generate': 120_000,
  // Starting or switching may render an OBS copy of a rotated video first.
  'studio.replies.send_comment': 130_000,
  'studio.live.start': 120_000, 'studio.live.switch': 120_000, 'studio.live.next': 120_000, 'studio.live.previous': 120_000,
}

async function request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  const timeoutMs = slowMethods[method] ?? 30_000
  try {
    if (isTauri()) {
      return await invoke<T>('core_request', { method, params, timeoutMs })
    }
    const connection = await session()
    const response = await fetch('/studio/rpc', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${connection.token}` },
      body: JSON.stringify({ method, params }),
      signal: AbortSignal.timeout(timeoutMs),
    })
    if (response.status === 401) sessionPromise = undefined
    return unwrap(await response.json() as Envelope<T>)
  } catch (raw) {
    const error = asError(raw)
    if (method === 'studio.media.import' && error.code === 'CORE_TIMEOUT') {
      throw new StudioError({ code: 'IMPORT_TIMEOUT', message: 'Core có thể vẫn đang nhập video. Đang kiểm tra lại thư viện; hãy đợi kết quả trước khi nhập lại.' })
    }
    throw error
  }
}

async function importMedia(file: File | string, productId?: string, onProgress?: (percent: number) => void): Promise<Media> {
  if (typeof file === 'string') {
    if (!isTauri()) throw new StudioError({ code: 'NATIVE_ONLY', message: 'Hãy chọn video bằng nút nhập file trong trình duyệt.' })
    const result = await request<{ media: Media }>('studio.media.import', { source_path: file, ...(productId ? { product_id: productId } : {}) })
    onProgress?.(100)
    return result.media
  }
  const connection = await session()
  return new Promise<Media>((resolve, reject) => {
    const upload = new XMLHttpRequest()
    const query = new URLSearchParams({ filename: file.name, ...(productId ? { product_id: productId } : {}) })
    upload.open('POST', `/studio/upload?${query}`)
    upload.setRequestHeader('Authorization', `Bearer ${connection.token}`)
    upload.setRequestHeader('Content-Type', 'application/octet-stream')
    upload.timeout = 30 * 60_000
    upload.upload.onprogress = (event) => {
      // 100 means validated and committed, rather than merely uploaded.
      if (event.lengthComputable) onProgress?.(Math.min(95, Math.round(event.loaded / event.total * 95)))
    }
    upload.onload = () => {
      try {
        if (upload.status === 401) sessionPromise = undefined
        const result = unwrap(JSON.parse(upload.responseText) as Envelope<{ media: Media }>)
        onProgress?.(100)
        resolve(result.media)
      } catch (raw) { reject(asError(raw)) }
    }
    upload.onerror = () => reject(asError(null))
    upload.ontimeout = () => reject(new StudioError({ code: 'IMPORT_TIMEOUT', message: 'Nhập video quá lâu; kiểm tra thư viện trước khi thử lại.' }))
    upload.onabort = () => reject(new StudioError({ code: 'IMPORT_CANCELLED', message: 'Đã dừng nhập video.' }))
    upload.send(file)
  })
}

async function subscribe(callback: (event: StudioEvent) => void): Promise<() => void> {
  if (isTauri()) {
    const unlisten = await listen<StudioEvent>('core://event', ({ payload }) => callback(payload))
    const unlistenState = await listen<{ phase: string }>('core://state', ({ payload }) => {
      callback({ event: 'studio.connection', data: payload })
    })
    return () => { unlisten(); unlistenState() }
  }
  const connection = await session()
  const events = new EventSource(`/studio/events?token=${encodeURIComponent(connection.token)}`)
  events.onmessage = (event) => {
    try { callback(JSON.parse(event.data) as StudioEvent) } catch { /* Ignore incomplete event frames. */ }
  }
  events.onerror = () => {
    sessionPromise = undefined
    callback({ event: 'studio.connection', data: { phase: 'disconnected' } })
  }
  return () => events.close()
}

export const studioClient = {
  request,
  getStatus: () => request<StudioStatus>('studio.status'),
  listProducts: async () => (await request<{ products: Product[] }>('studio.products.list')).products,
  updateProductDescription: async (id: string, description: string) => (await request<{ product: Product }>('studio.products.update', { id, description })).product,
  updateProduct: async (id: string, input: { description?: string; facts?: ProductFacts }) => (await request<{ product: Product }>('studio.products.update', { id, ...input })).product,
  aiState: () => request<AIState>('studio.ai.config'),
  aiConfigure: (input: Partial<AIConfig>) => request<AIState>('studio.ai.configure', { ...input }),
  aiSetKey: (provider: AIProvider, apiKey: string) => request<AIState>('studio.ai.set_key', { provider, api_key: apiKey }),
  aiDeleteKey: (provider: AIProvider) => request<AIState>('studio.ai.delete_key', { provider }),
  aiTest: () => request<{ ok: boolean; usage: AIUsage; sample: string }>('studio.ai.test'),
  aiReply: (input: { comment: string; product_id?: string; username?: string }) => request<AIReply>('studio.ai.reply', { ...input }),
  listScripts: async (productId?: string) => (await request<{ scripts: SalesScript[] }>('studio.scripts.list', productId ? { product_id: productId } : {})).scripts,
  generateScript: async (input: { product_id: string; style: ScriptStyle; duration_minutes: number; notes: string }) => (await request<{ script: SalesScript; usage: AIUsage }>('studio.scripts.generate', { ...input })),
  createScript: async (input: { product_id: string; title: string; body: string }) => (await request<{ script: SalesScript }>('studio.scripts.create', { ...input })).script,
  updateScript: async (id: string, input: { title?: string; body?: string }) => (await request<{ script: SalesScript }>('studio.scripts.update', { id, ...input })).script,
  deleteScript: (id: string) => request<{ deleted: boolean }>('studio.scripts.delete', { id }),
  obsStatus: () => request<ObsStatus>('studio.obs.status'),
  obsConfigure: (input: ObsSettingsInput) => request<ObsStatus>('studio.obs.configure', { ...input }),
  obsConnect: () => request<ObsStatus>('studio.obs.connect'),
  obsDisconnect: () => request<ObsStatus>('studio.obs.disconnect'),
  listLiveSessions: async () => (await request<{ sessions: LiveSession[] }>('studio.live.sessions.list')).sessions,
  createLiveSession: async (input: { title: string; product_ids: string[]; policy: PlaybackPolicy }) => (await request<{ session: LiveSession }>('studio.live.sessions.create', { ...input })).session,
  updateLiveSession: async (id: string, input: { title?: string; product_ids?: string[]; policy?: PlaybackPolicy }) => (await request<{ session: LiveSession }>('studio.live.sessions.update', { id, ...input })).session,
  deleteLiveSession: (id: string) => request<{ deleted: boolean }>('studio.live.sessions.delete', { id }),
  liveStatus: () => request<LiveStatus>('studio.live.status'),
  liveStart: (sessionId: string, productId?: string) => request<LiveStatus>('studio.live.start', { session_id: sessionId, ...(productId ? { product_id: productId } : {}) }),
  liveSwitch: (productId: string, mode: 'immediate' | 'after_current') => request<LiveStatus>('studio.live.switch', { product_id: productId, mode }),
  replies: (method: 'status' | 'chat_stop' | 'stop_voice') => request<ReplyDeskStatus>(`studio.replies.${method}`),
  repliesConfigure: (input: Partial<ReplySettings>) => request<ReplyDeskStatus>('studio.replies.configure', { ...input }),
  repliesChatStart: (username?: string) => request<ReplyDeskStatus>('studio.replies.chat_start', username ? { username } : {}),
  repliesSimulate: (comment: string, username?: string) => request<{ entry: ReplyEntry }>('studio.replies.simulate', { comment, ...(username ? { username } : {}) }),
  replyAction: (action: 'speak' | 'send_comment' | 'cancel' | 'dismiss', id: number, text?: string) => request<{ entry: ReplyEntry }>(`studio.replies.${action}`, { id, ...(text !== undefined ? { text } : {}) }),
  liveControl: (action: 'pause' | 'resume' | 'next' | 'previous' | 'cancel_pending' | 'stop') => request<LiveStatus>(`studio.live.${action}`),
  tiktokStatus: () => request<TikTokStatus>('studio.tiktok.status'),
  tiktokLogin: () => request<TikTokStatus>('studio.tiktok.login'),
  tiktokConnect: () => request<TikTokStatus>('studio.tiktok.connect'),
  tiktokSync: () => request<TikTokSyncResult>('studio.tiktok.sync'),
  deleteProduct: (id: string) => request<{ deleted: boolean }>('studio.products.delete', { id }),
  listPresenters: async () => (await request<{ presenters: Presenter[] }>('studio.presenters.list')).presenters,
  createPresenter: async (input: PresenterInput) => (await request<{ presenter: Presenter }>('studio.presenters.create', { ...input })).presenter,
  updatePresenter: async (id: string, input: Partial<PresenterInput>) => (await request<{ presenter: Presenter }>('studio.presenters.update', { id, ...input })).presenter,
  deletePresenter: (id: string) => request<{ deleted: boolean }>('studio.presenters.delete', { id }),
  listMedia: async (productId?: string) => (await request<{ media: Media[] }>('studio.media.list', productId ? { product_id: productId } : {})).media,
  updateMedia: async (id: string, links: MediaLinks) => (await request<{ media: Media }>('studio.media.update', { id, ...links })).media,
  importMedia,
  deleteMedia: (id: string) => request<{ deleted: boolean }>('studio.media.delete', { id }),
  mediaUrl: async (id: string) => (await request<{ url: string }>('studio.media.url', { id })).url,
  subscribe,
}
