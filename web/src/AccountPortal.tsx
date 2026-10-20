import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Alert, Badge, Button, Card, ConfirmModal, PasswordInput, SkeletonCard, useToast } from './ui/core'
import { CalendarClock, KeyRound, LogOut, MonitorPlay, ShieldCheck, ShieldAlert, UserRound } from 'lucide-react'
import { apiRequest, errorText } from './mediaApi'
import { agentState, useAgent } from './agentContext'
import { dateTime } from './liveSessionsApi'
import './account.css'

type Account = {
  id:string; email:string; role:'OWNER'|'SUPER_ADMIN'
  license:{max_agents:number; expires_at:string|null; enabled:boolean; valid:boolean}|null
  agents_used:number; sessions:number; session_expires_at:string|null
}

const DAY = 24 * 3600 * 1000
const daysLeft = (iso:string|null) => iso ? Math.ceil((new Date(iso).getTime() - Date.now()) / DAY) : null

/** The OWNER's own account: license and Agent slots (set by the platform admin), devices, password and sign-ins. */
export default function AccountPortal(){
  const toast = useToast()
  const {agents} = useAgent()
  const [account,setAccount] = useState<Account|null>(null)
  const [error,setError] = useState('')
  const [current,setCurrent] = useState('')
  const [next,setNext] = useState('')
  const [repeat,setRepeat] = useState('')
  const [busy,setBusy] = useState(false)
  const [confirmRevoke,setConfirmRevoke] = useState(false)

  const load = useCallback(async ()=>{
    try{setAccount(await apiRequest<Account>('/identity/account'));setError('')}
    catch(e){setError(errorText(e, 'Không tải được thông tin tài khoản'))}
  },[])
  useEffect(()=>{void load()},[load])

  const mismatch = repeat !== '' && repeat !== next
  const tooShort = next !== '' && next.length < 8
  const changePassword = async (event:FormEvent) => {
    event.preventDefault()
    if (mismatch || tooShort || !current) return
    setBusy(true)
    try{
      await apiRequest('/identity/password', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({current_password:current, new_password:next})})
      setCurrent('');setNext('');setRepeat('')
      toast.push({tone:'success', title:'Đã đổi mật khẩu', description:'Các thiết bị khác đã được đăng xuất.'})
      await load()
    }catch(e){toast.push({tone:'danger', title:'Không đổi được mật khẩu', description:errorText(e, '')})}
    finally{setBusy(false)}
  }
  const revokeOthers = async () => {
    setConfirmRevoke(false);setBusy(true)
    try{
      const {revoked} = await apiRequest<{revoked:number}>('/identity/sessions/revoke-others', {method:'POST'})
      toast.push({tone:'success', title:revoked ? `Đã đăng xuất ${revoked} phiên khác` : 'Không có phiên nào khác'})
      await load()
    }catch(e){toast.push({tone:'danger', title:'Không thực hiện được', description:errorText(e, '')})}
    finally{setBusy(false)}
  }

  if (error) return <Alert tone="danger" title="Không tải được tài khoản">{error}</Alert>
  if (!account) return <div className="acc-grid"><SkeletonCard lines={3}/><SkeletonCard lines={3}/></div>

  const lic = account.license
  const left = daysLeft(lic?.expires_at ?? null)
  const max = lic?.max_agents ?? 0
  const used = account.agents_used
  const usage = max ? Math.min(100, Math.round(used / max * 100)) : 0
  const licenseState = !lic ? {label:'Chưa có license', tone:'danger' as const}
    : !lic.enabled ? {label:'License bị khóa', tone:'danger' as const}
    : !lic.valid ? {label:'License hết hạn', tone:'danger' as const}
    : left != null && left <= 7 ? {label:`Còn ${left} ngày`, tone:'warning' as const}
    : {label:'Đang hiệu lực', tone:'success' as const}

  return <div className="acc-page">
    <section className="acc-hero">
      <span className="acc-avatar">{account.email.slice(0, 1).toUpperCase()}</span>
      <div className="acc-hero-text">
        <small>TÀI KHOẢN CHỦ SHOP</small>
        <h2>{account.email}</h2>
        <div className="acc-hero-tags">
          <Badge tone="primary"><UserRound size={12}/>OWNER</Badge>
          <Badge tone={licenseState.tone} dot>{licenseState.label}</Badge>
        </div>
      </div>
    </section>

    {lic && (!lic.valid || (left != null && left <= 7)) && <Alert tone={lic.valid ? 'warning' : 'danger'} title={lic.valid ? 'License sắp hết hạn' : 'License không còn hiệu lực'}>
      {lic.valid ? `License hết hạn vào ${dateTime(lic.expires_at)}.` : 'Các máy Windows không nhận lệnh và không thể bắt đầu LIVE.'} Liên hệ quản trị nền tảng để gia hạn.
    </Alert>}

    <div className="acc-stats">
      <div className="acc-stat">
        <span className="acc-stat-icon violet"><MonitorPlay size={19}/></span>
        <div><small>Máy Windows</small><strong>{used}<em> / {max}</em></strong>
          <div className="acc-meter" role="meter" aria-valuemin={0} aria-valuemax={max} aria-valuenow={used} aria-label="Số máy đã dùng"><span style={{width:`${usage}%`}} className={usage >= 100 ? 'is-full' : ''}/></div>
          <em>{max - used > 0 ? `Còn ${max - used} chỗ kích hoạt` : 'Đã dùng hết số máy'}</em></div>
      </div>
      <div className="acc-stat">
        <span className="acc-stat-icon mint"><CalendarClock size={19}/></span>
        <div><small>Hạn license</small><strong>{lic?.expires_at ? new Date(lic.expires_at).toLocaleDateString('vi-VN') : 'Không thời hạn'}</strong>
          <em>{left == null ? 'Dùng không giới hạn thời gian' : left > 0 ? `Còn ${left} ngày` : 'Đã hết hạn'}</em></div>
      </div>
      <div className="acc-stat">
        <span className="acc-stat-icon blue"><KeyRound size={19}/></span>
        <div><small>Phiên đăng nhập</small><strong>{account.sessions}</strong>
          <em>Phiên này hết hạn {dateTime(account.session_expires_at)}</em></div>
      </div>
    </div>

    <div className="acc-grid">
      <Card title={<span className="acc-title"><ShieldCheck size={17}/>License</span>} description="Do quản trị nền tảng cấp. Liên hệ quản trị để thêm máy hoặc gia hạn.">
        <dl className="acc-rows">
          <div><dt>Trạng thái</dt><dd><Badge tone={licenseState.tone}>{licenseState.label}</Badge></dd></div>
          <div><dt>Số máy tối đa</dt><dd>{max}</dd></div>
          <div><dt>Đã kích hoạt</dt><dd>{used} máy</dd></div>
          <div><dt>Hết hạn</dt><dd>{lic?.expires_at ? dateTime(lic.expires_at) : 'Không thời hạn'}</dd></div>
        </dl>
      </Card>

      <Card title={<span className="acc-title"><MonitorPlay size={17}/>Máy Windows của bạn</span>} description="Mỗi máy chạy Sarae Agent cạnh OBS; video luôn nằm trên máy."
        actions={<a className="acc-link" href="#/agents">Quản lý →</a>}>
        {!agents.length ? <p className="acc-muted">Chưa có máy nào. Nhập mã kích hoạt do quản trị cấp trong ứng dụng Sarae Agent.</p>
          : <ul className="acc-agents">{agents.map(a=><li key={a.id}>
            <span className={'agent-dot' + (a.online ? ' is-online' : '')}/>
            <span><strong>{a.hostname}</strong><small>{agentState(a)}</small></span>
            <small>{a.last_seen ? dateTime(a.last_seen) : 'Chưa kết nối'}</small>
          </li>)}</ul>}
      </Card>

      <Card className="acc-security" title={<span className="acc-title"><KeyRound size={17}/>Đổi mật khẩu</span>} description="Sau khi đổi, mọi thiết bị khác sẽ bị đăng xuất.">
        <form className="acc-form" onSubmit={e=>void changePassword(e)}>
          <PasswordInput label="Mật khẩu hiện tại" autoComplete="current-password" required value={current} onChange={e=>setCurrent(e.target.value)}/>
          <PasswordInput label="Mật khẩu mới" autoComplete="new-password" required value={next} onChange={e=>setNext(e.target.value)}
            error={tooShort ? 'Tối thiểu 8 ký tự' : undefined}/>
          <PasswordInput label="Nhập lại mật khẩu mới" autoComplete="new-password" required value={repeat} onChange={e=>setRepeat(e.target.value)}
            error={mismatch ? 'Không khớp với mật khẩu mới' : undefined}/>
          <Button type="submit" loading={busy} disabled={!current || !next || mismatch || tooShort}>Đổi mật khẩu</Button>
        </form>
      </Card>

      <Card title={<span className="acc-title"><ShieldAlert size={17}/>Phiên đăng nhập</span>} description="Trình duyệt / thiết bị đang đăng nhập tài khoản này.">
        <div className="acc-sessions">
          <strong>{account.sessions}</strong><span>phiên đang hoạt động (gồm phiên này)</span>
        </div>
        <p className="acc-muted">Nghi ngờ có người khác đăng nhập? Đăng xuất các thiết bị khác rồi đổi mật khẩu.</p>
        <Button variant="outline" leftIcon={<LogOut size={15}/>} disabled={busy || account.sessions <= 1} onClick={()=>setConfirmRevoke(true)}>Đăng xuất thiết bị khác</Button>
      </Card>
    </div>

    <ConfirmModal open={confirmRevoke} loading={busy} title="Đăng xuất các thiết bị khác?" confirmLabel="Đăng xuất"
      description="Mọi trình duyệt khác đang đăng nhập tài khoản này sẽ phải đăng nhập lại. Tab hiện tại vẫn giữ nguyên."
      onClose={()=>setConfirmRevoke(false)} onConfirm={()=>void revokeOthers()}/>
  </div>
}
