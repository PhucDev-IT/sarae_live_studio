/** LIVE overlays of a timeline version and the owner's overlay images (docs/LIVE_OVERLAYS.md §6). */
import { apiRequest } from './mediaApi'
import type { TimelineStatus } from './liveSessionsApi'
import { normalizeOverlay, type AnimationName, type Overlay, type OverlayIssue, type OverlayPreset, type OverlayScene, type ProductField } from './overlay/overlayModel'

export type OverlaysView = { timeline_id:number; version:number; status:TimelineStatus; canvas:{width:number;height:number}; overlays:Overlay[]; issues:OverlayIssue[] }
export type OverlayTemplates = {
  canvas:{width:number;height:number}; blocks:string[]; product_fields:ProductField[]; animations:AnimationName[]; max_concurrent:number; templates:OverlayPreset[]
}
export type OverlayAsset = { id:string; filename:string; mime_type:string; size_bytes:number; sha256:string; width:number; height:number; created_at:string; url:string }
export type OverlaySnapshot = OverlayScene & { revision:string; session_id:number; timeline_id:number; version:number }

const path = (sessionId:number, timelineId:number, rest = '') => `/live/sessions/${sessionId}/timelines/${timelineId}/overlays${rest}`
const view = (v:OverlaysView):OverlaysView => ({...v, overlays:v.overlays.map(o=>normalizeOverlay(o))})
/** What the PUT accepts: no id or timestamps (`extra` is forbidden). */
const body = (o:Overlay) => ({
  media_id:o.media_id, template:o.template, label:o.label, start_ms:Math.round(o.start_ms), duration_ms:Math.round(o.duration_ms), product_id:o.product_id,
  layout:o.layout, animation_in:o.animation_in, animation_out:o.animation_out, elements:o.elements, enabled:o.enabled !== false,
})

export const overlaysApi = {
  list:(sessionId:number, timelineId:number) => apiRequest<OverlaysView>(path(sessionId, timelineId)).then(view),
  /** Replaces every overlay of a DRAFT (all videos), in this order. */
  save:(sessionId:number, timelineId:number, overlays:Overlay[]) => apiRequest<OverlaysView>(path(sessionId, timelineId),
    {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({overlays:overlays.map(body)})}).then(view),
  snapshot:(sessionId:number, timelineId:number) => apiRequest<OverlaySnapshot>(path(sessionId, timelineId, '/snapshot')),
  templates:() => apiRequest<OverlayTemplates>('/live/overlay-templates'),
  assets:() => apiRequest<OverlayAsset[]>('/live/overlay-assets'),
  upload:(file:File) => {
    const form = new FormData()
    form.append('file', file)
    return apiRequest<OverlayAsset>('/live/overlay-assets', {method:'POST', body:form})
  },
  removeAsset:(id:string) => apiRequest<void>(`/live/overlay-assets/${id}`, {method:'DELETE'}),
}
