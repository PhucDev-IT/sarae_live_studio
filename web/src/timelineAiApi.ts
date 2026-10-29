/** AI Timeline Analyzer (backend/app/timeline_ai): settings, analysis jobs and review drafts. */
import { apiRequest } from './mediaApi'
import type { MarkerAction, ReviewStatus, Timeline } from './liveSessionsApi'

export type TimelineAISettings = {
  transcript:{provider:'faster_whisper'; model:string; language:string; device:string; compute_type:string}
  alignment:{provider:'words'|'whisperx'; model:string|null}
  llm:{provider:'gemini'; model:string|null; max_output_tokens:number}
  vision:{enabled:boolean; provider:'gemini'; model:string|null}
  min_confidence:number; unpin_after_seconds:number
}
export type TimelineAISettingsView = TimelineAISettings & {
  resolved:TimelineAISettings; has_api_key:boolean; installed:{faster_whisper:boolean; whisperx:boolean; ffmpeg:boolean}
}
export type AISuggestion = {
  start_ms:number; end_ms:number; product_id:number|null; product_name?:string|null; action:MarkerAction; confidence:number
  evidence_transcript:string; evidence_timestamp_ms:number; match_reason:string; review_status:ReviewStatus; sources?:string[]
}
export type AnalysisJob = {
  id:number; session_id:number; media_id:string; status:'QUEUED'|'RUNNING'|'DONE'|'FAILED'; phase:string; progress:number
  cached:boolean; error:string|null; timeline_id:number|null; created_at:string; updated_at:string
  summary?:{suggestions:number; needs_review:number; dropped_products:number; notes:string[]}
  suggestions?:AISuggestion[]
}

const json = (data:unknown, method = 'POST'):RequestInit => ({method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)})
const base = (sessionId:number) => `/live/sessions/${sessionId}/timeline-analysis`
export const timelineAiApi = {
  settings:() => apiRequest<TimelineAISettingsView>('/ai/timeline-settings'),
  saveSettings:(body:TimelineAISettings) => apiRequest<TimelineAISettingsView>('/ai/timeline-settings', json(body, 'PUT')),
  list:(sessionId:number) => apiRequest<AnalysisJob[]>(base(sessionId)),
  get:(sessionId:number, id:number) => apiRequest<AnalysisJob>(`${base(sessionId)}/${id}`),
  run:(sessionId:number, force = false) => apiRequest<AnalysisJob>(base(sessionId), json({force})),
  draft:(sessionId:number, id:number) => apiRequest<Timeline>(`${base(sessionId)}/${id}/draft`, {method:'POST'}),
}
