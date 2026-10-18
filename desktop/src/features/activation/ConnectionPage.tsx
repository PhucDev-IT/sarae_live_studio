import { useEffect, useState } from 'react'
import { Cloud, KeyRound, LogOut, Save, ShieldCheck } from 'lucide-react'
import { useApp } from '../../app/store'
import { ActionButton, Badge, Card, ErrorLine, Field, Input, Toggle, formatDateTime } from '../../app/ui'
import { call } from '../../services/core'

const ACTIVATION_HINTS: Record<string, string> = {
  ACTIVATION_KEY_INVALID: 'Mã không đúng. Kiểm tra lại mã do quản trị viên cấp.',
  ACTIVATION_KEY_USED: 'Mã này đã được dùng cho một máy khác. Mỗi mã chỉ kích hoạt một lần.',
  LICENSE_EXPIRED: 'License của tài khoản đã hết hạn hoặc bị khóa. Liên hệ quản trị viên.',
  AGENT_LIMIT_REACHED: 'Tài khoản đã đạt số máy tối đa của license.',
  CLOUD_UNREACHABLE: 'Không kết nối được Cloud. Kiểm tra Internet và địa chỉ Cloud URL.',
  CLOUD_TIMEOUT: 'Cloud phản hồi quá chậm. Thử lại sau.',
}

export function ConnectionPage() {
  const { state, run, refresh, refreshConfig, notify } = useApp()
  const agent = state.status?.agent
  const config = state.config
  const workerActive = !!agent && agent.state !== 'stopped' && agent.state !== 'error'
  const [url, setUrl] = useState('')
  const [key, setKey] = useState('')
  const [activationError, setActivationError] = useState<{ code: string; message: string } | null>(null)

  const savedUrl = config?.cloud.api_url
  useEffect(() => {
    if (savedUrl !== undefined) setUrl(savedUrl)
  }, [savedUrl])

  const activate = async () => {
    setActivationError(null)
    try {
      await call('activation.activate', { activation_key: key.trim(), cloud_url: url.trim() })
      setKey('')
      notify('success', 'Kích hoạt thành công')
      await Promise.all([refresh(), refreshConfig()])
    } catch (error) {
      const err = error as { code: string; message: string }
      setActivationError({ code: err.code, message: ACTIVATION_HINTS[err.code] ?? err.message })
    }
  }

  return (
    <div className="grid grid-2">
      <Card title="Cloud API" icon={<Cloud size={16} />}>
        <Field label="Địa chỉ Cloud API" hint="Ví dụ https://app.sarae.vn/api — bắt buộc HTTPS (HTTP chỉ dùng cho localhost khi thử nghiệm).">
          <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…/api" disabled={workerActive} spellCheck={false} />
        </Field>
        {workerActive && <small className="muted">Dừng Agent trước khi đổi Cloud URL.</small>}
        <div className="row">
          <ActionButton
            icon={<Save size={16} />}
            disabled={workerActive || !url.trim() || url.trim() === config?.cloud.api_url}
            onAction={() => run(() => call('config.set', { cloud: { api_url: url.trim() } }), 'Đã lưu Cloud URL').then(refreshConfig)}
          >Lưu</ActionButton>
        </div>
        {config && (
          <Toggle
            checked={config.agent.autostart}
            label="Tự bắt đầu Agent khi mở ứng dụng"
            onChange={(value) => void run(() => call('config.set', { agent: { autostart: value } })).then(refreshConfig)}
          />
        )}
      </Card>

      <Card title="Kích hoạt thiết bị" icon={<KeyRound size={16} />}>
        {agent?.activated ? (
          <div className="stack-sm">
            <div className="activated">
              <ShieldCheck size={28} />
              <div>
                <Badge tone="ok">Đã kích hoạt</Badge>
                <p><span className="muted">Agent ID</span> <code className="selectable">{agent.agent_id}</code></p>
                <p><span className="muted">Máy</span> {agent.hostname} · <span className="muted">kích hoạt</span> {formatDateTime(agent.activated_at)}</p>
                {agent.activated_cloud_url && <p><span className="muted">Cloud</span> {agent.activated_cloud_url}</p>}
              </div>
            </div>
            <p className="muted small">Thông tin xác thực thiết bị được mã hoá bằng Windows DPAPI theo tài khoản Windows hiện tại và không hiển thị lại.</p>
            <ActionButton
              variant="danger"
              icon={<LogOut size={16} />}
              disabled={workerActive}
              onAction={async () => {
                if (!window.confirm('Gỡ kích hoạt trên máy này? Cần mã kích hoạt mới để dùng lại. Thiết bị trên Cloud chỉ bị thu hồi khi OWNER/Quản trị thao tác trên Web.')) return
                await run(() => call('activation.deactivate'), 'Đã gỡ kích hoạt cục bộ')
                await refresh()
              }}
            >Gỡ kích hoạt trên máy này</ActionButton>
            {workerActive && <small className="muted">Dừng Agent trước khi gỡ kích hoạt.</small>}
          </div>
        ) : (
          <div className="stack-sm">
            <Field label="Mã kích hoạt dùng một lần" hint="Mã dạng SARAE-ACT-… do SUPER_ADMIN cấp cho tài khoản của bạn.">
              <Input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder="SARAE-ACT-…" autoComplete="off" spellCheck={false} />
            </Field>
            <ErrorLine error={activationError} />
            <ErrorLine error={agent?.credential_error} />
            <div className="row">
              <ActionButton variant="primary" icon={<KeyRound size={16} />} disabled={!key.trim() || !url.trim()} onAction={activate}>Kích hoạt</ActionButton>
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}
