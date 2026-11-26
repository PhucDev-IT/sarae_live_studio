import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Check, Copy, FileText, Package, PenLine, Plus, Search, Sparkles, Trash2, Wand2 } from 'lucide-react'
import { studioClient, type Product, type SalesScript, type ScriptStyle } from '../../services/studio'
import { EmptyState, ErrorNotice, Modal, errorMessage } from './ui'

type Props = { products: Product[]; disabled: boolean; visible: boolean; onOpenAI: () => void; onNotice: (message: string) => void }
const styles: { value: ScriptStyle; label: string; hint: string }[] = [
  { value: 'friendly', label: 'Thân thiện', hint: 'Nói chuyện gần gũi' },
  { value: 'energetic', label: 'Sôi động', hint: 'Năng lượng cao, chốt nhanh' },
  { value: 'expert', label: 'Chuyên gia', hint: 'Giải thích kỹ, điềm tĩnh' },
]
const collator = new Intl.Collator('vi', { sensitivity: 'base', numeric: true })
const when = new Intl.DateTimeFormat('vi-VN', { dateStyle: 'short', timeStyle: 'short' })

export function ScriptsPanel({ products, disabled, visible, onOpenAI, onNotice }: Props) {
  const [scripts, setScripts] = useState<SalesScript[]>([])
  const [aiEnabled, setAiEnabled] = useState(false)
  const [loading, setLoading] = useState(true)
  const [productId, setProductId] = useState('')
  const [query, setQuery] = useState('')
  const [style, setStyle] = useState<ScriptStyle>('friendly')
  const [minutes, setMinutes] = useState(3)
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [writing, setWriting] = useState(false)
  const load = async () => {
    try {
      const [list, ai] = await Promise.all([studioClient.listScripts(), studioClient.aiState()])
      setScripts(list)
      setAiEnabled(ai.config.enabled)
      setError('')
    }
    catch (cause) { setError(errorMessage(cause)) }
    finally { setLoading(false) }
  }
  useEffect(() => { if (visible && !disabled) void load() }, [visible, disabled, products])
  const sorted = useMemo(() => [...products].sort((a, b) => collator.compare(a.name, b.name)), [products])
  useEffect(() => {
    if (!sorted.length) setProductId('')
    else if (!productId || !sorted.some(product => product.id === productId)) setProductId(sorted[0].id)
  }, [sorted, productId])
  const counts = useMemo(() => {
    const result = new Map<string, number>()
    for (const script of scripts) result.set(script.product_id, (result.get(script.product_id) ?? 0) + 1)
    return result
  }, [scripts])
  const product = sorted.find(item => item.id === productId)
  const shown = scripts.filter(script => script.product_id === productId)
  const listed = sorted.filter(item => item.name.toLocaleLowerCase('vi').includes(query.trim().toLocaleLowerCase('vi')))
  const thinInfo = product && !product.description.trim() && !product.facts.sizes.length && !product.facts.material && !Object.keys(product.facts.extras).length

  const generate = async (event: FormEvent) => {
    event.preventDefault()
    if (!product || busy || disabled) return
    setBusy(true)
    setError('')
    try {
      const result = await studioClient.generateScript({ product_id: product.id, style, duration_minutes: minutes, notes: notes.trim() })
      setScripts(current => [result.script, ...current])
      onNotice(`Đã tạo kịch bản trong ${(result.usage.latency_ms / 1000).toLocaleString('vi-VN', { maximumFractionDigits: 1 })} giây.`)
    } catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy(false) }
  }

  if (!products.length) return <>
    <div className="studio-page-heading"><div><h1>Kịch bản LIVE</h1><p>Lời dẫn bán hàng cho từng sản phẩm, do AI soạn hoặc bạn tự viết.</p></div></div>
    <section className="studio-card"><EmptyState icon={<FileText size={30} />} title="Chưa có sản phẩm để viết kịch bản" description="Đồng bộ sản phẩm từ TikTok trước, sau đó quay lại đây để tạo kịch bản." /></section>
  </>
  return <>
    <div className="studio-page-heading"><div><h1>Kịch bản LIVE</h1><p>Lời dẫn bán hàng cho từng sản phẩm, do AI soạn hoặc bạn tự viết.</p></div></div>
    <div className="studio-scripts-layout">
      <aside className="studio-card studio-script-products">
        <label className="studio-search"><Search size={16} /><input aria-label="Tìm sản phẩm" placeholder="Tìm sản phẩm…" value={query} onChange={event => setQuery(event.target.value)} /></label>
        <ul>{listed.map(item => <li key={item.id}><button className={item.id === productId ? 'active' : ''} aria-current={item.id === productId ? 'true' : undefined} onClick={() => { setProductId(item.id); setError('') }}>
          <span className="studio-script-thumb">{item.thumbnail_url ? <img src={item.thumbnail_url} alt="" loading="lazy" /> : <Package size={16} />}</span>
          <span className="studio-grow">{item.name}</span><small>{counts.get(item.id) ?? 0}</small>
        </button></li>)}</ul>
      </aside>
      <div className="studio-script-main">
        {product && <form className="studio-card studio-generator" onSubmit={event => void generate(event)}>
          <header className="studio-card-heading"><Wand2 size={19} /><div><h2>Soạn kịch bản cho “{product.name}”</h2><p>AI chỉ dùng giá, tồn kho, mô tả và thông tin chi tiết của sản phẩm.</p></div></header>
          {thinInfo && <div className="studio-callout warning"><Sparkles size={16} /><span>Sản phẩm chưa có mô tả hay thông tin chi tiết, kịch bản sẽ khá chung chung. Bổ sung ở trang Sản phẩm để AI viết cụ thể hơn.</span></div>}
          <div className="studio-form-row">
            <div className="studio-field">Giọng điệu<div className="studio-segmented wide" role="radiogroup" aria-label="Giọng điệu">{styles.map(item => <button type="button" role="radio" aria-checked={style === item.value} key={item.value} title={item.hint} className={style === item.value ? 'active' : ''} disabled={busy} onClick={() => setStyle(item.value)}>{item.label}</button>)}</div></div>
            <label className="studio-field narrow">Thời lượng<select value={minutes} disabled={busy} onChange={event => setMinutes(Number(event.target.value))}>{[1, 2, 3, 5, 8, 10, 15].map(value => <option key={value} value={value}>{value} phút</option>)}</select></label>
          </div>
          <label className="studio-field">Ghi chú cho AI<input value={notes} maxLength={1000} disabled={busy} placeholder="Ví dụ: nhấn mạnh vải mát, hợp đi làm; nhắc khách bấm giỏ hàng số 3" onChange={event => setNotes(event.target.value)} /></label>
          {error && <ErrorNotice message={error} />}
          <div className="studio-form-actions spread">
            {aiEnabled ? <span className="studio-muted">Kịch bản mới được lưu tự động, bạn có thể sửa sau.</span> : <button type="button" className="studio-button ghost" onClick={onOpenAI}>AI đang tắt, mở Cài đặt AI</button>}
            <div className="studio-actions"><button type="button" className="studio-button" disabled={disabled || busy} onClick={() => setWriting(true)}><Plus size={15} />Tự viết</button><button className="studio-button primary" disabled={disabled || busy || !aiEnabled}><Wand2 size={15} className={busy ? 'studio-spin' : ''} />{busy ? 'AI đang viết…' : 'Tạo bằng AI'}</button></div>
          </div>
        </form>}
        {loading ? <div className="studio-card studio-empty" role="status">Đang tải kịch bản…</div>
          : !shown.length ? <section className="studio-card"><EmptyState icon={<FileText size={28} />} title="Sản phẩm này chưa có kịch bản" description="Chọn giọng điệu rồi bấm Tạo bằng AI, hoặc tự viết lời dẫn của bạn." /></section>
          : shown.map(script => <ScriptCard key={script.id} script={script} disabled={disabled} onChanged={updated => setScripts(current => updated ? current.map(item => item.id === updated.id ? updated : item) : current.filter(item => item.id !== script.id))} onNotice={onNotice} />)}
      </div>
    </div>
    {writing && product && <ManualScript product={product} onClose={() => setWriting(false)} onSaved={script => { setScripts(current => [script, ...current]); setWriting(false); onNotice('Đã lưu kịch bản.') }} />}
  </>
}

function ScriptBody({ body }: { body: string }) {
  return <div className="studio-script-body">{body.split('\n').map((line, index) => {
    const text = line.trim()
    if (!text) return <br key={index} />
    const heading = text.length <= 60 && text === text.toLocaleUpperCase('vi') && /\p{L}/u.test(text)
    return heading ? <h4 key={index}>{text.replace(/:$/, '')}</h4> : <p key={index}>{text}</p>
  })}</div>
}

function ScriptCard({ script, disabled, onChanged, onNotice }: { script: SalesScript; disabled: boolean; onChanged: (script: SalesScript | null) => void; onNotice: (message: string) => void }) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(script.title)
  const [body, setBody] = useState(script.body)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const save = async () => {
    if (busy || !title.trim() || !body.trim()) return
    setBusy(true)
    setError('')
    try { onChanged(await studioClient.updateScript(script.id, { title: title.trim(), body })); setEditing(false); onNotice('Đã lưu kịch bản.') }
    catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy(false) }
  }
  const remove = async () => {
    setBusy(true)
    try { await studioClient.deleteScript(script.id); onChanged(null); onNotice('Đã xóa kịch bản.') }
    catch (cause) { setError(errorMessage(cause)); setBusy(false) }
  }
  const copy = async () => {
    try { await navigator.clipboard.writeText(`${script.title}\n\n${script.body}`); setCopied(true); window.setTimeout(() => setCopied(false), 1500) }
    catch { setError('Không sao chép được. Hãy chọn và sao chép thủ công.') }
  }
  return <article className="studio-card studio-script-card">
    <header>
      {editing ? <input className="studio-script-title-input" aria-label="Tên kịch bản" value={title} maxLength={200} disabled={busy} onChange={event => setTitle(event.target.value)} />
        : <div className="studio-grow"><h3>{script.title}</h3><small>{script.source === 'ai' ? <><Sparkles size={12} />AI soạn</> : <><PenLine size={12} />Tự viết</>} · sửa lúc {when.format(new Date(script.updated_at))}</small></div>}
      <div className="studio-actions">
        {editing ? <><button className="studio-button" disabled={busy} onClick={() => { setEditing(false); setTitle(script.title); setBody(script.body); setError('') }}>Hủy</button><button className="studio-button primary" disabled={disabled || busy || !title.trim() || !body.trim()} onClick={() => void save()}>{busy ? 'Đang lưu…' : 'Lưu'}</button></>
          : <><button className="studio-button ghost icon-only" title="Sao chép" aria-label={`Sao chép ${script.title}`} onClick={() => void copy()}>{copied ? <Check size={16} /> : <Copy size={16} />}</button><button className="studio-button ghost icon-only" disabled={disabled} title="Sửa" aria-label={`Sửa ${script.title}`} onClick={() => setEditing(true)}><PenLine size={16} /></button><button className="studio-button ghost icon-only danger" disabled={disabled || busy} title="Xóa" aria-label={`Xóa ${script.title}`} onClick={() => setConfirming(true)}><Trash2 size={16} /></button></>}
      </div>
    </header>
    {editing ? <textarea className="studio-script-editor" aria-label="Nội dung kịch bản" rows={Math.min(24, Math.max(8, body.split('\n').length + 1))} maxLength={20000} value={body} disabled={busy} onChange={event => setBody(event.target.value)} /> : <ScriptBody body={script.body} />}
    {error && <ErrorNotice message={error} />}
    {confirming && <Modal title="Xóa kịch bản?" onClose={() => { if (!busy) setConfirming(false) }}><div className="studio-form"><p>Kịch bản <strong>{script.title}</strong> sẽ bị xóa vĩnh viễn.</p><div className="studio-form-actions"><button className="studio-button" disabled={busy} onClick={() => setConfirming(false)}>Giữ lại</button><button className="studio-button danger-solid" disabled={busy} onClick={() => void remove()}>{busy ? 'Đang xóa…' : 'Xóa kịch bản'}</button></div></div></Modal>}
  </article>
}

function ManualScript({ product, onClose, onSaved }: { product: Product; onClose: () => void; onSaved: (script: SalesScript) => void }) {
  const [title, setTitle] = useState(`Kịch bản ${product.name}`.slice(0, 200))
  const [body, setBody] = useState('MỞ ĐẦU\n\nGIỚI THIỆU SẢN PHẨM\n\nCHỐT ĐƠN\n')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    try { onSaved(await studioClient.createScript({ product_id: product.id, title: title.trim(), body })) }
    catch (cause) { setError(errorMessage(cause)); setBusy(false) }
  }
  return <Modal title="Tự viết kịch bản" onClose={() => { if (!busy) onClose() }} wide><form className="studio-form" onSubmit={event => void save(event)}>
    <label className="studio-field">Tên kịch bản<input data-autofocus autoFocus value={title} maxLength={200} disabled={busy} onChange={event => setTitle(event.target.value)} /></label>
    <label className="studio-field">Nội dung<textarea rows={14} maxLength={20000} value={body} disabled={busy} onChange={event => setBody(event.target.value)} /></label>
    <p className="studio-field-hint">Dòng viết IN HOA được hiển thị như tiêu đề phần.</p>
    {error && <ErrorNotice message={error} />}
    <div className="studio-form-actions"><button type="button" className="studio-button" disabled={busy} onClick={onClose}>Hủy</button><button className="studio-button primary" disabled={busy || !title.trim() || !body.trim()}>{busy ? 'Đang lưu…' : 'Lưu kịch bản'}</button></div>
  </form></Modal>
}
