/** Centralized Media Library (cloud storage managed by Sarae): list, edit, links, covers and resumable uploads. */
import { authHeaders, clearAuth } from './authToken'
import { api, apiRequest } from './mediaApi'

/** A video of the logged-in User. Storage ids/URLs are never exposed; preview uses short-lived signed URLs. */
export type CloudMedia = {
  id:string; filename:string; display_name:string; notes:string; tags:string; mime_type:string; size_bytes:number
  duration_seconds:number|null; width:number|null; height:number|null; checksum_sha256:string|null
  status:'AVAILABLE'|'DELETED'; available:boolean; product_ids:number[]; presenter_ids:number[]; has_thumbnail:boolean
  stream_url:string|null; thumb_url:string|null; created_at:string; updated_at:string
}
export type UploadView = { id:string; client_upload_id:string; filename:string; size_bytes:number; received_bytes:number
  status:'UPLOADING'|'COMPLETED'|'CANCELLED'|'FAILED'; media_id:string|null; error_code:string|null; chunk_size:number }

const json = (method:string, body:unknown):RequestInit => ({method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})

export const listCloudMedia = () => apiRequest<{total:number; items:CloudMedia[]}>('/media-library?limit=500')
export const patchCloudMedia = (id:string, body:Partial<Pick<CloudMedia,'display_name'|'notes'|'tags'>>) =>
  apiRequest<CloudMedia>('/media-library/' + id, json('PATCH', body))
export const deleteCloudMedia = (id:string) => apiRequest<CloudMedia>('/media-library/' + id, {method:'DELETE'})
export const setCloudMediaProducts = (id:string, productIds:number[]) =>
  apiRequest<CloudMedia>(`/media-library/${id}/products`, json('PUT', {product_ids:productIds}))
export const setCloudMediaPresenters = (id:string, presenterIds:number[]) =>
  apiRequest<CloudMedia>(`/media-library/${id}/presenters`, json('PUT', {presenter_ids:presenterIds}))

/** Save a cover image; duration/size measured by the browser fill what the storage has not reported yet. */
export function putCover(id:string, image:Blob, meta:{duration?:number|null; width?:number|null; height?:number|null} = {}){
  const query = new URLSearchParams()
  if (meta.duration) query.set('duration_seconds', meta.duration.toFixed(3))
  if (meta.width && meta.height) {query.set('width', String(meta.width));query.set('height', String(meta.height))}
  return apiRequest<CloudMedia>(`/media-library/${id}/thumbnail?${query}`, {method:'PUT', body:image, headers:{'Content-Type':image.type || 'image/jpeg'}})
}

export const ACCEPTED_VIDEO = '.mp4,.mov,.m4v,video/mp4,video/quicktime,video/x-m4v'
const RETRIES = 4

class HttpError extends Error {
  readonly status:number
  constructor(message:string, status:number){super(message);this.status = status}
}

/** One chunk with XMLHttpRequest, so progress moves while the chunk is being sent (fetch cannot report it). */
function sendChunk(uploadId:string, blob:Blob, start:number, total:number, onSent:(bytes:number)=>void, signal:AbortSignal):Promise<UploadView>{
  return new Promise((resolve, reject)=>{
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', `${api}/api/media-library/uploads/${uploadId}/chunk`)
    for (const [key, value] of Object.entries(authHeaders())) xhr.setRequestHeader(key, value)
    xhr.setRequestHeader('Content-Type', 'application/octet-stream')
    xhr.setRequestHeader('Content-Range', `bytes ${start}-${start + blob.size - 1}/${total}`)
    xhr.upload.onprogress = event => onSent(event.loaded)
    xhr.onload = () => {
      if (xhr.status === 401) clearAuth()
      let body:{detail?:unknown} = {}
      try { body = JSON.parse(xhr.responseText) } catch { /* empty body */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as UploadView)
      else reject(new HttpError(typeof body.detail === 'string' ? body.detail : 'HTTP ' + xhr.status, xhr.status))
    }
    xhr.onerror = () => reject(new HttpError('Mất kết nối khi tải lên', 0))
    const abort = () => xhr.abort()
    xhr.onabort = () => reject(new DOMException('Đã hủy', 'AbortError'))
    signal.addEventListener('abort', abort, {once:true})
    xhr.send(blob)
  })
}

/**
 * Upload one file in chunks. Safe to call again with the same clientUploadId (also after a page reload): the
 * Backend returns the same upload and the real stored offset, so nothing is uploaded twice and no duplicate video
 * is created.
 */
export async function uploadVideo(file:File, clientUploadId:string, onProgress:(sent:number)=>void,
  signal:AbortSignal, onStarted?:(uploadId:string)=>void):Promise<UploadView>{
  let upload = await apiRequest<UploadView>('/media-library/uploads', {...json('POST', {client_upload_id:clientUploadId, filename:file.name, size_bytes:file.size}), signal})
  onStarted?.(upload.id)
  let failures = 0
  while (upload.status === 'UPLOADING') {
    if (signal.aborted) throw new DOMException('Đã hủy', 'AbortError')
    const start = upload.received_bytes
    onProgress(start)
    const end = Math.min(start + upload.chunk_size, file.size)
    try {
      upload = await sendChunk(upload.id, file.slice(start, end), start, file.size, sent=>onProgress(start + sent), signal)
      failures = 0
    } catch (error) {
      if (signal.aborted) throw error
      if (error instanceof HttpError && error.status >= 400 && error.status < 500 && error.status !== 409 && error.status !== 408) throw error
      if (++failures > RETRIES) throw error
      await new Promise(resolve=>window.setTimeout(resolve, 1000 * 2 ** failures))
      // Ask where the storage really is (a lost reply may already have stored the chunk).
      upload = await apiRequest<UploadView>('/media-library/uploads/' + upload.id)
    }
  }
  onProgress(upload.received_bytes)
  if (upload.status !== 'COMPLETED') throw new Error(upload.error_code ? `Upload thất bại (${upload.error_code})` : 'Upload thất bại')
  return upload
}

export const cancelUpload = (uploadId:string) => apiRequest<UploadView>('/media-library/uploads/' + uploadId, {method:'DELETE'})
