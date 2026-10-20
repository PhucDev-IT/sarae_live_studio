import { useCallback, useEffect, useState } from 'react'
import { Alert, Badge, Button, Card, ConfirmModal, EmptyState, Input, Modal, Table, useToast, type TableColumn } from './ui/core'
import { Ban, Check, KeyRound, MonitorPlay, MonitorX, Pencil, RefreshCw, X } from 'lucide-react'
import { apiRequest, errorText, type AgentCommand, type OwnerAgent } from './mediaApi'
import { agentState, useAgent } from './agentContext'
import AndroidAgentPanel from './AndroidAgentPanel'
import { dateTime } from './liveSessionsApi'
import './agent-picker.css'

const KIND:Record<string,string> = {
  product_play:'Phát sản phẩm', playback_pause:'Tạm dừng', playback_resume:'Tiếp tục', playback_next:'Video tiếp', playback_previous:'Video trước',
  playback_select:'Chọn video', playback_seek:'Tua video', playback_stop:'Dừng phát', playback_cancel_pending:'Hủy hẹn chuyển', obs_configure:'Cấu hình OBS',
  obs_inputs:'Đọc source OBS', media_rescan:'Quét lại thư mục', monitor_start:'Bật CAPTCHA Monitor', monitor_stop:'Dừng CAPTCHA Monitor',
  monitor_config:'Cấu hình CAPTCHA Monitor', tts_speak:'Phát giọng nói', local_playlist_start:'Phát thử playlist',
  android_play:'Android · phát', android_switch:'Android · chuyển video', android_pause:'Android · tạm dừng',
  android_resume:'Android · tiếp tục', android_next:'Android · video tiếp', android_previous:'Android · video trước',
  android_stop:'Android · dừng', android_queue_set:'Android · cập nhật playlist', android_rotation_set:'Android · auto rotation',
  android_mock_live_start:'Android · bật Mock LIVE', android_mock_live_stop:'Android · tắt Mock LIVE',
}
const STATUS:Record<AgentCommand['status'],{label:string;tone:'default'|'info'|'success'|'danger'|'warning'}> = {
  PENDING:{label:'Chờ Agent nhận', tone:'default'}, RUNNING:{label:'Đang chạy', tone:'info'}, SUCCESS:{label:'Thành công', tone:'success'},
  FAILED:{label:'Lỗi', tone:'danger'}, TIMEOUT:{label:'Hết hạn', tone:'warning'}, CANCELLED:{label:'Đã hủy', tone:'default'},
}

/** The OWNER's Agents: state of each device, which one this tab drives, and the commands sent to it. */
export default function AgentCloudStatus(){
  const toast = useToast()
  const {agents, agent:current, select, refresh, loaded} = useAgent()
  const [commands,setCommands] = useState<AgentCommand[]>([])
  const [renaming,setRenaming] = useState<OwnerAgent|null>(null)
  const [name,setName] = useState('')
  const [revoking,setRevoking] = useState<OwnerAgent|null>(null)
  const [busy,setBusy] = useState(false)
  const currentId = current?.id

  const loadCommands = useCallback(async ()=>{
    if (!currentId) {setCommands([]);return}
    try{setCommands((await apiRequest<{commands:AgentCommand[]}>(`/agents/${encodeURIComponent(currentId)}/commands`)).commands)}catch{/* keep the last list */}
  },[currentId])
  useEffect(()=>{
    void loadCommands()
    const timer = window.setInterval(()=>void loadCommands(), 4000)
    return ()=>window.clearInterval(timer)
  },[loadCommands])

  const run = async (job:()=>Promise<unknown>, success:string) => {
    setBusy(true)
    try{await job();toast.push({tone:'success', title:success});await refresh();await loadCommands()}
    catch(e){toast.push({tone:'danger', title:'Không thực hiện được', description:errorText(e, '')})}
    finally{setBusy(false)}
  }

  const columns:TableColumn<AgentCommand>[] = [
    {key:'kind', header:'Lệnh', render:c=><strong>{KIND[c.kind] ?? c.kind}</strong>},
    {key:'status', header:'Trạng thái', width:150, render:c=><span className="ag-status"><Badge tone={STATUS[c.status].tone}>{STATUS[c.status].label}</Badge>{c.late_ack && <small title="Agent phản hồi sau khi lệnh đã hết hạn">phản hồi trễ</small>}</span>},
    {key:'created_at', header:'Gửi lúc', width:170, render:c=>dateTime(c.created_at)},
    {key:'error', header:'Ghi chú', render:c=><small className="ag-note">{c.error ?? (c.attempts > 1 ? `Giao lại ${c.attempts} lần` : '')}</small>},
    {key:'actions', header:'', width:60, align:'right', render:c=>c.status === 'PENDING' ? <button type="button" className="media-icon-btn" title="Hủy lệnh chưa được Agent nhận" aria-label="Hủy lệnh"
      onClick={()=>void run(()=>apiRequest(`/agents/${encodeURIComponent(c.agent_id)}/commands/${encodeURIComponent(c.command_id)}/cancel`, {method:'POST'}), 'Đã hủy lệnh')}><X size={15}/></button> : null},
  ]

  if (!loaded) return null
  return <div className="ag-page">
    <Alert tone="info" title="Thêm Agent Windows hoặc Android">
      Quản trị nền tảng cấp mã kích hoạt theo License của bạn. Nhập mã trong Sarae Agent tương ứng để kết nối thiết bị.
      Video MP4 và cấu hình OBS luôn ở trên máy; máy chủ chỉ giữ tên file, thời lượng và ảnh thu nhỏ.
    </Alert>
    {!agents.length ? <Card><EmptyState icon={<MonitorX size={32}/>} title="Chưa có Agent" description="Sau khi kích hoạt, thiết bị sẽ xuất hiện ở đây trong vài giây."/></Card>
      : <div className="ag-grid">{agents.map(a=><Card key={a.id} className={'ag-card' + (a.id === currentId ? ' is-current' : '')} padding={false}>
        <div className="ag-card-head">
          <span className={'agent-dot' + (a.online ? ' is-online' : '')}/>
          <div><strong>{a.hostname}</strong><small>{agentState(a)}</small></div>
          {a.id === currentId && <Badge tone="primary"><Check size={12}/>Tab này</Badge>}
        </div>
        <dl className="ag-facts">
          <div><dt>Kết nối</dt><dd>{a.online ? 'Online' : 'Offline'}</dd></div>
          <div><dt>Nền tảng</dt><dd>{a.platform === 'ANDROID' ? 'Android' : 'Windows'}</dd></div>
          <div><dt>OBS</dt><dd>{a.platform === 'ANDROID' ? 'Không áp dụng' : a.obs_connected ? 'Đã kết nối' : '—'}</dd></div>
          <div><dt>Đang phát</dt><dd>{a.platform === 'ANDROID' ? (a.playback_state ?? '—') : a.playing ? (a.session_id ? `Phiên #${a.session_id}` : 'Có') : 'Không'}</dd></div>
          {a.platform === 'ANDROID' && <div className="ag-wide"><dt>Video / Mock LIVE</dt><dd>{a.playback_error ? `Lỗi: ${a.playback_error}` : `${a.playback_media_id?.slice(0,12) ?? '—'} · ${a.mock_live ? 'Mock ON' : 'Mock OFF'}`}</dd></div>}
          <div><dt>CAPTCHA</dt><dd>{a.platform === 'ANDROID' ? 'Không áp dụng' : a.monitor_running ? 'Đang giám sát' : 'Tắt'}</dd></div>
          <div className="ag-wide"><dt>Liên lạc gần nhất</dt><dd>{a.last_seen ? dateTime(a.last_seen) : 'Chưa từng kết nối'}</dd></div>
        </dl>
        <div className="ag-actions">
          {a.id !== currentId && <Button size="sm" leftIcon={<MonitorPlay size={14}/>} onClick={()=>select(a.id)}>Dùng trong tab này</Button>}
          {a.platform !== 'ANDROID' && <Button size="sm" variant="ghost" leftIcon={<Pencil size={14}/>} onClick={()=>{setName(a.hostname);setRenaming(a)}}>Đổi tên</Button>}
          {a.platform !== 'ANDROID' && <Button size="sm" variant="ghost" leftIcon={<Ban size={14}/>} disabled={a.playing} title={a.playing ? 'Kết thúc phiên LIVE trên máy này trước' : undefined} onClick={()=>setRevoking(a)}>Thu hồi</Button>}
        </div>
      </Card>)}</div>}

    {current?.platform === 'ANDROID' && <AndroidAgentPanel agent={current}/>}
    {current && <Card padding={false} title={<span className="ag-title"><KeyRound size={17}/>Lệnh gửi tới {current.hostname}</span>}
      description="30 lệnh gần nhất. Lệnh có tác động (phát, chuyển, tua…) không tự gửi lại khi hết hạn để tránh phát hai lần."
      actions={<Button size="sm" variant="ghost" aria-label="Tải lại" onClick={()=>void loadCommands()}><RefreshCw size={15}/></Button>}>
      {commands.length ? <Table<AgentCommand> columns={columns} data={commands}/> : <p className="ag-empty">Chưa có lệnh nào.</p>}
    </Card>}

    <Modal open={!!renaming} title="Đổi tên máy" size="sm" onClose={()=>!busy && setRenaming(null)} footer={<>
      <Button variant="ghost" disabled={busy} onClick={()=>setRenaming(null)}>Hủy</Button>
      <Button loading={busy} disabled={!name.trim()} onClick={()=>{const a = renaming!;void run(async ()=>{await apiRequest(`/identity/agents/${encodeURIComponent(a.id)}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({hostname:name.trim()})});setRenaming(null)}, 'Đã đổi tên máy')}}>Lưu</Button>
    </>}>
      <Input label="Tên hiển thị" autoFocus maxLength={120} value={name} onChange={e=>setName(e.target.value)} hint="VD: PC phòng LIVE 1"/>
    </Modal>

    <ConfirmModal open={!!revoking} loading={busy} tone="danger" title={`Thu hồi “${revoking?.hostname ?? ''}”?`}
      description="Máy này sẽ không nhận lệnh nữa và cần mã kích hoạt mới để dùng lại. Video, playlist và phiên đã gắn với máy này không chuyển sang máy khác."
      confirmLabel="Thu hồi" onClose={()=>setRevoking(null)}
      onConfirm={()=>{const a = revoking!;setRevoking(null);void run(()=>apiRequest(`/identity/agents/${encodeURIComponent(a.id)}`, {method:'DELETE'}), `Đã thu hồi ${a.hostname}`)}}/>
  </div>
}
