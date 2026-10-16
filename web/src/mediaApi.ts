/** Media library API shared by the library page and the per-product LIVE video setup. */
export type Media = { id:number; filename:string; size_bytes:number; duration_seconds:number|null; width:number|null; height:number|null; fps:number|null; video_codec:string; product_ids:number[]; presenter_id:number|null; tags:string; created_at:string; local_path:string|null }
export type CatalogItem = { id:number; name:string }
export type Product = CatalogItem & { sku?:string; image_url?:string }

export const api = ((import.meta.env.VITE_API_URL as string|undefined) || 'http://127.0.0.1:8000').replace(/\/$/, '')
export const fileUrl = (id:number) => `${api}/api/media/${id}/file`

export const sizeLabel = (bytes:number) => bytes >= 1024 ** 3 ? (bytes / 1024 ** 3).toFixed(2) + ' GB' : (bytes / 1024 ** 2).toFixed(1) + ' MB'
export const durationLabel = (seconds?:number|null) => seconds == null || !Number.isFinite(seconds) ? null : `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
export const errorText = (e:unknown, fallback:string) => e instanceof Error && e.message ? e.message : fallback

export async function apiRequest<T>(url:string, options?:RequestInit):Promise<T>{
  const response = await fetch(api + '/api' + url, options)
  if (!response.ok) {
    const result = await response.json().catch(()=>({}))
    throw new Error(typeof result.detail === 'string' ? result.detail : 'HTTP ' + response.status)
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>
}

/** Ordered LIVE playlist of a product; the array order is the playback order. */
export const getProductVideos = (productId:number) =>
  apiRequest<{product_id:number;media_ids:number[]}>(`/product-videos/${productId}`).then(result=>result.media_ids)

export const putProductVideos = (productId:number, mediaIds:number[]) =>
  apiRequest<{product_id:number;media_ids:number[]}>(`/product-videos/${productId}`, {
    method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({media_ids:mediaIds}),
  })

/**
 * Replaces the products that use a video (many-to-many) in one backend transaction.
 * Newly linked products get the video appended to the end of their playlist; existing order is kept.
 */
export const setVideoProducts = (mediaId:number, productIds:number[]) =>
  apiRequest<Media>(`/media/${mediaId}/products`, {
    method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({product_ids:productIds}),
  })
