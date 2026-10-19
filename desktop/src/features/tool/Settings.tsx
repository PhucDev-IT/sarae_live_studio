import { useEffect, useState } from 'react'
import { open } from '@tauri-apps/plugin-dialog'
import { ImagePlus, LogOut, Settings2, X } from 'lucide-react'
import { useApp } from '../../app/store'
import { ActionButton, Input, Toggle } from '../../app/ui'
import { call } from '../../services/core'
import type { MethodParams, TemplateInfo } from '../../types/ipc'

/** Rarely changed, folded by default: startup behaviour, OBS connection, CAPTCHA images, this device. */
export function Settings() {
  const { state, run, refresh, refreshConfig } = useApp()
  const config = state.config
  const agent = state.status?.agent
  const [host, setHost] = useState('')
  const [port, setPort] = useState(4455)
  const [password, setPassword] = useState('')
  const [templates, setTemplates] = useState<TemplateInfo[]>([])
  const templateCount = state.status?.monitor.templates

  useEffect(() => {
    if (!config) return
    setHost(config.obs.host)
    setPort(config.obs.port)
  }, [config])

  useEffect(() => {
    call('monitor.templates.list').then((r) => setTemplates(r.templates)).catch(() => undefined)
  }, [templateCount])

  if (!config || !agent) return null
  const workerActive = agent.state !== 'stopped' && agent.state !== 'error'
  const setConfig = (patch: MethodParams<'config.set'>) =>
    run(() => call('config.set', patch)).then(refreshConfig).then(refresh)

  return (
    <details className="panel settings">
      <summary className="panel-title"><Settings2 size={15} />Cài đặt</summary>

      <div className="settings-group">
        <h3>Khi mở ứng dụng</h3>
        <Toggle checked={config.agent.autostart} label="Tự kết nối Cloud" onChange={(v) => void setConfig({ agent: { autostart: v } })} />
        <Toggle checked={config.obs.auto_connect} label="Tự kết nối OBS" onChange={(v) => void setConfig({ obs: { auto_connect: v } })} />
        <Toggle checked={config.monitor.autostart} label="Tự bật giám sát CAPTCHA" onChange={(v) => void setConfig({ monitor: { autostart: v } })} />
      </div>

      <div className="settings-group">
        <h3>OBS WebSocket</h3>
        <div className="inline">
          <Input value={host} onChange={(e) => setHost(e.target.value)} placeholder="127.0.0.1" spellCheck={false} />
          <Input type="number" min={1} max={65535} value={port} onChange={(e) => setPort(Number(e.target.value))} className="input input-num" />
        </div>
        <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="off"
          placeholder={config.obs.password_set ? 'Mật khẩu đã lưu (để trống = giữ nguyên)' : 'Mật khẩu (nếu OBS bật xác thực)'} />
        <ActionButton disabled={!host.trim()} onAction={async () => {
          await setConfig({ obs: { host: host.trim(), port }, ...(password ? { obs_password: password } : {}) })
          setPassword('')
          await run(() => call('obs.connect'), 'Đã kết nối OBS')
          await refresh()
        }}>Lưu &amp; kết nối</ActionButton>
      </div>

      <div className="settings-group">
        <h3>Ảnh mẫu CAPTCHA ({templates.length})</h3>
        {templates.map((t) => (
          <div key={t.name} className="inline chip-row">
            <span className="chip-name">{t.name}</span>
            <ActionButton variant="ghost" icon={<X size={14} />} aria-label={`Xoá ${t.name}`} onAction={async () => {
              await run(() => call('monitor.templates.remove', { name: t.name }), 'Đã xoá ảnh mẫu')
              await refresh()
            }} />
          </div>
        ))}
        <ActionButton variant="ghost" icon={<ImagePlus size={14} />} onAction={async () => {
          const picked = await open({ multiple: false, title: 'Chọn ảnh mẫu CAPTCHA', filters: [{ name: 'Ảnh', extensions: ['png', 'jpg', 'jpeg'] }] })
          if (typeof picked === 'string') {
            await run(() => call('monitor.templates.add', { path: picked }), 'Đã thêm ảnh mẫu')
            await refresh()
          }
        }}>Thêm ảnh mẫu</ActionButton>
      </div>

      {agent.activated && (
        <div className="settings-group">
          <h3>Thiết bị</h3>
          <p className="muted small selectable">{agent.activated_cloud_url ?? config.cloud.api_url}<br />{agent.agent_id}</p>
          <ActionButton variant="danger" icon={<LogOut size={14} />} disabled={workerActive} onAction={async () => {
            if (!window.confirm('Gỡ kích hoạt trên máy này? Cần mã kích hoạt mới để dùng lại.')) return
            await run(() => call('activation.deactivate'), 'Đã gỡ kích hoạt')
            await refresh()
          }}>Gỡ kích hoạt</ActionButton>
          {workerActive && <small className="muted">Tắt "Kết nối Cloud" trước khi gỡ kích hoạt.</small>}
        </div>
      )}
    </details>
  )
}
