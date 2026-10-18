import { Clapperboard, Cloud, FolderOpen, ListVideo, Play, Power, RotateCw, ScanEye, Square, Terminal } from 'lucide-react'
import { useApp } from '../../app/store'
import { AGENT_LABEL } from '../../app/App'
import { ActionButton, Badge, Card, Empty, ErrorLine, Stat, formatDateTime, formatTime, type Tone } from '../../app/ui'
import { call } from '../../services/core'
import { MONITOR_LABEL } from '../monitor/MonitorPage'
import type { CommandStatus } from '../../types/ipc'

export const COMMAND_LABEL: Record<string, string> = {
  monitor_start: 'Bật giám sát CAPTCHA', monitor_stop: 'Tắt giám sát CAPTCHA', monitor_config: 'Cấu hình giám sát',
  obs_status: 'Kiểm tra OBS', obs_pause: 'Tạm dừng video', obs_resume: 'Tiếp tục video', obs_next: 'Video tiếp theo',
  obs_previous: 'Video trước', obs_stop: 'Dừng phát', tts_speak: 'Đọc giọng nói (TTS)',
  local_media_play: 'Phát một video', local_media_next: 'Video tiếp theo', local_media_previous: 'Video trước',
  local_media_stop: 'Dừng phát', local_playlist_start: 'Phát playlist', local_playback_loop: 'Đổi chế độ lặp',
  media_sync: 'Quét & đồng bộ thư viện',
}

export const COMMAND_STATUS: Record<CommandStatus, [string, Tone]> = {
  accepted: ['Đã nhận', 'info'], running: ['Đang chạy', 'info'], succeeded: ['Thành công', 'ok'], failed: ['Thất bại', 'danger'],
}

export function OverviewPage({ navigate }: { navigate: (page: string) => void }) {
  const { state, run, refresh } = useApp()
  const status = state.status
  if (!status) return null
  const { agent, cloud, obs, media, playback, monitor } = status
  const [agentLabel, agentTone] = AGENT_LABEL[agent.state]
  const running = agent.state !== 'stopped' && agent.state !== 'error'

  return (
    <div className="stack">
      <section className="hero">
        <div className="hero-main">
          <div className={`hero-orb orb-${agentTone}`}><Power size={26} /></div>
          <div>
            <span className="eyebrow">Trạng thái Agent</span>
            <h2>{agentLabel}</h2>
            <p className="muted">
              {agent.activated ? <>Agent <code>{agent.agent_id}</code> · {cloud.api_url ?? state.config?.cloud.api_url}</> : 'Máy chưa kích hoạt. Nhập Cloud URL và mã kích hoạt để bắt đầu.'}
            </p>
            <ErrorLine error={agent.error} />
          </div>
        </div>
        <div className="hero-actions">
          {!agent.activated ? (
            <ActionButton variant="primary" icon={<Cloud size={16} />} onAction={async () => navigate('connection')}>Kích hoạt ngay</ActionButton>
          ) : running ? (
            <>
              <ActionButton icon={<RotateCw size={16} />} onAction={() => run(() => call('agent.restart'), 'Đã khởi động lại Agent').then(refresh)}>Khởi động lại</ActionButton>
              <ActionButton variant="danger" icon={<Square size={16} />} onAction={() => run(() => call('agent.stop'), 'Đã dừng Agent').then(refresh)}>Dừng Agent</ActionButton>
            </>
          ) : (
            <ActionButton variant="primary" icon={<Play size={16} />} onAction={() => run(() => call('agent.start')).then(refresh)}>Bắt đầu Agent</ActionButton>
          )}
        </div>
      </section>

      <div className="grid grid-4">
        <Card title="Cloud" icon={<Cloud size={16} />}>
          <Badge tone={cloud.connected ? 'ok' : cloud.state === 'disconnected' || cloud.state === 'rejected' ? 'danger' : 'muted'}>
            {cloud.connected ? 'Đã kết nối' : cloud.state === 'disconnected' ? 'Mất kết nối' : cloud.state === 'connecting' ? 'Đang kết nối' : cloud.state === 'rejected' ? 'Bị từ chối' : 'Chưa chạy'}
          </Badge>
          <Stat label="Heartbeat gần nhất" value={formatTime(cloud.last_ok)} sub={cloud.next_retry_seconds ? `Thử lại sau ~${cloud.next_retry_seconds}s` : undefined} />
          <ErrorLine error={cloud.connected ? null : cloud.last_error} />
        </Card>
        <Card title="OBS Studio" icon={<Clapperboard size={16} />} actions={<button className="link" onClick={() => navigate('obs')}>Mở</button>}>
          <Badge tone={obs.connected ? 'ok' : obs.state === 'error' ? 'danger' : 'muted'}>{obs.connected ? `OBS ${obs.obs_version ?? ''}` : obs.state === 'connecting' ? 'Đang kết nối' : 'Chưa kết nối'}</Badge>
          <Stat label="Nguồn video" value={obs.media_input} sub={`${obs.host}:${obs.port}`} />
          <ErrorLine error={obs.connected ? null : obs.error} />
        </Card>
        <Card title="Thư viện video" icon={<FolderOpen size={16} />} actions={<button className="link" onClick={() => navigate('media')}>Mở</button>}>
          <Stat label="Video MP4" value={media.video_count.toLocaleString('vi-VN')} sub={`${media.folder_count} thư mục · quét ${formatDateTime(media.scanned_at)}`} />
          {state.lastSync && <small className="muted">Đồng bộ Cloud {formatTime(state.lastSync.at)} · {state.lastSync.synced} video</small>}
        </Card>
        <Card title="CAPTCHA Monitor" icon={<ScanEye size={16} />} actions={<button className="link" onClick={() => navigate('monitor')}>Mở</button>}>
          <Badge tone={MONITOR_LABEL[monitor.state.status]?.[1] ?? 'muted'}>{MONITOR_LABEL[monitor.state.status]?.[0] ?? monitor.state.status}</Badge>
          <Stat label="Cửa sổ TikTok LIVE Studio" value={monitor.state.window_found ? 'Đã tìm thấy' : 'Chưa thấy'} sub={monitor.state.running ? `Quét mỗi ${monitor.config.interval_seconds}s` : 'Đang tắt'} />
        </Card>
      </div>

      <div className="grid grid-2">
        <Card title="Đang phát" icon={<ListVideo size={16} />}>
          {playback.active ? (
            <div className="now-playing">
              <strong>{playback.current_filename ?? playback.current_media_id?.slice(0, 12)}</strong>
              <span className="muted">Video {playback.index + 1}/{playback.count} · {playback.paused ? 'Tạm dừng' : 'Đang phát'} · {playback.loop ? 'Lặp lại' : 'Không lặp'}</span>
            </div>
          ) : (
            <Empty icon={<ListVideo size={28} />} title={playback.needs_manual_resume ? 'Playlist trước đã dừng khi tắt ứng dụng' : 'Chưa phát playlist'}>
              {playback.needs_manual_resume ? 'Vì an toàn LIVE, Agent không tự phát lại. Hãy bắt đầu lại từ Web hoặc trang OBS & Phát.' : 'Playlist được gửi từ Sarae Web hoặc phát thử tại trang OBS & Phát.'}
            </Empty>
          )}
          <ErrorLine error={playback.last_error} />
        </Card>
        <Card title="Lệnh từ Cloud" icon={<Terminal size={16} />}>
          {state.commands.length === 0 ? (
            <Empty title="Chưa có lệnh nào">Lệnh do OWNER gửi từ Sarae Web sẽ hiện ở đây cùng kết quả thực thi.</Empty>
          ) : (
            <ul className="list">
              {state.commands.slice(0, 8).map((c) => (
                <li key={c.command_id}>
                  <div>
                    <strong>{COMMAND_LABEL[c.kind] ?? c.kind}</strong>
                    <small className="muted">{formatTime(c.received_at)}{c.duration_ms != null ? ` · ${c.duration_ms} ms` : ''}</small>
                    {c.error && <small className="danger-text">{c.error.message}</small>}
                  </div>
                  <Badge tone={COMMAND_STATUS[c.status][1]}>{COMMAND_STATUS[c.status][0]}</Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  )
}
