/** Centralized Media Library (cloud storage managed by Sarae): list, edit, product links and resumable uploads. */
import { apiRequest } from './mediaApi'

/** A video of the logged-in User. Storage ids/URLs are never exposed; preview uses short-lived signed URLs. */
export type CloudMedia = {
  id:string; filename:string; display_name:string; notes:string; tags:string; mime_type:string; size_bytes:number
  duration_seconds:number|null; width:number|null; height:number|null; checksum_sha256:string|null
  status:'AVAILABLE'|'DELETED'; available:boolean; product_ids:number[]; stream_url:string|null; thumb_url:string|null
  created_at:string; updated_at:string
}
export type UploadView = { id:string; client_upload_id:string; filename:string; size_bytes:number; received_bytes:number
  status:'UPLOADING'|'COMPLETED'|'CANCELLED'|'FAILED'; media_id:string|null; error_code:string|null; chunk_size:number }
export type ProductVideos = { product_id:number; media_ids:string[]; items:CloudMedia[]; available_count:number }

const json = (method:string, body:unknown):RequestInit => ({method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})

export const listCloudMedia = (params:{q?:string; product_id?:number|null; status?:string} = {}) => {
  const query = new URLSearchParams({limit:'500'})
  if (params.q) query.set('q', params.q)
  if (params.product_id) query.set('product_id', String(params.product_id))
  if (params.status) query.set('status', params.status)
  return apiRequest<{total:number; items:CloudMedia[]}>('/media-library?' + query)
}
export const patchCloudMedia = (id:string, body:Partial<Pick<CloudMedia,'display_name'|'notes'|'tags'>>) =>
  apiRequest<CloudMedia>('/media-library/' + id, json('PATCH', body))
export const deleteCloudMedia = (id:string) => apiRequest<CloudMedia>('/media-library/' + id, {method:'DELETE'})
export const setCloudMediaProducts = (id:string, productIds:number[]) =>
  apiRequest<CloudMedia>(`/media-library/${id}/products`, json('PUT', {product_ids:productIds}))
export const getProductCloudVideos = (productId:number) => apiRequest<ProductVideos>(`/products/${productId}/videos`)
export const putProductCloudVideos = (productId:number, mediaIds:string[]) =>
  apiRequest<ProductVideos>(`/products/${productId}/videos`, json('PUT', {media_ids:mediaIds}))

export const ACCEPTED_VIDEO = '.mp4,.mov,.m4v,video/mp4,video/quicktime,video/x-m4v'
const RETRIES = 4

/**
 * Upload one file in chunks. Safe to call again with the same clientUploadId after a failure: the Backend returns
 * the same upload and the real stored offset, so nothing is uploaded twice and no duplicate video is created.
 */
export async function uploadVideo(file:File, clientUploadId:string, onProgress:(sent:number, total:number)=>void,
  signal:AbortSignal, onStarted?:(uploadId:string)=>void):Promise<UploadView>{
  let upload = await apiRequest<UploadView>('/media-library/uploads', json('POST', {client_upload_id:clientUploadId, filename:file.name, size_bytes:file.size}))
  onStarted?.(upload.id)
  let failures = 0
  while (upload.status === 'UPLOADING') {
    if (signal.aborted) throw new DOMException('Đã hủy', 'AbortError')
    onProgress(upload.received_bytes, file.size)
    const start = upload.received_bytes
    const end = Math.min(start + upload.chunk_size, file.size) - 1
    try {
      upload = await apiRequest<UploadView>(`/media-library/uploads/${upload.id}/chunk`, {
        method:'PUT', body:file.slice(start, end + 1), signal,
        headers:{'Content-Type':'application/octet-stream', 'Content-Range':`bytes ${start}-${end}/${file.size}`},
      })
      failures = 0
    } catch (error) {
      if (signal.aborted) throw error
      if (++failures > RETRIES) throw error
      await new Promise(resolve=>window.setTimeout(resolve, 1000 * 2 ** failures))
      // Ask where the storage really is (a lost reply may already have stored the chunk).
      upload = await apiRequest<UploadView>('/media-library/uploads/' + upload.id)
    }
  }
  onProgress(upload.received_bytes, file.size)
  if (upload.status !== 'COMPLETED') throw new Error(upload.error_code ? `Upload thất bại (${upload.error_code})` : 'Upload thất bại')
  return upload
}

export const cancelUpload = (uploadId:string) => apiRequest<UploadView>('/media-library/uploads/' + uploadId, {method:'DELETE'})
