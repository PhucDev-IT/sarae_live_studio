import { useCallback, useEffect, useState } from 'react'
import { Alert, Badge, Button, Card, Input, Modal, Select, Switch, useToast } from './ui/core'
import { Bot, FilePlus2, RefreshCw, Settings2, Sparkles } from 'lucide-react'
import { errorText } from './mediaApi'
import { hms } from './liveSessionsApi'
import { timelineAiApi, type AnalysisJob, type TimelineAISettings, type TimelineAISettingsView } from './timelineAiApi'

const POLL_MS = 2000

/**
 * AI suggestions for the pin timeline of a Single Video session: run the analysis of the main video (cached when nothing
 * changed), read the suggestions with their evidence, and turn them into a new draft to review. Nothing is applied by
 * itself: suggestions under the confidence threshold need a person's review before the draft can be approved.
 */
export default function TimelineAIPanel({sessionId, disabled, onSeek, onDraft}:{
  sessionId:number; disabled:boolean; onSeek:(ms:number)=>void; onDraft:(timelineId:number)=>void
}){
  const toast = useToast()
  const [job,setJob] = useState<AnalysisJob|null>(null)
  const [busy,setBusy] = useState(false)
  const [settingsOpen,setSettingsOpen] = useState(false)
  const loadLatest = useCallback(async ()=>{
    const list = await timelineAiApi.list(sessionId).catch(()=>[] as AnalysisJob[])
    setJob(list[0] ? await timelineAiApi.get(sessionId, list[0].id) : null)
  },[sessionId])
  useEffect(()=>{void loadLatest()},[loadLatest])
  const running = job?.status === 'QUEUED' || job?.status === 'RUNNING'
  useEffect(()=>{
    if (!running || !job) return
    const timer = window.setInterval(()=>void timelineAiApi.get(sessionId, job.id).then(setJob).catch(()=>{}), POLL_MS)
    return ()=>window.clearInterval(timer)
  },[running, job, sessionId])

  const analyze = async (force:boolean) => {
    setBusy(true)
    try {const started = await timelineAiApi.run(sessionId, force);setJob(await timelineAiApi.get(sessionId, started.id))}
    catch(e){toast.push({tone:'danger', title:'Không chạy được phân tích', description:errorText(e, '')})}
    finally{setBusy(false)}
  }
  const makeDraft = async () => {
    if (!job) return
    setBusy(true)
    try {
      const draft = await timelineAiApi.draft(sessionId, job.id)
      toast.push({tone:'success', title:`Đã tạo bản nháp phiên bản ${draft.version} từ đề xuất AI`, description:'Duyệt từng mốc AI cần xem lại trước khi duyệt phiên bản.'})
      onDraft(draft.id)
      await loadLatest()
    } catch(e){toast.push({tone:'danger', title:'Không tạo được bản nháp', description:errorText(e, '')})}
    finally{setBusy(false)}
  }

  return <Card className="te-ai-panel" padding={false} title={<span className="te-card-title"><Sparkles size={17}/>Gợi ý mốc bằng AI</span>}
    description="Nhận dạng lời nói tiếng Việt, đối chiếu với catalog thật và đề xuất mốc ghim kèm bằng chứng. Bạn duyệt trước khi dùng."
    actions={<Button size="sm" variant="ghost" leftIcon={<Settings2 size={15}/>} onClick={()=>setSettingsOpen(true)}>Cấu hình</Button>}>
    <div className="te-ai-body">
      <div className="te-ai-actions">
        <Button leftIcon={<Bot size={15}/>} loading={busy && !job} disabled={busy || running || disabled} onClick={()=>void analyze(false)}>Phân tích video</Button>
        {job?.status === 'DONE' && <Button variant="ghost" leftIcon={<RefreshCw size={15}/>} disabled={busy || disabled} onClick={()=>void analyze(true)} title="Bỏ qua cache, chạy lại mọi bước">Phân tích lại</Button>}
        {job?.status === 'DONE' && (job.summary?.suggestions ?? 0) > 0 && <Button variant="outline" leftIcon={<FilePlus2 size={15}/>} disabled={busy || disabled} onClick={()=>void makeDraft()}>Tạo bản nháp từ đề xuất</Button>}
      </div>
      {running && <div className="te-ai-progress"><span style={{width:`${Math.round((job?.progress ?? 0) * 100)}%`}}/><small>{job?.phase || 'Đang chờ máy chủ'} · {Math.round((job?.progress ?? 0) * 100)}%</small></div>}
      {job?.status === 'FAILED' && <Alert tone="danger" title="Phân tích thất bại">{job.error}</Alert>}
      {job?.status === 'DONE' && job.summary && <p className="te-muted">
        {job.summary.suggestions} đề xuất · {job.summary.needs_review} cần xem lại{job.summary.dropped_products ? ` · bỏ ${job.summary.dropped_products} sản phẩm AI tự nêu không có trong catalog` : ''}{job.cached ? ' · từ cache' : ''}{job.timeline_id ? ' · đã tạo bản nháp' : ''}
        {job.summary.notes.map(n=><span key={n}> · {n}</span>)}
      </p>}
      {!!job?.suggestions?.length && <ol className="te-ai-list">{job.suggestions.map((s, i)=><li key={i} className={'is-' + s.review_status.toLowerCase()}>
        <button type="button" className="te-ai-time" onClick={()=>onSeek(s.start_ms)} title="Tua tới đoạn này">{hms(s.start_ms)}{s.end_ms > s.start_ms ? `–${hms(s.end_ms)}` : ''}</button>
        <div>
          <strong>{s.action === 'UNPIN_PRODUCT' ? 'Bỏ ghim' : s.product_name ?? 'Chưa xác định sản phẩm'}</strong>
          <Badge tone={s.review_status === 'APPROVED' ? 'success' : 'warning'}>{Math.round(s.confidence * 100)}% · {s.review_status === 'APPROVED' ? 'đủ tin cậy' : 'cần xem lại'}</Badge>
          {s.evidence_transcript && <button type="button" className="te-ai-quote" onClick={()=>onSeek(s.evidence_timestamp_ms)} title="Tua tới câu nói">“{s.evidence_transcript}” · {hms(s.evidence_timestamp_ms)}</button>}
          <small className="te-muted">{s.match_reason}</small>
        </div>
      </li>)}</ol>}
    </div>
    {settingsOpen && <AISettingsModal onClose={()=>setSettingsOpen(false)}/>}
  </Card>
}

/** Providers and models of the analyzer (owner settings); API keys are managed on the AI Assistant page. */
function AISettingsModal({onClose}:{onClose:()=>void}){
  const toast = useToast()
  const [view,setView] = useState<TimelineAISettingsView|null>(null)
  const [form,setForm] = useState<TimelineAISettings|null>(null)
  const [busy,setBusy] = useState(false)
  useEffect(()=>{void timelineAiApi.settings().then(v=>{setView(v);setForm(v)}).catch(e=>toast.push({tone:'danger', title:'Không tải được cấu hình', description:errorText(e, '')}))},[toast])
  const save = async () => {
    if (!form) return
    setBusy(true)
    try {const v = await timelineAiApi.saveSettings(form);setView(v);setForm(v);toast.push({tone:'success', title:'Đã lưu cấu hình phân tích AI'});onClose()}
    catch(e){toast.push({tone:'danger', title:'Không lưu được cấu hình', description:errorText(e, '')})}
    finally{setBusy(false)}
  }
  const set = <K extends keyof TimelineAISettings>(key:K, patch:Partial<TimelineAISettings[K]>) => setForm(f=>f ? {...f, [key]:{...(f[key] as object), ...patch}} : f)
  return <Modal open title="Cấu hình phân tích AI" size="md" onClose={onClose} footer={<>
    <Button variant="ghost" onClick={onClose}>Hủy</Button>
    <Button loading={busy} disabled={!form} onClick={()=>void save()}>Lưu</Button>
  </>}>
    {!form || !view ? <p className="te-muted">Đang tải…</p> : <div className="te-ai-settings">
      {!view.installed.faster_whisper && <Alert tone="warning" title="Máy chủ chưa cài Faster-Whisper">Cài <code>backend/requirements-timeline-ai.txt</code> trên máy chủ để nhận dạng giọng nói.</Alert>}
      {!view.installed.ffmpeg && <Alert tone="warning" title="Máy chủ chưa có ffmpeg">Cần ffmpeg/ffprobe để tách âm thanh và khung hình.</Alert>}
      {!view.has_api_key && <Alert tone="warning" title="Chưa có API key Gemini">Thêm API key ở trang AI Assistant.</Alert>}
      <fieldset><legend>Nhận dạng giọng nói (Faster-Whisper, chạy trên máy chủ)</legend>
        <Input label="Model" value={form.transcript.model} onChange={e=>set('transcript', {model:e.target.value})} hint="VD: small, medium, large-v3 (lớn hơn: chính xác hơn, chậm hơn)"/>
        <Select label="Thiết bị" value={form.transcript.device} onChange={v=>set('transcript', {device:v})} options={[{value:'auto', label:'Tự chọn'}, {value:'cpu', label:'CPU'}, {value:'cuda', label:'GPU (CUDA)'}]}/>
        <Select label="Căn thời gian từng từ" value={form.alignment.provider} onChange={v=>set('alignment', {provider:v as 'words'|'whisperx'})}
          options={[{value:'words', label:'Theo Whisper (mặc định)'}, {value:'whisperx', label:`WhisperX${view.installed.whisperx ? '' : ' (chưa cài)'}`}]}/>
      </fieldset>
      <fieldset><legend>Phân tích sản phẩm (LLM)</legend>
        <Input label="Model Gemini" value={form.llm.model ?? ''} placeholder={view.resolved.llm.model ?? ''} onChange={e=>set('llm', {model:e.target.value.trim() || null})} hint="Để trống: dùng model trong trang AI Assistant"/>
        <Switch label="Kiểm tra hình ảnh cho đoạn chưa chắc chắn (Gemini multimodal)" checked={form.vision.enabled} onChange={e=>set('vision', {enabled:e.target.checked})}/>
      </fieldset>
      <fieldset><legend>Ngưỡng</legend>
        <Input type="number" label="Độ tin cậy tối thiểu (0.3–0.99)" min={0.3} max={0.99} step={0.05} value={form.min_confidence}
          onChange={e=>setForm(f=>f ? {...f, min_confidence:Number(e.target.value)} : f)} hint="Thấp hơn ngưỡng: đề xuất ở trạng thái Cần xem lại"/>
        <Input type="number" label="Đề xuất bỏ ghim sau (giây, 0 = không)" min={0} max={3600} value={form.unpin_after_seconds}
          onChange={e=>setForm(f=>f ? {...f, unpin_after_seconds:Number(e.target.value)} : f)}/>
      </fieldset>
    </div>}
  </Modal>
}
