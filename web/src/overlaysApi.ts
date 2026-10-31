/** LIVE overlays of a timeline version and the owner's overlay images (docs/LIVE_OVERLAYS.md §6). */
import { apiRequest } from './mediaApi'
import type { TimelineStatus } from './liveSessionsApi'
import { normalizeOverlay, type AnimationName, type Overlay, type OverlayIssue, type OverlayPreset, type OverlayScene, type ProductField } from './overlay/overlayModel'

export type OverlaysView = { timeline_id:number; version:number; status:TimelineStatus; canvas:{width:number;height:number}; overlays:Overlay[]; issues:OverlayIssue[] }
export type OverlayTemplates = {
  canvas:{width:number;height:number}; blocks:string[]; product_fields:ProductField[]; animations:AnimationName[]; max_concurrent:number; templates:OverlayPreset[]
  /** The owner's own templates (reusable in every session). */
  owner_templates:OverlayPreset[]
}
/** One line of a readiness report (Backend or Agent side). */
export type ReadinessCheck = { code:string; ok:boolean; level:'ok'|'warning'|'error'; message:string }
export type OverlayReadiness = {
  session_id:number; session_name:string; generated_at:string; ok:boolean; active_version:number|null; snapshot_revision:string|null
  checks:ReadinessCheck[]; agent_overlay:Record<string, unknown>|null
}
/** What the Agent answers to "Kiểm tra OBS Overlay" (the overlay_check command). */
export type AgentOverlayCheck = { session_id:number; ok:boolean; checks:ReadinessCheck[]; status:Record<string, unknown> }
/** An owner template as the API accepts it. */
export type TemplateBody = Pick<Overlay, 'template'|'layout'|'elements'|'animation_in'|'animation_out'|'duration_ms'> & { name:string; description?:string }
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
  createTemplate:(t:TemplateBody) => apiRequest<OverlayPreset>('/live/overlay-templates', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(t)}),
  duplicateTemplate:(id:number) => apiRequest<OverlayPreset>(`/live/overlay-templates/${id}/duplicate`, {method:'POST'}),
  removeTemplate:(id:number) => apiRequest<void>(`/live/overlay-templates/${id}`, {method:'DELETE'}),
  readiness:(sessionId:number, agentId?:string|null) => apiRequest<OverlayReadiness>(`/live/sessions/${sessionId}/overlay-readiness${agentId ? '?agent_id=' + encodeURIComponent(agentId) : ''}`),
  /** Asks the Agent to check (and prepare, hidden) the OBS overlay; wait for the result with waitCommand. */
  check:(sessionId:number, agentId?:string|null) => apiRequest<{agent_id:string; command_id:string}>(`/live/sessions/${sessionId}/overlay-check`,
    {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({agent_id:agentId ?? null})}),
  assets:() => apiRequest<OverlayAsset[]>('/live/overlay-assets'),
  upload:(file:File) => {
    const form = new FormData()
    form.append('file', file)
    return apiRequest<OverlayAsset>('/live/overlay-assets', {method:'POST', body:form})
  },
  removeAsset:(id:string) => apiRequest<void>(`/live/overlay-assets/${id}`, {method:'DELETE'}),
}
