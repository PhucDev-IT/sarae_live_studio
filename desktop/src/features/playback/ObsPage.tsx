import { useEffect, useState } from 'react'
import {
  CheckCircle2, Clapperboard, Link2, Mic, Pause, Play, Plug, Repeat, SkipBack, SkipForward, Square, Unplug, XCircle,
} from 'lucide-react'
import { useApp } from '../../app/store'
import { ActionButton, Badge, Card, Empty, ErrorLine, Field, Input, Progress, Stat, Toggle, formatDuration } from '../../app/ui'
import { call } from '../../services/core'
import type { ObsStatus, Voice } from '../../types/ipc'

const MEDIA_STATE: Record<string, string> = {
  OBS_MEDIA_STATE_PLAYING: 'Đang phát', OBS_MEDIA_STATE_PAUSED: 'Tạm dừng', OBS_MEDIA_STATE_STOPPED: 'Đã dừng',
  OBS_MEDIA_STATE_ENDED: 'Hết video', OBS_MEDIA_STATE_OPENING: 'Đang mở', OBS_MEDIA_STATE_BUFFERING: 'Đang tải',
  OBS_MEDIA_STATE_ERROR: 'Lỗi media', OBS_MEDIA_STATE_NONE: 'Trống',
}

export function ObsPage() {
  return (
    <div className="grid grid-2">
      <ObsConnectionCard />
      <PlaybackCard />
      <VoiceCard />
    </div>
  )
}

function InputCheck({ ok, name }: { ok: boolean | null | undefined; name: string }) {
  if (ok === undefined || ok === null) return <span className="muted">{name}: chưa kiểm tra</span>
  return ok
    ? <span className="ok-text"><CheckCircle2 size={14} /> {name}</span>
    : <span className="danger-text"><XCircle size={14} /> Thiếu nguồn “{name}” trong OBS</span>
}

function ObsConnectionCard() {
  const { state, run, refreshConfig } = useApp()
  const obs = state.status?.obs
  const config = state.config
  const [form, setForm] = useState({ host: '127.0.0.1', port: 4455, media_input: 'Sarae Video', voice_input: 'Sarae Voice', password: '' })
  const [inspect, setInspect] = useState<ObsStatus | null>(null)

  useEffect(() => {
    if (config) setForm((f) => ({ ...f, host: config.obs.host, port: config.obs.port, media_input: config.obs.media_input, voice_input: config.obs.voice_input }))
  }, [config])

  useEffect(() => {
    if (obs?.connected) void call('obs.status').then(setInspect).catch(() => undefined)
    else setInspect(null)
  }, [obs?.connected, obs?.media_input, obs?.voice_input])

  const connect = async () => {
    const saved = await run(() => call('config.set', {
      obs: { host: form.host.trim(), port: Number(form.port), media_input: form.media_input.trim(), voice_input: form.voice_input.trim() },
      ...(form.password ? { obs_password: form.password } : {}),
    }))
    if (!saved) return
    setForm((f) => ({ ...f, password: '' }))
    await refreshConfig()
    const result = await run(() => call('obs.connect'), 'Đã kết nối OBS')
    if (result) setInspect(await call('obs.status'))
  }

  return (
    <Card
      title="Kết nối OBS WebSocket"
      icon={<Plug size={16} />}
      actions={obs && <Badge tone={obs.connected ? 'ok' : obs.state === 'error' ? 'danger' : 'muted'}>{obs.connected ? `OBS ${obs.obs_version ?? ''} · WS ${obs.websocket_version ?? ''}` : obs.state === 'connecting' ? 'Đang kết nối' : 'Chưa kết nối'}</Badge>}
    >
      <p className="muted small">Trong OBS: Tools → WebSocket Server Settings → bật server. Tạo Media Source tên trùng “Nguồn video”, và một Media Source riêng cho giọng đọc TTS.</p>
      <div className="form-grid">
        <Field label="Host"><Input value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} /></Field>
        <Field label="Port"><Input type="number" min={1} max={65535} value={form.port} onChange={(e) => setForm({ ...form, port: Number(e.target.value) })} /></Field>
        <Field label="Mật khẩu" hint={config?.obs.password_set ? 'Đã lưu (DPAPI). Để trống để giữ nguyên.' : 'Để trống nếu OBS không đặt mật khẩu.'}>
          <Input type="password" value={form.password} autoComplete="off" onChange={(e) => setForm({ ...form, password: e.target.value })} />
        </Field>
        <Field label="Nguồn video (Media Source)"><Input value={form.media_input} onChange={(e) => setForm({ ...form, media_input: e.target.value })} /></Field>
        <Field label="Nguồn giọng đọc (Media Source)"><Input value={form.voice_input} onChange={(e) => setForm({ ...form, voice_input: e.target.value })} /></Field>
      </div>
      <div className="row">
        <ActionButton variant="primary" icon={<Link2 size={16} />} onAction={connect}>Lưu & kết nối</ActionButton>
        {obs?.connected && <ActionButton icon={<Unplug size={16} />} onAction={() => run(() => call('obs.disconnect'), 'Đã ngắt kết nối OBS')}>Ngắt kết nối</ActionButton>}
        {config?.obs.password_set && (
          <ActionButton variant="ghost" onAction={() => run(() => call('config.set', { clear_obs_password: true }), 'Đã xoá mật khẩu OBS').then(refreshConfig)}>Xoá mật khẩu đã lưu</ActionButton>
        )}
      </div>
      {config && (
        <Toggle checked={config.obs.auto_connect} label="Tự kết nối lại OBS khi bị ngắt"
          onChange={(value) => void run(() => call('config.set', { obs: { auto_connect: value } })).then(refreshConfig)} />
      )}
      {obs?.connected && inspect && (
        <div className="checks">
          <InputCheck ok={inspect.media_input_ok} name={inspect.media_input} />
          <InputCheck ok={inspect.voice_input_ok} name={inspect.voice_input} />
        </div>
      )}
      <ErrorLine error={obs?.connected ? null : obs?.error} />
    </Card>
  )
}

function PlaybackCard() {
  const { state, run } = useApp()
  const playback = state.status?.playback
  const connected = !!state.status?.obs.connected
  const active = !!playback?.active
  const [media, setMedia] = useState<ObsStatus['media']>(null)

  // Position is only known by OBS: poll it while a playlist is active and visible.
  useEffect(() => {
    if (!active || !connected) {
      setMedia(null)
      return
    }
    const tick = () => void call('obs.status').then((s) => setMedia(s.media ?? null)).catch(() => undefined)
    tick()
    const timer = window.setInterval(tick, 1500)
    return () => window.clearInterval(timer)
  }, [active, connected])

  if (!playback) return null
  const percent = media?.duration_ms ? ((media.cursor_ms ?? 0) / media.duration_ms) * 100 : 0

  return (
    <Card title="Playlist đang phát" icon={<Clapperboard size={16} />}
      actions={<Badge tone={playback.active ? (playback.paused ? 'warn' : 'ok') : 'muted'}>{playback.active ? (playback.paused ? 'Tạm dừng' : 'Đang phát') : 'Đã dừng'}</Badge>}>
      {playback.active ? (
        <div className="player">
          <div className="player-title">
            <strong>{playback.current_filename ?? playback.current_media_id?.slice(0, 16)}</strong>
            <span className="muted">Video {playback.index + 1}/{playback.count} · nguồn “{playback.source}” · {MEDIA_STATE[playback.media_state ?? ''] ?? playback.media_state ?? '—'}</span>
          </div>
          {media?.duration_ms ? (
            <div className="player-progress">
              <Progress value={percent} />
              <span className="muted mono">{formatDuration(media.cursor_ms)} / {formatDuration(media.duration_ms)}</span>
            </div>
          ) : null}
          <div className="player-controls">
            <ActionButton icon={<SkipBack size={16} />} onAction={() => run(() => call('playback.previous'))} aria-label="Video trước" />
            {playback.paused
              ? <ActionButton variant="primary" icon={<Play size={16} />} onAction={() => run(() => call('playback.resume'))}>Tiếp tục</ActionButton>
              : <ActionButton variant="primary" icon={<Pause size={16} />} onAction={() => run(() => call('playback.pause'))}>Tạm dừng</ActionButton>}
            <ActionButton icon={<SkipForward size={16} />} onAction={() => run(() => call('playback.next'))} aria-label="Video tiếp theo" />
            <ActionButton variant="danger" icon={<Square size={16} />} onAction={() => run(() => call('playback.stop'), 'Đã dừng phát')}>Dừng</ActionButton>
          </div>
          <Toggle checked={playback.loop} label={<><Repeat size={14} /> Lặp lại playlist</>} onChange={(value) => void run(() => call('playback.loop', { loop: value }))} />
          {playback.skipped.length > 0 && <small className="warn-text">Đã bỏ qua {playback.skipped.length} video không còn file trên máy.</small>}
        </div>
      ) : (
        <Empty icon={<Clapperboard size={28} />} title={playback.needs_manual_resume ? 'Playlist trước đã dừng khi tắt ứng dụng' : 'Chưa phát playlist'}>
          Playlist được OWNER gửi từ Sarae Web. Có thể phát thử từng video tại trang Thư viện video.
        </Empty>
      )}
      <ErrorLine error={playback.last_error} />
      <div className="stats-row">
        <Stat label="Bắt đầu" value={playback.started_at ? new Date(playback.started_at).toLocaleTimeString('vi-VN') : '—'} />
        <Stat label="Số video" value={playback.count} />
      </div>
    </Card>
  )
}

function VoiceCard() {
  const { state, run } = useApp()
  const [text, setText] = useState('')
  const [voice, setVoice] = useState<Voice>('vi-VN-HoaiMyNeural')
  return (
    <Card title="Giọng đọc thử (TTS)" icon={<Mic size={16} />}>
      <p className="muted small">Câu đọc được tạo bằng giọng Microsoft Neural và phát qua nguồn OBS “{state.config?.obs.voice_input ?? 'Sarae Voice'}”. Cloud cũng có thể gửi lệnh đọc tới Agent.</p>
      <Field label={`Nội dung (${text.length}/450)`}>
        <textarea className="input textarea" maxLength={450} value={text} onChange={(e) => setText(e.target.value)} placeholder="Xin chào cả nhà, hôm nay shop có ưu đãi…" />
      </Field>
      <div className="row">
        <select className="input select" value={voice} onChange={(e) => setVoice(e.target.value as Voice)}>
          <option value="vi-VN-HoaiMyNeural">Hoài My (nữ)</option>
          <option value="vi-VN-NamMinhNeural">Nam Minh (nam)</option>
        </select>
        <ActionButton variant="primary" icon={<Mic size={16} />} disabled={!text.trim()} onAction={() => run(() => call('voice.speak', { text: text.trim(), voice }), 'Đã gửi giọng đọc tới OBS')}>Đọc qua OBS</ActionButton>
      </div>
    </Card>
  )
}
