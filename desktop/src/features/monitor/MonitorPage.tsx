import { useEffect, useState } from 'react'
import { open } from '@tauri-apps/plugin-dialog'
import { History, ImagePlus, Play, Save, ScanEye, Settings2, Square, Trash2 } from 'lucide-react'
import { useApp } from '../../app/store'
import { ActionButton, Badge, Card, Empty, Field, Input, Stat, Toggle, formatBytes, formatTime, type Tone } from '../../app/ui'
import { call } from '../../services/core'
import type { MonitorConfig, TemplateInfo } from '../../types/ipc'

export const MONITOR_LABEL: Record<string, [string, Tone]> = {
  idle: ['Chưa bật', 'muted'],
  starting: ['Đang khởi động', 'info'],
  watching: ['Đang giám sát', 'ok'],
  captcha_detected: ['Phát hiện CAPTCHA', 'danger'],
  window_unavailable: ['Không thấy cửa sổ TikTok', 'warn'],
  template_missing_or_invalid: ['Ảnh mẫu không hợp lệ', 'warn'],
  stopped: ['Đã dừng', 'muted'],
  error: ['Có lỗi', 'danger'],
}

const EVENT_LABEL: Record<string, string> = {
  captcha_detected: 'Phát hiện CAPTCHA', captcha_cleared: 'CAPTCHA không còn hiển thị', monitor_started: 'Bật giám sát',
  monitor_stopped: 'Dừng giám sát', monitor_error: 'Lỗi giám sát',
}

export function MonitorPage() {
  const { state, run, refresh } = useApp()
  const monitor = state.status?.monitor
  const [templates, setTemplates] = useState<TemplateInfo[]>([])
  const [form, setForm] = useState<MonitorConfig | null>(null)

  const loadTemplates = async () => {
    const result = await run(() => call('monitor.templates.list'))
    if (result) setTemplates(result.templates)
  }

  useEffect(() => {
    void call('monitor.templates.list').then((r) => setTemplates(r.templates)).catch(() => undefined)
  }, [])

  useEffect(() => {
    if (monitor && !form) setForm(monitor.config)
  }, [monitor, form])

  if (!monitor) return null
  const s = monitor.state
  const [label, tone] = MONITOR_LABEL[s.status] ?? [s.status, 'muted' as Tone]

  return (
    <div className="stack">
      <div className="grid grid-2">
        <Card title="Giám sát TikTok LIVE Studio" icon={<ScanEye size={16} />} actions={<Badge tone={tone}>{label}</Badge>}>
          <p className="muted small">Agent chụp vùng cửa sổ TikTok LIVE Studio và so khớp với ảnh mẫu CAPTCHA. Khi phát hiện, Agent cảnh báo trên ứng dụng và gửi trạng thái lên Cloud để OWNER nhận thông báo. Agent không tự giải hay bấm vào CAPTCHA.</p>
          <div className="stats-row">
            <Stat label="Cửa sổ" value={s.window_found ? (s.window_visible ? 'Đang hiển thị' : 'Thu nhỏ') : 'Chưa thấy'} sub={s.window_info ? `${s.window_info.exe_name} · PID ${s.window_info.pid}` : s.process_running ? 'Tiến trình đang chạy nhưng không thấy cửa sổ' : undefined} />
            <Stat label="Điểm khớp gần nhất" value={s.match_score ?? '—'} sub={s.matched_template ?? undefined} />
            <Stat label="Lần quét" value={formatTime(s.last_scan)} sub={s.monitors_detected ? `${s.monitors_detected} màn hình` : undefined} />
          </div>
          {s.error && <div className="error-line"><span>{s.error}</span></div>}
          <div className="row">
            {s.running ? (
              <ActionButton variant="danger" icon={<Square size={16} />} onAction={() => run(() => call('monitor.stop'), 'Đã dừng giám sát').then(refresh)}>Dừng giám sát</ActionButton>
            ) : (
              <ActionButton variant="primary" icon={<Play size={16} />} disabled={templates.length === 0} onAction={() => run(() => call('monitor.start'), 'Đã bật giám sát').then(refresh)}>Bật giám sát</ActionButton>
            )}
            {templates.length === 0 && <small className="warn-text">Thêm ít nhất một ảnh mẫu CAPTCHA trước.</small>}
          </div>
        </Card>

        <Card title="Cấu hình" icon={<Settings2 size={16} />}>
          {form && (
            <div className="stack-sm">
              <Field label={`Chu kỳ quét: ${form.interval_seconds.toFixed(1)} giây`}>
                <input type="range" min={0.5} max={15} step={0.5} value={form.interval_seconds} onChange={(e) => setForm({ ...form, interval_seconds: Number(e.target.value) })} />
              </Field>
              <Field label={`Ngưỡng nhận diện: ${form.threshold.toFixed(2)}`} hint="Cao hơn = ít báo nhầm hơn nhưng dễ bỏ sót.">
                <input type="range" min={0.65} max={0.99} step={0.01} value={form.threshold} onChange={(e) => setForm({ ...form, threshold: Number(e.target.value) })} />
              </Field>
              <Toggle checked={form.telegram_enabled} label="Gửi cảnh báo Telegram (Cloud gửi bằng bot của hệ thống)" onChange={(v) => setForm({ ...form, telegram_enabled: v })} />
              {form.telegram_enabled && (
                <Field label="Telegram Chat ID"><Input value={form.telegram_chat_id} maxLength={50} onChange={(e) => setForm({ ...form, telegram_chat_id: e.target.value })} /></Field>
              )}
              <Toggle checked={form.autostart} label="Tự bật giám sát khi mở ứng dụng" onChange={(v) => setForm({ ...form, autostart: v })} />
              <div className="row">
                <ActionButton variant="primary" icon={<Save size={16} />} onAction={async () => {
                  const saved = await run(() => call('monitor.config', form), 'Đã lưu cấu hình giám sát')
                  if (saved) setForm(saved)
                }}>Lưu cấu hình</ActionButton>
              </div>
            </div>
          )}
        </Card>
      </div>

      <div className="grid grid-2">
        <Card
          title={`Ảnh mẫu CAPTCHA (${templates.length})`}
          icon={<ImagePlus size={16} />}
          actions={<ActionButton icon={<ImagePlus size={16} />} onAction={async () => {
            const picked = await open({ multiple: false, title: 'Chọn ảnh mẫu CAPTCHA', filters: [{ name: 'Ảnh', extensions: ['png', 'jpg', 'jpeg'] }] })
            if (typeof picked === 'string') {
              await run(() => call('monitor.templates.add', { path: picked }), 'Đã thêm ảnh mẫu')
              await loadTemplates()
            }
          }}>Thêm ảnh mẫu</ActionButton>}
        >
          <p className="muted small">Cắt ảnh vùng tiêu đề/khung CAPTCHA rõ nét (PNG/JPEG, 20–1800px). Có thể thêm nhiều mẫu; Agent so khớp tất cả.</p>
          {templates.length === 0 ? <Empty icon={<ImagePlus size={28} />} title="Chưa có ảnh mẫu" /> : (
            <ul className="list">
              {templates.map((t) => (
                <li key={t.name}>
                  <div><strong>{t.name}</strong><small className="muted">{formatBytes(t.bytes)}</small></div>
                  <ActionButton variant="ghost" icon={<Trash2 size={16} />} onAction={async () => {
                    if (!window.confirm(`Xoá ảnh mẫu ${t.name}?`)) return
                    await run(() => call('monitor.templates.remove', { name: t.name }), 'Đã xoá ảnh mẫu')
                    await loadTemplates()
                  }}>Xoá</ActionButton>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Sự kiện gần đây" icon={<History size={16} />}>
          {monitor.events.length === 0 ? <Empty title="Chưa có sự kiện" /> : (
            <ul className="list">
              {monitor.events.slice(0, 15).map((e) => (
                <li key={e.id}>
                  <div>
                    <strong className={e.kind === 'captcha_detected' ? 'danger-text' : undefined}>{EVENT_LABEL[e.kind] ?? e.kind}</strong>
                    <small className="muted">{formatTime(e.at)} · {e.detail}</small>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  )
}
