import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { AlertTriangle, BrainCircuit, Cloud, Cpu, KeyRound, MessageCircleReply, PlugZap, Send, ShieldCheck, Sparkles, Trash2 } from 'lucide-react'
import { studioClient, type AIConfig, type AIProvider, type AIReply, type AIState, type Product } from '../../services/studio'
import { ErrorNotice, errorMessage } from './ui'

type Props = { products: Product[]; disabled: boolean; visible: boolean; onNotice: (message: string) => void }

const providerInfo: Record<AIProvider, { title: string; tag: string; body: string; icon: typeof Cloud }> = {
  gemini: { title: 'Google Gemini', tag: 'Cloud · cần API key', body: 'Nhanh, rẻ, hợp trả lời bình luận. Lấy key tại Google AI Studio.', icon: Sparkles },
  openai_compatible: { title: 'OpenAI-compatible', tag: 'Cloud · cần API key', body: 'OpenAI, OpenRouter hoặc dịch vụ có API /chat/completions qua HTTPS.', icon: Cloud },
  seller_brain: { title: 'Seller Brain', tag: 'Trên máy · không cần key', body: 'Model Qwen chạy local bằng llama.cpp (sarae-ai-live-studio). Không gửi dữ liệu ra ngoài.', icon: Cpu },
}
const intentLabels: Record<string, string> = {
  ASK_PRICE: 'Hỏi giá', ASK_SIZE: 'Hỏi size', ASK_COLOR: 'Hỏi màu', ASK_MATERIAL: 'Hỏi chất liệu', ASK_SHIPPING: 'Hỏi giao hàng',
  ASK_STOCK: 'Hỏi tồn kho', ASK_RETURN: 'Đổi trả', ASK_PROMO: 'Khuyến mãi', ASK_CARE: 'Bảo quản', SHOW_PRODUCT: 'Xem sản phẩm',
  ORDER: 'Chốt đơn', COMPLAINT: 'Phàn nàn', GREETING: 'Chào hỏi', GENERAL: 'Chung', OTHER: 'Khác',
}
const samples = ['Giá bao nhiêu vậy shop?', '50kg 1m58 mặc size gì ạ?', 'Có màu đen không shop?', 'Bao lâu thì nhận được hàng?', 'Vải có nóng không?']
type Turn = { id: number; comment: string; username: string; product: string | null; result?: AIReply; error?: string }

export function AIPanel({ products, disabled, visible, onNotice }: Props) {
  const [state, setState] = useState<AIState | null>(null)
  const [draft, setDraft] = useState<AIConfig | null>(null)
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState<'' | 'save' | 'key' | 'test'>('')
  const [error, setError] = useState('')
  const [testResult, setTestResult] = useState('')
  const load = async () => {
    try {
      const next = await studioClient.aiState()
      setState(next)
      setDraft(current => current ?? next.config)
      setError('')
    } catch (cause) { setError(errorMessage(cause)) }
  }
  useEffect(() => { if (visible && !disabled) void load() }, [visible, disabled])
  const dirty = !!(state && draft && JSON.stringify(state.config) !== JSON.stringify(draft))
  const providerSpec = draft && state ? state.providers[draft.provider] : null
  const savedKey = draft && state ? state.keys[draft.provider] : undefined

  const chooseProvider = (provider: AIProvider) => {
    if (!state || !draft || provider === draft.provider) return
    const spec = state.providers[provider]
    const keepSaved = state.config.provider === provider
    setDraft({ ...draft, provider, model: keepSaved ? state.config.model : spec.default_model, base_url: keepSaved ? state.config.base_url : spec.default_base_url })
    setKey('')
    setTestResult('')
  }
  const save = async (event?: FormEvent) => {
    event?.preventDefault()
    if (!draft || busy || disabled) return
    setBusy('save')
    setError('')
    try {
      const next = await studioClient.aiConfigure(draft)
      setState(next)
      setDraft(next.config)
      onNotice(next.config.enabled ? 'Đã lưu cài đặt AI. AI đang bật.' : 'Đã lưu cài đặt AI.')
    } catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy('') }
  }
  const saveKey = async () => {
    if (!draft || !key.trim() || busy || disabled) return
    setBusy('key')
    setError('')
    try {
      setState(await studioClient.aiSetKey(draft.provider, key.trim()))
      setKey('')
      onNotice('Đã lưu API key, được mã hóa bằng tài khoản Windows của bạn.')
    } catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy('') }
  }
  const removeKey = async () => {
    if (!draft || busy || disabled) return
    setBusy('key')
    setError('')
    try { setState(await studioClient.aiDeleteKey(draft.provider)); onNotice('Đã xóa API key khỏi máy.') }
    catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy('') }
  }
  const test = async () => {
    if (busy || disabled) return
    if (dirty) { setError('Hãy lưu cài đặt trước khi kiểm tra kết nối.'); return }
    setBusy('test')
    setError('')
    setTestResult('')
    try {
      const result = await studioClient.aiTest()
      setTestResult(`Kết nối tốt · ${result.usage.model} · ${result.usage.latency_ms.toLocaleString('vi-VN')} ms`)
    } catch (cause) { setError(errorMessage(cause)) }
    finally {
      // Refresh counters either way, without clearing the error just shown.
      try { setState(await studioClient.aiState()) } catch { /* The connection banner covers core loss. */ }
      setBusy('')
    }
  }

  return <>
    <div className="studio-page-heading"><div><h1>Trợ lý AI</h1><p>Chọn bộ não trả lời bình luận và thử với sản phẩm thật trước khi LIVE.</p></div></div>
    <div className="studio-ai-layout">
      <section className="studio-card studio-ai-settings">
        <header className="studio-card-heading"><BrainCircuit size={19} /><div><h2>Kết nối AI</h2><p>AI chỉ dùng thông tin sản phẩm bạn cung cấp, không tự gửi chat hay ghim sản phẩm.</p></div></header>
        {!draft || !state ? <div className="studio-empty" role="status">{error ? <ErrorNotice message={error} /> : 'Đang tải cài đặt AI…'}</div> : <form className="studio-form" onSubmit={event => void save(event)}>
          <div className="studio-provider-grid" role="radiogroup" aria-label="Nhà cung cấp AI">{(Object.keys(providerInfo) as AIProvider[]).map(provider => {
            const info = providerInfo[provider]
            const Icon = info.icon
            const selected = draft.provider === provider
            return <button type="button" role="radio" aria-checked={selected} key={provider} className={`studio-provider${selected ? ' selected' : ''}`} disabled={disabled || !!busy} onClick={() => chooseProvider(provider)}>
              <span className="studio-provider-icon"><Icon size={18} /></span><strong>{info.title}</strong><small>{info.tag}</small><span>{info.body}</span>
            </button>
          })}</div>
          {draft.provider === 'seller_brain' && <div className="studio-callout warning"><AlertTriangle size={17} /><span>Seller Brain dùng chung GPU với OBS và giọng đọc. Chạy <code>seller-brain serve</code> trước khi dùng và kiểm tra máy còn đủ VRAM khi đang LIVE.</span></div>}
          <div className="studio-form-row">
            <label className="studio-field">Model ID<input value={draft.model} disabled={disabled || !!busy} placeholder={providerSpec?.default_model || 'ví dụ: gpt-4.1-mini'} onChange={event => setDraft({ ...draft, model: event.target.value.trim() })} /></label>
            {draft.provider !== 'gemini' && <label className="studio-field">Địa chỉ API<input value={draft.base_url} disabled={disabled || !!busy} placeholder={providerSpec?.default_base_url} onChange={event => setDraft({ ...draft, base_url: event.target.value.trim() })} /></label>}
          </div>
          {providerSpec?.needs_key && <div className="studio-key-box">
            <KeyRound size={17} />
            {savedKey?.saved ? <><div className="studio-grow"><strong>Đã lưu API key</strong><span>Kết thúc bằng ••••{savedKey.last4} · mã hóa bằng DPAPI, chỉ tài khoản Windows này đọc được.</span></div><button type="button" className="studio-button ghost danger" disabled={disabled || !!busy} onClick={() => void removeKey()}><Trash2 size={15} />Xóa key</button></>
              : <><input type="password" autoComplete="off" spellCheck={false} aria-label="API key" placeholder={`Dán API key ${providerInfo[draft.provider].title}`} value={key} disabled={disabled || !!busy} onChange={event => setKey(event.target.value)} /><button type="button" className="studio-button" disabled={disabled || !!busy || key.trim().length < 8} onClick={() => void saveKey()}>{busy === 'key' ? 'Đang lưu…' : 'Lưu key'}</button></>}
          </div>}
          <div className="studio-form-row">
            <label className="studio-field">Độ dài trả lời tối đa (token)<input type="number" min={60} max={3000} step={20} value={draft.max_output_tokens} disabled={disabled || !!busy} onChange={event => setDraft({ ...draft, max_output_tokens: Number(event.target.value) })} /></label>
            <label className="studio-field">Độ sáng tạo <span className="studio-muted">{draft.temperature.toFixed(1)}</span><input type="range" min={0} max={1.5} step={0.1} value={draft.temperature} disabled={disabled || !!busy} onChange={event => setDraft({ ...draft, temperature: Number(event.target.value) })} /></label>
          </div>
          <label className="studio-switch studio-switch-field"><input type="checkbox" role="switch" checked={draft.enabled} disabled={disabled || !!busy} onChange={event => setDraft({ ...draft, enabled: event.target.checked })} /><span aria-hidden="true" /><small>Bật AI cho Studio</small></label>
          {error && <ErrorNotice message={error} />}
          {testResult && <p className="studio-test-ok" role="status"><ShieldCheck size={16} />{testResult}</p>}
          <div className="studio-form-actions spread">
            <span className="studio-muted">{state.usage.requests.toLocaleString('vi-VN')} lượt gọi · {(state.usage.input_tokens + state.usage.output_tokens).toLocaleString('vi-VN')} token{state.usage.errors ? ` · ${state.usage.errors} lỗi` : ''} từ lúc mở Core</span>
            <div className="studio-actions"><button type="button" className="studio-button" disabled={disabled || !!busy} onClick={() => void test()}><PlugZap size={15} />{busy === 'test' ? 'Đang kiểm tra…' : 'Kiểm tra kết nối'}</button><button className="studio-button primary" disabled={disabled || !!busy || !dirty}>{busy === 'save' ? 'Đang lưu…' : 'Lưu cài đặt'}</button></div>
          </div>
        </form>}
      </section>
      <Playground products={products} disabled={disabled} enabled={!!state?.config.enabled && !dirty} />
    </div>
  </>
}

function Playground({ products, disabled, enabled }: { products: Product[]; disabled: boolean; enabled: boolean }) {
  const [productId, setProductId] = useState('')
  const [username, setUsername] = useState('')
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [turns, setTurns] = useState<Turn[]>([])
  const names = useMemo(() => new Map(products.map(product => [product.id, product.name])), [products])
  const sorted = useMemo(() => [...products].sort((a, b) => a.name.localeCompare(b.name, 'vi', { sensitivity: 'base', numeric: true })), [products])
  useEffect(() => { if (productId && !names.has(productId)) setProductId('') }, [names, productId])
  const ask = async (event?: FormEvent, text = comment) => {
    event?.preventDefault()
    if (busy || disabled || !text.trim()) return
    const turn: Turn = { id: Date.now(), comment: text.trim(), username: username.trim(), product: productId || null }
    setTurns(current => [turn, ...current].slice(0, 6))
    setBusy(true)
    setComment('')
    try {
      const result = await studioClient.aiReply({ comment: turn.comment, ...(turn.product ? { product_id: turn.product } : {}), ...(turn.username ? { username: turn.username } : {}) })
      setTurns(current => current.map(item => item.id === turn.id ? { ...item, result } : item))
    } catch (cause) {
      setTurns(current => current.map(item => item.id === turn.id ? { ...item, error: errorMessage(cause) } : item))
    } finally { setBusy(false) }
  }
  return <section className="studio-card studio-playground">
    <header className="studio-card-heading"><MessageCircleReply size={19} /><div><h2>Thử trả lời bình luận</h2><p>Câu trả lời chỉ hiển thị ở đây, không gửi lên TikTok.</p></div></header>
    <form className="studio-form" onSubmit={event => void ask(event)}>
      <div className="studio-form-row">
        <label className="studio-field">Sản phẩm đang giới thiệu<select value={productId} disabled={disabled || busy} onChange={event => setProductId(event.target.value)}><option value="">Không chọn sản phẩm</option>{sorted.map(product => <option key={product.id} value={product.id}>{product.name}</option>)}</select></label>
        <label className="studio-field narrow">Tên khách<input value={username} maxLength={100} disabled={disabled || busy} placeholder="lan.nguyen" onChange={event => setUsername(event.target.value)} /></label>
      </div>
      <div className="studio-sample-list" aria-label="Bình luận mẫu">{samples.map(sample => <button type="button" key={sample} className="studio-chip" disabled={disabled || busy || !enabled} onClick={() => void ask(undefined, sample)}>{sample}</button>)}</div>
      <div className="studio-ask-row"><input aria-label="Bình luận của khách" value={comment} maxLength={1000} disabled={disabled || busy || !enabled} placeholder={enabled ? 'Gõ bình luận như khách trên LIVE…' : 'Bật AI và lưu cài đặt để thử'} onChange={event => setComment(event.target.value)} /><button className="studio-button primary" disabled={disabled || busy || !enabled || !comment.trim()}><Send size={15} />{busy ? 'Đang nghĩ…' : 'Hỏi AI'}</button></div>
    </form>
    <div className="studio-chat-log" aria-live="polite">
      {!turns.length && <p className="studio-muted studio-chat-empty">Chọn sản phẩm, rồi bấm một bình luận mẫu để xem AI trả lời thế nào.</p>}
      {turns.map(turn => <article key={turn.id} className="studio-chat-turn">
        <div className="studio-bubble viewer"><small>{turn.username || 'Khách'}{turn.product ? ` · ${names.get(turn.product) ?? 'sản phẩm đã xóa'}` : ''}</small>{turn.comment}</div>
        {turn.error ? <div className="studio-bubble error">{turn.error}</div>
          : !turn.result ? <div className="studio-bubble shop pending"><span className="studio-typing"><i /><i /><i /></span></div>
          : <div className={`studio-bubble shop${turn.result.needs_review ? ' review' : ''}`}>
            {turn.result.reply || <span className="studio-muted">AI không trả lời bình luận này.</span>}
            <footer><span className="studio-tag neutral">{intentLabels[turn.result.intent] ?? turn.result.intent}</span>{turn.result.needs_review && <span className="studio-tag warning">Cần người duyệt</span>}<span>{turn.result.usage.latency_ms.toLocaleString('vi-VN')} ms · {turn.result.usage.output_tokens} token</span></footer>
            {turn.result.reason && <p className="studio-reason">{turn.result.reason}</p>}
          </div>}
      </article>)}
    </div>
  </section>
}
