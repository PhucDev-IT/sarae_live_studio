import { useState, type FormEvent } from 'react'
import { AlertTriangle, KeyRound, Layers3, LogOut, RefreshCw, ShieldAlert, ShieldCheck, X } from 'lucide-react'
import { studioClient, type AccountStatus } from '../../services/studio'
import { ErrorNotice, Modal, errorMessage } from './ui'
import { APP_VERSION } from '../../app/version'

const activationErrors: Record<string, string> = {
  ACTIVATION_KEY_INVALID: 'Mã kích hoạt không đúng. Kiểm tra lại mã Sarae đã gửi cho bạn (dạng SARAE-ACT-…).',
  ACTIVATION_KEY_USED: 'Mã kích hoạt này đã được dùng. Hãy xin Sarae một mã mới.',
  AGENT_LIMIT_REACHED: 'Tài khoản đã kích hoạt đủ số máy cho phép. Gỡ một máy cũ hoặc liên hệ Sarae để thêm máy.',
  LICENSE_EXPIRED: 'License Sarae đã hết hạn. Liên hệ Sarae để gia hạn.',
  SERVER_UNREACHABLE: 'Không kết nối được server Sarae. Kiểm tra mạng rồi thử lại.',
  SERVER_URL_INVALID: 'Địa chỉ server Sarae (SARAE_STUDIO_SERVER_URL) không hợp lệ: phải dùng https://.',
  AGENT_NOT_ACTIVATED: 'Máy này chưa được kích hoạt. Hãy nhập mã kích hoạt.',
}

function accountError(cause: unknown): string {
  if (cause && typeof cause === 'object' && 'code' in cause) {
    const code = String(cause.code)
    // RATE_LIMITED carries the wait time in the Core's message.
    if (code === 'RATE_LIMITED' && 'message' in cause) return String(cause.message)
    if (activationErrors[code]) return activationErrors[code]
  }
  return errorMessage(cause)
}

export function formatDateTime(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('vi-VN', { dateStyle: 'short', timeStyle: 'short' })
}

function formatDate(value: string | null): string {
  if (!value) return 'Không thời hạn'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('vi-VN', { dateStyle: 'long' })
}

const stateLabels: Record<AccountStatus['state'], string> = {
  active: 'Đang hoạt động', offline: 'Mất kết nối server', locked: 'Đang khóa', unactivated: 'Chưa kích hoạt',
}

/** Full-screen gate while this machine is not activated or its license is locked (and no LIVE is playing). */
export function ActivationScreen({ account, onChange }: { account: AccountStatus; onChange: (status: AccountStatus) => void }) {
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState<'' | 'activate' | 'check' | 'forget'>('')
  const [error, setError] = useState('')
  const locked = account.state === 'locked' && account.activated
  const run = async (kind: 'activate' | 'check' | 'forget', operation: () => Promise<AccountStatus>) => {
    setBusy(kind)
    setError('')
    try {
      const next = await operation()
      onChange(next)
      if (kind === 'activate') setKey('')
      if (kind === 'check' && !next.licensed && next.last_error) setError(next.last_error.message)
    } catch (cause) {
      setError(accountError(cause))
    } finally {
      setBusy('')
    }
  }
  const submit = (event: FormEvent) => {
    event.preventDefault()
    const value = key.trim()
    if (!value) { setError('Hãy nhập mã kích hoạt.'); return }
    void run('activate', () => studioClient.accountActivate(value))
  }
  return <div className="studio-activation">
    <section className="studio-activation-card studio-card" aria-labelledby="studio-activation-title">
      <div className="studio-brand"><div className="studio-brand-mark"><Layers3 size={25} strokeWidth={1.7} /></div><div><strong>Sarae</strong><span>LIVE STUDIO</span></div></div>
      {locked ? <>
        <h1 id="studio-activation-title"><ShieldAlert size={22} />Studio đang bị khóa</h1>
        <p className="studio-muted">{account.message ?? 'License của máy này chưa hợp lệ.'}</p>
        {account.email && <p className="studio-activation-meta">Tài khoản <strong>{account.email}</strong>{account.expires_at ? ` · hết hạn ${formatDate(account.expires_at)}` : ''}</p>}
        <div className="studio-activation-actions">
          <button className="studio-button primary" disabled={!!busy} onClick={() => void run('check', studioClient.accountCheck)}><RefreshCw size={16} className={busy === 'check' ? 'studio-spin' : ''} />{busy === 'check' ? 'Đang kiểm tra…' : 'Kiểm tra lại'}</button>
          <button className="studio-button danger" disabled={!!busy} onClick={() => void run('forget', studioClient.accountDeactivate)}><LogOut size={16} />Gỡ kích hoạt máy này</button>
        </div>
        <p className="studio-muted studio-activation-hint">Đã gia hạn hoặc có mã kích hoạt mới? Nhập mã bên dưới.</p>
      </> : <>
        <h1 id="studio-activation-title"><KeyRound size={22} />Kích hoạt máy này</h1>
        <p className="studio-muted">Dán mã kích hoạt Sarae đã gửi cho bạn. Mỗi mã chỉ dùng được một lần, cho một máy.</p>
        {account.reason && account.message && <ErrorNotice message={account.message} />}
      </>}
      <form className="studio-activation-form" onSubmit={submit}>
        <label className="studio-field">Mã kích hoạt<input value={key} autoFocus={!locked} autoComplete="off" spellCheck={false} placeholder="SARAE-ACT-…" maxLength={200} disabled={!!busy} onChange={event => setKey(event.target.value)} /></label>
        <button className={`studio-button${locked ? '' : ' primary'}`} type="submit" disabled={!!busy || !key.trim()}><KeyRound size={16} />{busy === 'activate' ? 'Đang kích hoạt…' : 'Kích hoạt'}</button>
      </form>
      {error && <ErrorNotice message={error} />}
      <p className="studio-activation-server">Tên máy: {account.device_name} · {serverKind(account.server_url).local ? 'Server local' : 'Server mạng'}: <code>{account.server_url}</code> · v{APP_VERSION}</p>
    </section>
  </div>
}

/** Non-blocking notice while a LIVE keeps playing with an invalid license. */
/** Which Sarae server this Studio talks to: the gateway on this PC (127.0.0.1) or the network one (Cloudflare). */
export function serverKind(url: string): { local: boolean; host: string } {
  try {
    const parts = new URL(url)
    const host = parts.hostname.replace(/^\[|\]$/g, '')
    return { local: ['localhost', '127.0.0.1', '::1'].includes(host), host: parts.host }
  } catch {
    return { local: false, host: url }
  }
}

const serverStates: Record<AccountStatus['state'], string> = {
  active: 'trực tuyến', offline: 'mất kết nối', locked: 'license bị khóa', unactivated: 'chưa kích hoạt',
}

/** Top bar chip: server kind and reachability; dev bypass is called out so it is never mistaken for a license. */
export function ServerBadge({ account }: { account: AccountStatus | null }) {
  if (!account) return null
  if (account.dev_bypass) {
    return <span className="studio-server-badge dev" title="SARAE_STUDIO_DEV_UNLICENSED=1: không kiểm tra license (chỉ dùng khi phát triển)."><span aria-hidden="true" />Chế độ dev · không license</span>
  }
  const { local, host } = serverKind(account.server_url)
  const tone = account.state === 'active' ? 'ok' : account.state === 'offline' ? 'warn' : 'error'
  const until = account.state === 'offline' && account.grace_until ? ` Vẫn dùng được tới ${formatDateTime(account.grace_until)}.` : ''
  const title = `${local ? 'Server trên máy này' : 'Server qua mạng (Cloudflare)'}: ${account.server_url} · ${serverStates[account.state]}.${until}`
  return <span className={`studio-server-badge ${tone}`} role="status" title={title}><span aria-hidden="true" />{local ? 'Server local' : 'Server mạng'} · {host}{account.state === 'active' ? '' : ` · ${serverStates[account.state]}`}</span>
}

export function LicenseBanner({ account }: { account: AccountStatus }) {
  return <div className="studio-license-banner" role="alert"><AlertTriangle size={18} /><div><strong>License chưa hợp lệ — phiên LIVE đang phát vẫn chạy tiếp</strong><p>{account.message ?? 'License của máy này chưa hợp lệ.'} Khi phiên kết thúc, Studio sẽ khóa cho đến khi license hợp lệ trở lại; chưa bắt đầu được phiên LIVE mới.</p></div></div>
}

/** The account section of the top bar: who this machine belongs to and its license. */
export function AccountCard({ account, onChange, onClose, onNotice }: { account: AccountStatus; onChange: (status: AccountStatus) => void; onClose: () => void; onNotice: (message: string) => void }) {
  const [busy, setBusy] = useState<'' | 'check' | 'forget'>('')
  const [error, setError] = useState('')
  const [confirming, setConfirming] = useState(false)
  const check = async () => {
    setBusy('check'); setError('')
    try {
      const next = await studioClient.accountCheck()
      onChange(next)
      if (next.last_error) setError(next.last_error.message)
      else if (next.licensed) onNotice('License hợp lệ.')
    } catch (cause) { setError(accountError(cause)) } finally { setBusy('') }
  }
  const forget = async () => {
    setBusy('forget'); setError('')
    try { onChange(await studioClient.accountDeactivate()); setConfirming(false) } catch (cause) { setError(accountError(cause)) } finally { setBusy('') }
  }
  return <section className="studio-storage studio-account studio-card" aria-label="Tài khoản và license">
    <div className="studio-storage-heading">{account.licensed ? <ShieldCheck size={19} /> : <ShieldAlert size={19} />}<h2>Tài khoản Sarae</h2><span className={`studio-account-state ${account.dev_bypass ? 'dev' : account.state}`}>{account.dev_bypass ? 'Dev · bỏ qua license' : stateLabels[account.state]}</span><button className="studio-button ghost icon-only" aria-label="Ẩn thông tin tài khoản" onClick={onClose}><X size={17} /></button></div>
    {account.activated ? <dl className="studio-account-grid">
      <div><dt>Email</dt><dd>{account.email ?? '—'}</dd></div>
      <div><dt>Hết hạn</dt><dd>{formatDate(account.expires_at)}</dd></div>
      <div><dt>Tên máy</dt><dd>{account.device_name}</dd></div>
      <div><dt>Kiểm tra gần nhất</dt><dd>{formatDateTime(account.checked_at)}</dd></div>
    </dl> : <p className="studio-muted">Máy này chưa kích hoạt.</p>}
    {account.state === 'offline' && account.grace_until && <p className="studio-version-warning">Chưa kết nối được server Sarae. Studio vẫn dùng được đến {formatDateTime(account.grace_until)}.</p>}
    {!account.licensed && account.message && <p className="studio-version-warning">{account.message}</p>}
    {error && <ErrorNotice message={error} />}
    <div className="studio-account-actions">
      <button className="studio-button" disabled={!!busy || !account.activated} onClick={() => void check()}><RefreshCw size={15} className={busy === 'check' ? 'studio-spin' : ''} />{busy === 'check' ? 'Đang kiểm tra…' : 'Kiểm tra lại'}</button>
      <button className="studio-button danger" disabled={!!busy || !account.activated} onClick={() => setConfirming(true)}><LogOut size={15} />Gỡ kích hoạt máy này</button>
      <small className="studio-muted">Server: <code>{account.server_url}</code></small>
    </div>
    {confirming && <Modal title="Gỡ kích hoạt máy này?" onClose={() => setConfirming(false)}>
      <div className="studio-form"><p>Studio sẽ quên thông tin kích hoạt trên máy này và hiện lại màn hình nhập mã. Mã kích hoạt cũ không dùng lại được; bạn cần mã mới từ Sarae.{account.live_running ? ' Phiên LIVE đang phát vẫn chạy tiếp đến khi kết thúc.' : ''}</p>
      <div className="studio-form-actions"><button className="studio-button" onClick={() => setConfirming(false)}>Giữ kích hoạt</button><button className="studio-button danger-solid" disabled={!!busy} onClick={() => void forget()}><LogOut size={15} />{busy === 'forget' ? 'Đang gỡ…' : 'Gỡ kích hoạt'}</button></div></div>
    </Modal>}
  </section>
}
