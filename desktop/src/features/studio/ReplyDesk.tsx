import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Ban, Bot, MessageSquareText, Mic, Play, Radio, RefreshCw, Send, Settings2, Square, Timer, Volume2, X } from 'lucide-react'
import { studioClient, type Product, type ReplyDeskStatus, type ReplyEntry, type ReplySettings } from '../../services/studio'
import { ErrorNotice, Modal, errorMessage } from './ui'

type Props = { products: Product[]; disabled: boolean; visible: boolean; onNotice: (message: string) => void }
const stageInfo: Record<string, { label: string; tone: string }> = {
  ignored: { label: 'Bỏ qua', tone: 'neutral' }, skipped: { label: 'Không cần trả lời', tone: 'neutral' },
  queued: { label: 'Chờ AI', tone: 'honey' }, thinking: { label: 'AI đang nghĩ', tone: 'honey' },
  ready: { label: 'Sẵn sàng', tone: 'success' }, review: { label: 'Cần duyệt', tone: 'warning' },
  done: { label: 'Đã trả lời', tone: 'success' }, expired: { label: 'Quá hạn', tone: 'neutral' },
  failed: { label: 'Lỗi', tone: 'warning' }, dismissed: { label: 'Đã bỏ qua', tone: 'neutral' },
}
const deliveryLabels: Record<string, string> = {
  QUEUED: 'đã xếp hàng đọc', FAILED: 'lỗi', EXPIRED: 'quá hạn', SENDING: 'đang gửi…', CONFIRMED: 'đã hiện trên LIVE',
  SUBMITTED: 'đã gửi', RATE_LIMITED: 'chờ giãn nhịp', DUPLICATE: 'trùng, không gửi',
}
const voiceNames: Record<string, string> = { 'vi-VN-HoaiMyNeural': 'Hoài My (nữ)', 'vi-VN-NamMinhNeural': 'Nam Minh (nam)' }
const chatStates: Record<string, string> = { idle: 'Chưa đọc chat', connecting: 'Đang kết nối chat…', connected: 'Đang đọc chat', reconnecting: 'Mất kết nối chat, đang nối lại…', disconnected: 'Chat đã ngắt', stopping: 'Đang dừng…', error: 'Lỗi đọc chat' }

export function ReplyDesk({ products, disabled, visible, onNotice }: Props) {
  const [desk, setDesk] = useState<ReplyDeskStatus | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [test, setTest] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [showAll, setShowAll] = useState(false)
  const names = useMemo(() => new Map(products.map(product => [product.id, product.name])), [products])
  useEffect(() => {
    if (!visible || disabled) return
    let alive = true
    const load = () => studioClient.replies('status').then(next => { if (alive) setDesk(next) }).catch(cause => { if (alive) setError(errorMessage(cause)) })
    void load()
    const timer = window.setInterval(load, 1200)
    return () => { alive = false; window.clearInterval(timer) }
  }, [visible, disabled])
  const run = async (key: string, action: () => Promise<ReplyDeskStatus | unknown>) => {
    if (busy || disabled) return
    setBusy(key)
    setError('')
    try {
      const result = await action()
      if (result && typeof result === 'object' && 'settings' in result) setDesk(result as ReplyDeskStatus)
      else setDesk(await studioClient.replies('status'))
    } catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy('') }
  }
  const toggle = (key: 'auto_voice' | 'auto_comment') => {
    if (!desk) return
    const next = !desk.settings[key]
    void run(key, () => studioClient.repliesConfigure({ [key]: next }))
    onNotice(key === 'auto_voice' ? (next ? 'Đã bật tự đọc trả lời.' : 'Đã tắt tự đọc trả lời.') : (next ? 'Đã bật tự gửi bình luận.' : 'Đã tắt tự gửi bình luận.'))
  }
  const simulate = (event: FormEvent) => {
    event.preventDefault()
    if (!test.trim()) return
    const text = test.trim()
    setTest('')
    void run('simulate', () => studioClient.repliesSimulate(text))
  }
  const chatOn = ['connected', 'connecting', 'reconnecting'].includes(desk?.chat.status ?? '')
  const entries = (desk?.entries ?? []).filter(entry => showAll || !['ignored', 'skipped'].includes(entry.stage))
  const hidden = (desk?.entries.length ?? 0) - entries.length

  return <section className="studio-card studio-reply-desk" aria-label="Bàn trả lời khách">
    <header className="studio-card-heading"><MessageSquareText size={19} /><div className="studio-grow"><h2>Bàn trả lời khách</h2><p>AI trả lời câu hỏi theo sản phẩm đang phát{desk?.product_id ? `: ${names.get(desk.product_id) ?? ''}` : ' (chưa phát sản phẩm nào)'}.</p></div>
      <button className="studio-button ghost icon-only" aria-label="Cài đặt trả lời" title="Cài đặt trả lời" disabled={!desk} onClick={() => setSettingsOpen(true)}><Settings2 size={17} /></button></header>
    <div className="studio-reply-bar">
      <label className="studio-live-voice">Giọng đọc<select aria-label="Giọng đọc LIVE" value={desk?.settings.voice ?? ''} disabled={disabled || !desk || !!busy} onChange={event => void run('voice', () => studioClient.repliesConfigure({ voice: event.target.value }))}>{(desk?.voice_options ?? desk?.voices.map(id => ({ id, name: voiceNames[id] ?? id, provider: 'edge' as const, ready: true })) ?? []).map(voice => <option key={voice.id} value={voice.id}>{voice.name}{voice.provider === 'local_voice' ? ' · giọng local' : ''}</option>)}</select></label>
      <button className="studio-button ghost" disabled={disabled || !!busy || !desk} onClick={() => void run('voices', () => studioClient.replies('voices'))}><RefreshCw size={14} />{busy === 'voices' ? 'Đang đọc giọng…' : 'Làm mới giọng'}</button>
      {desk?.voice_server && !desk.voice_server.online && <button className="studio-button" disabled={disabled || !!busy || desk.voice_server.starting} onClick={() => void run('voices-start', () => studioClient.replies('voices_start'))}><Volume2 size={14} />{desk.voice_server.starting ? 'Đang bật voice server…' : 'Bật voice server'}</button>}
      <div className={`studio-chat-state ${desk?.chat.status ?? 'idle'}`}><Radio size={15} /><span><strong>{chatStates[desk?.chat.status ?? 'idle']}</strong>{desk?.chat.username ? ` · @${desk.chat.username}` : desk?.account_username ? ` · @${desk.account_username}` : ''}</span>
        {chatOn ? <button className="studio-button ghost" disabled={disabled || !!busy} onClick={() => void run('chat', () => studioClient.replies('chat_stop'))}><Square size={13} />Dừng</button>
          : <button className="studio-button" disabled={disabled || !!busy || !desk?.account_username} title={desk?.account_username ? 'Đọc chat công khai của tài khoản đã xác minh' : 'Đồng bộ sản phẩm TikTok trước để xác minh tài khoản'} onClick={() => void run('chat', () => studioClient.repliesChatStart())}><Play size={13} fill="currentColor" />Đọc chat</button>}
      </div>
      <label className="studio-switch"><input type="checkbox" role="switch" checked={!!desk?.settings.auto_voice} disabled={disabled || !desk || !!busy} onChange={() => toggle('auto_voice')} /><span aria-hidden="true" /><small><Volume2 size={13} />Tự đọc giọng</small></label>
      <label className="studio-switch"><input type="checkbox" role="switch" checked={!!desk?.settings.auto_comment} disabled={disabled || !desk || !!busy} onChange={() => toggle('auto_comment')} /><span aria-hidden="true" /><small><Send size={13} />Tự gửi bình luận</small></label>
      <span className="studio-muted studio-reply-stats" title="Đã nhận: mọi bình luận chat gửi tới Studio, kể cả bình luận bị ẩn vì không phải câu hỏi">{desk ? `${desk.stats.received ?? 0} đã nhận · ${desk.stats.answered ?? 0} trả lời · ${desk.queued} chờ` : ''}</span>
    </div>
    {desk?.settings.voice.startsWith('local:') && desk.voice_server && <p className="studio-reply-note">{desk.voice_server.online ? 'Giọng local qua sarae-voice-server · chế độ đọc nhanh cho LIVE.' : desk.voice_server.error}</p>}
    {desk?.chat.status === 'error' && <ErrorNotice message={`Không đọc được chat: ${desk.chat.error}. Kiểm tra tài khoản đang LIVE công khai.`} />}
    {desk?.chat.status === 'reconnecting' && <p className="studio-reply-note"><RefreshCw size={13} />TikTok vừa ngắt kết nối chat ({desk.chat.error}); Studio tự nối lại. Bình luận trong lúc mất kết nối không nhận được.</p>}
    {(desk?.settings.auto_voice || desk?.settings.auto_comment) && <p className="studio-reply-note"><Timer size={13} />Câu trả lời tự gửi sau {desk.settings.confirm_seconds} giây để bạn kịp hủy; câu cần duyệt luôn chờ bạn bấm.</p>}
    <form className="studio-test-box" onSubmit={simulate}><Bot size={16} /><input aria-label="Bình luận thử" value={test} maxLength={500} disabled={disabled || !desk} placeholder="Thử một bình luận như khách (không gửi lên TikTok)…" onChange={event => setTest(event.target.value)} /><button className="studio-button" disabled={disabled || !test.trim() || !!busy}>Thử</button></form>
    {error && <ErrorNotice message={error} />}
    <div className="studio-reply-feed">
      {!entries.length && <p className="studio-chat-empty studio-muted">{desk?.chat.status === 'connected' ? 'Đang chờ khách đặt câu hỏi…' : 'Bấm Đọc chat khi đang LIVE, hoặc gõ một bình luận thử ở trên.'}</p>}
      {entries.map(entry => <ReplyCard key={entry.id} entry={entry} productName={entry.product_id ? names.get(entry.product_id) : undefined} disabled={disabled} onAction={(action, text) => run(`${action}:${entry.id}`, () => studioClient.replyAction(action, entry.id, text))} busy={busy} />)}
      {hidden > 0 && <button className="studio-button ghost studio-show-all" onClick={() => setShowAll(value => !value)}>{showAll ? 'Ẩn bình luận không cần trả lời' : `Hiện ${hidden} bình luận không cần trả lời`}</button>}
    </div>
    {settingsOpen && desk && <ReplySettingsModal settings={desk.settings} voices={desk.voices} voiceOptions={desk.voice_options} onClose={() => setSettingsOpen(false)} onSaved={next => { setDesk(next); setSettingsOpen(false); onNotice('Đã lưu cài đặt trả lời.') }} />}
  </section>
}

function ReplyCard({ entry, productName, disabled, busy, onAction }: { entry: ReplyEntry; productName?: string; disabled: boolean; busy: string; onAction: (action: 'speak' | 'send_comment' | 'cancel' | 'dismiss' | 'edit' | 'hold', text?: string) => Promise<void> }) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(entry.reply)
  useEffect(() => { if (!editing) setText(entry.reply) }, [entry.reply, editing])
  const startEditing = () => {
    setText(entry.reply)
    setEditing(true)
    // The AI's text must never go out while the operator rewrites it.
    if (entry.auto_in !== null && entry.auto.length > 0) void onAction('hold')
  }
  const changed = editing && text.trim() !== '' && text !== entry.reply
  const saveEdit = async () => { if (changed) await onAction('edit', text); setEditing(false) }
  const stage = stageInfo[entry.stage] ?? { label: entry.stage, tone: 'neutral' }
  // A review card the AI left blank (facts missing) is still answerable: the operator writes the reply.
  const answerable = ['ready', 'review', 'done', 'failed'].includes(entry.stage) && (!!entry.reply || entry.stage === 'review')
  const noText = !entry.reply && !(editing && text.trim())
  const locked = disabled || busy.endsWith(`:${entry.id}`)
  // Sending while editing sends (and saves) the text in the box.
  const send = (action: 'speak' | 'send_comment') => { void onAction(action, editing && text.trim() ? text : undefined); setEditing(false) }
  return <article className={`studio-reply-card ${entry.stage}`}>
    <div className="studio-reply-comment"><strong>{entry.nickname || entry.username || 'Khách'}</strong>{entry.simulated && <span className="studio-tag neutral">Thử</span>}<span className={`studio-tag ${stage.tone}`}>{stage.label}</span>{entry.edited && <span className="studio-tag neutral">Đã sửa</span>}<time>{entry.age_seconds < 60 ? `${entry.age_seconds}s` : `${Math.floor(entry.age_seconds / 60)} phút`}</time><p>{entry.comment}</p></div>
    {(entry.reply || entry.stage === 'thinking' || editing) && <div className="studio-reply-answer">
      {entry.stage === 'thinking' ? <span className="studio-typing"><i /><i /><i /></span>
        : editing ? <textarea aria-label="Sửa câu trả lời" value={text} maxLength={300} rows={3} autoFocus onChange={event => setText(event.target.value)}
          onKeyDown={event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void saveEdit() } if (event.key === 'Escape') setEditing(false) }} />
        : <p>{entry.reply}</p>}
      {entry.reply && <footer>
        {productName && <span className="studio-muted">Theo: {productName}</span>}
        {entry.voice && <span className={`studio-delivery ${entry.voice.status}`} title={entry.voice.detail}><Mic size={12} />Giọng {deliveryLabels[entry.voice.status] ?? entry.voice.status}</span>}
        {entry.comment_delivery && <span className={`studio-delivery ${entry.comment_delivery.status}`} title={entry.comment_delivery.error ?? undefined}><Send size={12} />Bình luận {deliveryLabels[entry.comment_delivery.status] ?? entry.comment_delivery.status}</span>}
      </footer>}
    </div>}
    {(entry.stage === 'failed' || entry.stage === 'expired' || entry.stage === 'skipped' || entry.stage === 'ignored' || entry.stage === 'review') && entry.reason && !entry.reply && !editing && <p className="studio-reason">{entry.stage === 'review' ? `AI không trả lời được: ${entry.reason}` : entry.reason}</p>}
    {entry.comment_delivery?.error && <p className="studio-reason">{entry.comment_delivery.error}</p>}
    {entry.voice?.status === 'FAILED' && <p className="studio-reason">{entry.voice.detail}</p>}
    {answerable && <div className="studio-reply-actions">
      {entry.auto_in !== null && entry.auto.length > 0 && <button className="studio-button studio-countdown" disabled={locked} onClick={() => void onAction('cancel')}><X size={14} />Hủy tự gửi ({Math.ceil(entry.auto_in)}s)</button>}
      {editing ? <>
        <button className="studio-button primary" disabled={locked || !text.trim()} title="Lưu (Ctrl+Enter)" onClick={() => void saveEdit()}>Lưu</button>
        <button className="studio-button ghost" disabled={locked} title="Bỏ thay đổi (Esc)" onClick={() => setEditing(false)}>Hủy</button>
      </> : <button className="studio-button ghost" disabled={locked} title="Sửa câu trả lời (tạm dừng tự gửi)" onClick={startEditing}>{entry.reply ? 'Sửa' : 'Viết trả lời'}</button>}
      <button className="studio-button" disabled={locked || noText} onClick={() => send('speak')}><Volume2 size={14} />Đọc</button>
      <button className="studio-button" disabled={locked || noText || entry.simulated}title={entry.simulated ? 'Bình luận thử không gửi lên TikTok' : undefined} onClick={() => send('send_comment')}><Send size={14} />Gửi bình luận</button>
      {entry.stage !== 'done' && <button className="studio-button ghost icon-only" disabled={locked} aria-label="Bỏ qua" title="Bỏ qua" onClick={() => void onAction('dismiss')}><Ban size={15} /></button>}
    </div>}
  </article>
}

function ReplySettingsModal({ settings, voices, voiceOptions, onClose, onSaved }: { settings: ReplySettings; voices: string[]; voiceOptions?: ReplyDeskStatus['voice_options']; onClose: () => void; onSaved: (status: ReplyDeskStatus) => void }) {
  const [draft, setDraft] = useState(settings)
  const [ignored, setIgnored] = useState(settings.ignored_usernames.map(name => '@' + name).join(', '))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const save = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const names = ignored.split(/[\s,]+/).map(name => name.trim().replace(/^@/, '')).filter(Boolean)
      onSaved(await studioClient.repliesConfigure({ ...draft, ignored_usernames: names }))
    } catch (cause) { setError(errorMessage(cause)); setBusy(false) }
  }
  const number = (key: keyof ReplySettings, label: string, min: number, max: number, unit: string) => <label className="studio-field">{label}<span className="studio-input-unit"><input type="number" min={min} max={max} value={draft[key] as number} onChange={event => setDraft({ ...draft, [key]: Number(event.target.value) })} /><em>{unit}</em></span></label>
  return <Modal title="Cài đặt trả lời khách" onClose={() => { if (!busy) onClose() }} wide><form className="studio-form" onSubmit={event => void save(event)}>
    <h3 className="studio-subhead">Giọng đọc (phát vào nguồn giọng của OBS)</h3>
    <div className="studio-form-row">
      <label className="studio-field">Giọng<select value={draft.voice} onChange={event => setDraft({ ...draft, voice: event.target.value })}>{voices.map(voice => <option key={voice} value={voice}>{voiceOptions?.find(item => item.id === voice)?.name ?? voiceNames[voice] ?? voice}</option>)}</select></label>
      {number('rate', 'Tốc độ đọc', -30, 50, '%')}
    </div>
    <label className="studio-switch studio-switch-field"><input type="checkbox" role="switch" checked={draft.call_name} onChange={event => setDraft({ ...draft, call_name: event.target.checked })} /><span aria-hidden="true" /><small>Gọi tên khách trước câu trả lời (“Lan ơi, …”)</small></label>
    <label className="studio-switch studio-switch-field"><input type="checkbox" role="switch" checked={draft.duck_video} onChange={event => setDraft({ ...draft, duck_video: event.target.checked })} /><span aria-hidden="true" /><small>Giảm âm lượng video khi đang đọc</small></label>
    <h3 className="studio-subhead">Tự động & nhịp gửi</h3>
    <label className="studio-switch studio-switch-field"><input type="checkbox" role="switch" checked={draft.follow_session} onChange={event => setDraft({ ...draft, follow_session: event.target.checked })} /><span aria-hidden="true" /><small>Tự đọc chat khi phiên LIVE bắt đầu và dừng khi phiên kết thúc</small></label>
    <div className="studio-form-row">
      {number('confirm_seconds', 'Chờ trước khi tự gửi', 0, 30, 'giây')}
      {number('max_age_seconds', 'Bỏ câu hỏi cũ hơn', 10, 300, 'giây')}
    </div>
    <div className="studio-form-row">
      {number('comment_min_interval_seconds', 'Cách nhau tối thiểu', 4, 120, 'giây')}
      {number('comment_max_per_minute', 'Tối đa bình luận', 1, 20, '/phút')}
    </div>
    <label className="studio-switch studio-switch-field"><input type="checkbox" role="switch" checked={draft.answer_all} onChange={event => setDraft({ ...draft, answer_all: event.target.checked })} /><span aria-hidden="true" /><small>Gửi mọi bình luận cho AI (mặc định chỉ câu hỏi bán hàng)</small></label>
    <label className="studio-field">Bỏ qua các tài khoản<input value={ignored} placeholder="@nhanvien1, @bot.spam" onChange={event => setIgnored(event.target.value)} /></label>
    {error && <ErrorNotice message={error} />}
    <div className="studio-form-actions"><button type="button" className="studio-button" disabled={busy} onClick={onClose}>Hủy</button><button className="studio-button primary" disabled={busy}>{busy ? 'Đang lưu…' : 'Lưu cài đặt'}</button></div>
  </form></Modal>
}
