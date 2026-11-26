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
  created_at: string
  updated_at: string
}

export interface TikTokAccount {
  user_id: string
  nick_name: string
  user_name: string
  avatar_url: string
  product_count: number
  last_synced_at: string
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
