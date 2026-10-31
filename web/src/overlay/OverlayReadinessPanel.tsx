import { useState } from 'react'
import { Badge, Button, Card, useToast } from '../ui/core'
import { CircleAlert, CircleCheck, Download, MonitorCheck, TriangleAlert } from 'lucide-react'
import { useAgent } from '../agentContext'
import { errorText, waitCommand } from '../mediaApi'
import { overlaysApi, type AgentOverlayCheck, type OverlayReadiness, type ReadinessCheck } from '../overlaysApi'
import './overlay-readiness.css'

type Report = {server:OverlayReadiness; agent:AgentOverlayCheck|null; agentError:string|null; agentId:string|null; checkedAt:string}

/**
 * "Kiểm tra OBS Overlay" before LIVE: the Backend's checks (active version, overlays, images, Agent online and in sync)
 * and the Agent's (OBS connected, canvas 9:16, Browser Source present with its transform read back, images on the PC,
 * renderer READY). The Agent prepares the overlay hidden; nothing is shown and playback is never touched. The report
 * can be downloaded for the operator.
 */
export default function OverlayReadinessPanel({sessionId, sessionAgentId}:{sessionId:number; sessionAgentId:string|null}){
  const toast = useToast()
  const {agentId:tabAgent, agent:tabAgentView} = useAgent()
  const agentId = sessionAgentId ?? (tabAgentView?.platform !== 'ANDROID' ? tabAgent : null)
  const [busy,setBusy] = useState(false)
  const [report,setReport] = useState<Report|null>(null)

  const run = async () => {
    setBusy(true)
    let agent:AgentOverlayCheck|null = null, agentError:string|null = null
    try {
      if (agentId) {
        try {
          const sent = await overlaysApi.check(sessionId, agentId)
          const done = await waitCommand(sent.agent_id, sent.command_id, 60000)
          agent = (typeof done.result === 'string' ? JSON.parse(done.result) : done.result) as AgentOverlayCheck
        } catch(e){agentError = errorText(e, 'Agent không trả lời')}
      } else agentError = 'Chưa chọn Agent Windows sẽ phát phiên này'
      const server = await overlaysApi.readiness(sessionId, agentId)
      setReport({server, agent, agentError, agentId, checkedAt:new Date().toISOString()})
    } catch(e){toast.push({tone:'danger', title:'Không kiểm tra được overlay', description:errorText(e, '')})}
    finally{setBusy(false)}
  }

  const ok = report ? report.server.ok && !!report.agent?.ok && !report.agentError : null
  return <Card className="orp" padding={false} title={<span className="te-card-title"><MonitorCheck size={17}/>Kiểm tra OBS Overlay trước LIVE</span>}
    description="Agent chuẩn bị overlay ở chế độ ẩn (tải ảnh, kiểm tra Browser Source, canvas, vị trí) và báo lại. Không hiện overlay, không đổi video."
    actions={<div className="orp-actions">
      {report && <>
        <Button size="sm" variant="ghost" leftIcon={<Download size={14}/>} onClick={()=>download(`overlay-readiness-${sessionId}.md`, markdown(report), 'text/markdown')}>Báo cáo .md</Button>
        <Button size="sm" variant="ghost" leftIcon={<Download size={14}/>} onClick={()=>download(`overlay-readiness-${sessionId}.json`, JSON.stringify(report, null, 2), 'application/json')}>.json</Button>
      </>}
      <Button size="sm" loading={busy} onClick={()=>void run()}>{report ? 'Kiểm tra lại' : 'Kiểm tra OBS Overlay'}</Button>
    </div>}>
    <div className="orp-body">
      {!report ? <p className="te-muted">Chạy trước khi LIVE (OBS đang mở, Agent online). {agentId ? '' : 'Chọn Agent Windows ở thanh trên cùng.'}</p> : <>
        <div className="orp-summary">{ok ? <Badge tone="success"><CircleCheck size={12}/>Sẵn sàng</Badge> : <Badge tone="danger"><CircleAlert size={12}/>Chưa sẵn sàng</Badge>}
          <small className="te-muted">Phiên bản {report.server.active_version ?? '—'} · {new Date(report.checkedAt).toLocaleString('vi-VN')}</small></div>
        <h4>Trên Sarae</h4>
        <CheckList checks={report.server.checks}/>
        <h4>Trên máy Agent / OBS</h4>
        {report.agentError ? <p className="orp-error"><CircleAlert size={14}/>{report.agentError}</p> : <CheckList checks={report.agent?.checks ?? []}/>}
      </>}
    </div>
  </Card>
}

function CheckList({checks}:{checks:ReadinessCheck[]}){
  return <ul className="orp-list">{checks.map((c, i)=><li key={c.code + i} className={'is-' + c.level}>
    {c.level === 'ok' ? <CircleCheck size={15}/> : c.level === 'warning' ? <TriangleAlert size={15}/> : <CircleAlert size={15}/>}
    <span>{c.message}</span><code>{c.code}</code>
  </li>)}</ul>
}

const MARK = {ok:'✅', warning:'⚠️', error:'❌'} as const

/** A plain report the operator can keep or send (no secrets: codes, messages, versions). */
function markdown(r:Report){
  const lines = [`# Kiểm tra OBS Overlay — ${r.server.session_name} (#${r.server.session_id})`, '',
    `- Thời điểm: ${new Date(r.checkedAt).toLocaleString('vi-VN')}`, `- Kết quả: ${r.server.ok && r.agent?.ok && !r.agentError ? 'SẴN SÀNG' : 'CHƯA SẴN SÀNG'}`,
    `- Phiên bản timeline ACTIVE: ${r.server.active_version ?? 'chưa có'} (revision ${r.server.snapshot_revision ?? '—'})`, `- Agent: ${r.agentId ?? 'chưa chọn'}`, '',
    '## Trên Sarae', '', ...r.server.checks.map(c=>`- ${MARK[c.level]} ${c.message} \`${c.code}\``), '', '## Trên máy Agent / OBS', '',
    ...(r.agentError ? [`- ❌ ${r.agentError}`] : (r.agent?.checks ?? []).map(c=>`- ${MARK[c.level]} ${c.message} \`${c.code}\``)), '',
    '> TikTok Viewer Preview chỉ mô phỏng gần đúng; kiểm tra cuối cùng trên điện thoại khi LIVE thử.', '']
  return lines.join('\n')
}

function download(name:string, text:string, type:string){
  const url = URL.createObjectURL(new Blob([text], {type}))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  window.setTimeout(()=>URL.revokeObjectURL(url), 1000)
}
