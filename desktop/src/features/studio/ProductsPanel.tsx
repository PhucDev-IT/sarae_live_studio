import { useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { CheckCircle2, Film, LogIn, Package, Pencil, PlugZap, RefreshCw, Search, ShoppingBag, Sparkles, Trash2 } from 'lucide-react'
import { studioClient, type Media, type Product, type ProductFacts, type TikTokStatus } from '../../services/studio'
import { FactsEditor, factsProblem } from './FactsEditor'
import { EmptyState, ErrorNotice, Modal, errorMessage } from './ui'

type Props = {
  products: Product[]; media: Media[]; tiktok: TikTokStatus | null; loading: boolean; disabled: boolean
  onRefresh: () => Promise<void>; onTiktok: (status: TikTokStatus) => void
  onOpenMedia: (productId: string) => void; onNotice: (message: string) => void
}
type Filter = 'all' | 'no_description' | 'no_video'
const filters: { value: Filter; label: string }[] = [
  { value: 'all', label: 'Tất cả' }, { value: 'no_description', label: 'Chưa có thông tin' }, { value: 'no_video', label: 'Chưa có video' },
]
const money = new Intl.NumberFormat('vi-VN', { style: 'currency', currency: 'VND', maximumFractionDigits: 0 })
const emptyFacts: ProductFacts = { sizes: [], colors: [], material: '', shipping: '', notes: '', extras: {} }

function factsCount(facts: ProductFacts | undefined): number {
  if (!facts) return 0
  return Number(facts.sizes.length > 0) + Number(facts.colors.length > 0) + Number(!!facts.material) + Number(!!facts.shipping)
    + Number(!!facts.notes) + Object.keys(facts.extras).length
}

function aiInfo(product: Product): 'full' | 'partial' | 'none' {
  const described = !!product.description.trim()
  const detailed = factsCount(product.facts) > 0
  return described && detailed ? 'full' : described || detailed ? 'partial' : 'none'
}

const collator = new Intl.Collator('vi', { sensitivity: 'base', numeric: true })
const dateTime = new Intl.DateTimeFormat('vi-VN', { dateStyle: 'short', timeStyle: 'short' })

export function ProductsPanel({ products, media, tiktok, loading, disabled, onRefresh, onTiktok, onOpenMedia, onNotice }: Props) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [editing, setEditing] = useState<Product | null>(null)
  const [deleting, setDeleting] = useState<Product | null>(null)
  const [description, setDescription] = useState('')
  const [facts, setFacts] = useState<ProductFacts>(emptyFacts)
  const [tab, setTab] = useState<'description' | 'facts'>('description')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const counts = useMemo(() => {
    const result = new Map<string, number>()
    for (const item of media) if (item.product_id) result.set(item.product_id, (result.get(item.product_id) ?? 0) + 1)
    return result
  }, [media])
  const visible = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase('vi')
    return products.filter(product => (filter === 'all' || (filter === 'no_description' ? aiInfo(product) === 'none' : !counts.get(product.id)))
      && `${product.name} ${product.category} ${product.tiktok_product_id ?? ''}`.toLocaleLowerCase('vi').includes(needle))
      .sort((a, b) => Number(a.source !== 'tiktok') - Number(b.source !== 'tiktok') || collator.compare(a.name, b.name))
  }, [products, query, filter, counts])
  const describedCount = products.filter(product => aiInfo(product) !== 'none').length

  const openEditor = (product: Product, startTab: 'description' | 'facts' = 'description') => {
    setDescription(product.description)
    setFacts(structuredClone(product.facts ?? emptyFacts))
    setTab(startTab)
    setError('')
    setEditing(product)
  }
  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (busy || disabled || !editing) return
    const cleaned = { ...facts, extras: Object.fromEntries(Object.entries(facts.extras).filter(([key, text]) => key && text.trim())) }
    const problem = factsProblem(cleaned)
    if (problem) { setTab('facts'); setError(problem); return }
    setBusy(true)
    setError('')
    try {
      await studioClient.updateProduct(editing.id, { description: description.trim(), facts: cleaned })
      setEditing(null)
      onNotice('Đã lưu thông tin sản phẩm cho AI.')
      await onRefresh()
    } catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy(false) }
  }
  const remove = async () => {
    if (!deleting || busy || disabled) return
    setBusy(true)
    setError('')
    try {
      await studioClient.deleteProduct(deleting.id)
      setDeleting(null)
      onNotice('Đã xóa sản phẩm khỏi Studio.')
      await onRefresh()
    } catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy(false) }
  }

  return <>
    <div className="studio-page-heading"><div><h1>Sản phẩm</h1><p>Đồng bộ từ giỏ hàng TikTok LIVE, bổ sung mô tả để AI tư vấn đúng.</p></div></div>
    <TikTokCard status={tiktok} disabled={disabled} onStatus={onTiktok} onRefresh={onRefresh} onNotice={onNotice} />
    <section className="studio-card">
      <div className="studio-toolbar">
        <label className="studio-search"><Search size={17} /><input aria-label="Tìm sản phẩm" placeholder="Tìm theo tên, danh mục hoặc mã TikTok…" value={query} onChange={event => setQuery(event.target.value)} /></label>
        <div className="studio-segmented" role="group" aria-label="Lọc sản phẩm">{filters.map(item => <button key={item.value} aria-pressed={filter === item.value} className={filter === item.value ? 'active' : ''} onClick={() => setFilter(item.value)}>{item.label}</button>)}</div>
        <span className="studio-muted">{describedCount}/{products.length} có thông tin cho AI</span>
      </div>
      {loading ? <div className="studio-empty" role="status">Đang tải sản phẩm…</div>
        : !products.length ? <EmptyState icon={<ShoppingBag size={30} />} title="Chưa có sản phẩm nào" description="Kết nối TikTok LIVE Console ở trên rồi bấm Đồng bộ sản phẩm. Studio lấy tên, ảnh, giá, tồn kho và danh mục từ giỏ hàng LIVE." />
        : !visible.length ? <EmptyState icon={<Package size={30} />} title="Không có sản phẩm phù hợp" description="Thử đổi từ khóa hoặc bộ lọc." action={<button className="studio-button" onClick={() => { setQuery(''); setFilter('all') }}>Xóa bộ lọc</button>} />
        : <div className="studio-table-wrap"><table className="studio-table studio-product-table"><thead><tr><th>Sản phẩm</th><th>Giá bán</th><th>Tồn kho</th><th>Thông tin cho AI</th><th>Video</th><th><span className="sr-only">Thao tác</span></th></tr></thead><tbody>{visible.map(product => {
          const videoCount = counts.get(product.id) ?? 0
          return <tr key={product.id}>
            <td><div className="studio-product-name"><ProductThumb product={product} /><div><strong className="studio-row-title">{product.name}</strong><p className="studio-row-description">{product.source === 'tiktok' ? <>{product.category || 'TikTok Shop'}<span className="studio-dot-sep">·</span>#{product.tiktok_product_id}</> : <span className="studio-tag warning">Tạo tay trước đây · không đồng bộ</span>}</p></div></div></td>
            <td className="studio-nowrap"><PriceCell product={product} /></td>
            <td className="studio-nowrap"><StockTag stock={product.stock_num} /></td>
            <td>{(() => {
              const info = aiInfo(product)
              if (info === 'none') return <button className="studio-button ghost studio-inline-action" disabled={disabled} onClick={() => openEditor(product)}><Sparkles size={14} />Thêm thông tin</button>
              if (info === 'full') return <span className="studio-tag success">Đầy đủ · {factsCount(product.facts)} dữ kiện</span>
              return <button className="studio-tag honey studio-tag-button" disabled={disabled} title="Bổ sung phần còn thiếu" onClick={() => openEditor(product, product.description.trim() ? 'facts' : 'description')}>{product.description.trim() ? 'Thiếu thông tin chi tiết' : 'Thiếu mô tả'}</button>
            })()}</td>
            <td><button className="studio-button ghost studio-video-link" onClick={() => onOpenMedia(product.id)}><Film size={15} />{videoCount} video</button></td>
            <td><div className="studio-actions"><button className="studio-button ghost icon-only" disabled={disabled || busy} title={`Sửa mô tả ${product.name}`} aria-label={`Sửa mô tả ${product.name}`} onClick={() => openEditor(product)}><Pencil size={16} /></button><button className="studio-button ghost icon-only danger" disabled={disabled || busy} title={`Xóa ${product.name}`} aria-label={`Xóa ${product.name}`} onClick={() => { setError(''); setDeleting(product) }}><Trash2 size={16} /></button></div></td>
          </tr>
        })}</tbody></table></div>}
    </section>
    {editing && <Modal title="Thông tin sản phẩm cho AI" onClose={() => { if (!busy) setEditing(null) }} wide><form className="studio-form" onSubmit={event => void save(event)}>
      <div className="studio-product-summary"><ProductThumb product={editing} large /><div><strong>{editing.name}</strong><span><PriceCell product={editing} /> · <StockTag stock={editing.stock_num} plain /></span><small>{editing.source === 'tiktok' ? 'Tên, giá, tồn kho và ảnh được cập nhật mỗi lần đồng bộ TikTok.' : 'Sản phẩm tạo tay trước đây, không được đồng bộ.'}</small></div></div>
      <div className="studio-tabs" role="tablist" aria-label="Thông tin sản phẩm">
        <button type="button" role="tab" aria-selected={tab === 'description'} className={tab === 'description' ? 'active' : ''} onClick={() => setTab('description')}>Mô tả tư vấn</button>
        <button type="button" role="tab" aria-selected={tab === 'facts'} className={tab === 'facts' ? 'active' : ''} onClick={() => setTab('facts')}>Thông tin chi tiết{factsCount(facts) ? <small>{factsCount(facts)}</small> : null}</button>
      </div>
      <div role="tabpanel" hidden={tab !== 'description'}>
        <label className="studio-field">Mô tả & lời tư vấn<textarea data-autofocus autoFocus rows={9} maxLength={10000} value={description} disabled={busy} placeholder={'Điểm nổi bật, cách phối đồ, đối tượng phù hợp…\nAI chỉ khẳng định những gì có ở đây, trong thông tin chi tiết và trên TikTok.'} onChange={event => setDescription(event.target.value)} /></label>
        <p className="studio-field-hint">{description.length.toLocaleString('vi-VN')}/10.000 ký tự</p>
      </div>
      <div role="tabpanel" hidden={tab !== 'facts'}><FactsEditor value={facts} onChange={setFacts} disabled={busy} /></div>
      {error && <ErrorNotice message={error} />}
      {disabled && <ErrorNotice message="Studio Core đang mất kết nối. Kết nối lại trước khi lưu." />}
      <div className="studio-form-actions"><button type="button" className="studio-button" disabled={busy} onClick={() => setEditing(null)}>Hủy</button><button className="studio-button primary" disabled={busy || disabled}>{busy ? 'Đang lưu…' : 'Lưu thông tin'}</button></div>
    </form></Modal>}
    {deleting && <Modal title="Xóa sản phẩm khỏi Studio?" onClose={() => { if (!busy) setDeleting(null) }}><div className="studio-form"><p>Bạn sắp xóa <strong>{deleting.name}</strong> cùng mô tả đã viết. Sản phẩm trên TikTok không bị ảnh hưởng.</p><p className="studio-muted">{deleting.source === 'tiktok' ? 'Nếu sản phẩm vẫn còn trong giỏ LIVE, lần đồng bộ sau sẽ thêm lại với mô tả trống. ' : ''}Video đã nhập vẫn nằm trong thư viện và chỉ bỏ liên kết.</p>{error && <ErrorNotice message={error} />}<div className="studio-form-actions"><button className="studio-button" disabled={busy} onClick={() => setDeleting(null)}>Giữ lại</button><button className="studio-button danger-solid" disabled={busy || disabled} onClick={() => void remove()}>{busy ? 'Đang xóa…' : 'Xóa sản phẩm'}</button></div></div></Modal>}
  </>
}

function ProductThumb({ product, large = false }: { product: Product; large?: boolean }) {
  const [failed, setFailed] = useState('')
  const url = product.thumbnail_url
  return <span className={`studio-product-symbol${large ? ' large' : ''}`}>{url && failed !== url ? <img src={url} alt="" loading="lazy" onError={() => setFailed(url)} /> : <Package size={large ? 28 : 21} />}</span>
}

function PriceCell({ product }: { product: Product }) {
  if (product.price === null) return <span className="studio-muted">Chưa có giá</span>
  const discounted = product.origin_price !== null && product.origin_price > product.price
  return <span className="studio-price"><strong>{money.format(product.price)}</strong>{discounted && <s>{money.format(product.origin_price!)}</s>}</span>
}

function StockTag({ stock, plain = false }: { stock: number | null; plain?: boolean }) {
  if (stock === null) return <span className="studio-muted">—</span>
  const label = stock === 0 ? 'Hết hàng' : `Còn ${stock.toLocaleString('vi-VN')}`
  if (plain) return <>{label}</>
  return <span className={`studio-tag ${stock === 0 ? 'warning' : stock <= 5 ? 'honey' : 'neutral'}`}>{label}</span>
}

function TikTokCard({ status, disabled, onStatus, onRefresh, onNotice }: { status: TikTokStatus | null; disabled: boolean; onStatus: (status: TikTokStatus) => void; onRefresh: () => Promise<void>; onNotice: (message: string) => void }) {
  const [busy, setBusy] = useState<'' | 'login' | 'connect' | 'sync'>('')
  const [error, setError] = useState('')
  const account = status?.account
  const ready = Boolean(status?.browser_open && status.state === 'ready')
  const run = async (action: 'login' | 'connect' | 'sync') => {
    if (busy || disabled) return
    setBusy(action)
    setError('')
    try {
      if (action === 'sync') {
        const result = await studioClient.tiktokSync()
        const parts = [`${result.created} mới`, `${result.updated} cập nhật`]
        onNotice(`Đã đồng bộ ${result.total} sản phẩm (${parts.join(', ')})${result.missing ? `. ${result.missing} sản phẩm không còn trong giỏ LIVE vẫn được giữ.` : '.'}`)
        await onRefresh()
      } else {
        onStatus(action === 'login' ? await studioClient.tiktokLogin() : await studioClient.tiktokConnect())
      }
    } catch (cause) {
      setError(errorMessage(cause))
      try { onStatus(await studioClient.tiktokStatus()) } catch { /* Connection banner covers core loss. */ }
    } finally { setBusy('') }
  }
  let step: { title: string; body: string; actions: ReactNode }
  if (status?.login_open) {
    step = { title: 'Đăng nhập trong cửa sổ Chrome vừa mở', body: 'Đăng nhập tài khoản TikTok Shop, mở LIVE Console, rồi quay lại đây bấm Tiếp tục. Studio sẽ tự đóng cửa sổ đăng nhập.', actions: <button className="studio-button primary" disabled={disabled || !!busy} onClick={() => void run('connect')}><PlugZap size={16} />{busy === 'connect' ? 'Đang kết nối…' : 'Tiếp tục'}</button> }
  } else if (ready) {
    step = { title: 'Đã kết nối TikTok LIVE Console', body: 'Đồng bộ sẽ tải lại LIVE Console để đọc giỏ hàng mới nhất. Mô tả, video và người giới thiệu bạn đã gắn được giữ nguyên.', actions: <button className="studio-button primary" disabled={disabled || !!busy || status?.syncing} onClick={() => void run('sync')}><RefreshCw size={16} className={busy === 'sync' ? 'studio-spin' : ''} />{busy === 'sync' ? 'Đang đồng bộ…' : 'Đồng bộ sản phẩm'}</button> }
  } else if (status?.browser_open && status.state === 'login_required') {
    step = { title: 'Phiên TikTok đã hết hạn', body: 'Đăng nhập lại trong Chrome của Studio để tiếp tục đồng bộ.', actions: <button className="studio-button primary" disabled={disabled || !!busy} onClick={() => void run('login')}><LogIn size={16} />Đăng nhập lại</button> }
  } else {
    step = { title: account ? 'Kết nối lại để đồng bộ' : 'Kết nối TikTok LIVE Console', body: account ? 'Chrome của Studio đã lưu phiên đăng nhập. Bấm Kết nối để mở lại LIVE Console.' : 'Studio mở một cửa sổ Chrome riêng để bạn tự đăng nhập. Mật khẩu và cookie chỉ nằm trong hồ sơ Chrome trên máy này.', actions: <>{account && <button className="studio-button primary" disabled={disabled || !!busy} onClick={() => void run('connect')}><PlugZap size={16} />{busy === 'connect' ? 'Đang mở Chrome…' : 'Kết nối'}</button>}<button className={`studio-button${account ? '' : ' primary'}`} disabled={disabled || !!busy} onClick={() => void run('login')}><LogIn size={16} />{account ? 'Đăng nhập tài khoản khác' : 'Mở Chrome đăng nhập'}</button></> }
  }
  return <section className={`studio-tiktok-card${ready ? ' ready' : ''}`} aria-live="polite">
    <div className="studio-tiktok-mark" aria-hidden="true"><TikTokGlyph /></div>
    <div className="studio-grow">
      <div className="studio-tiktok-title"><strong>{step.title}</strong>{ready && <CheckCircle2 size={16} />}</div>
      <p>{step.body}</p>
      {account && <p className="studio-tiktok-account">Tài khoản <b>{account.nick_name || account.user_name || account.user_id}</b> · {account.product_count.toLocaleString('vi-VN')} sản phẩm · đồng bộ lúc {dateTime.format(new Date(account.last_synced_at))}</p>}
      {error && <ErrorNotice message={error} />}
    </div>
    <div className="studio-tiktok-actions">{step.actions}</div>
  </section>
}

function TikTokGlyph() {
  // A neutral music-note mark; the TikTok logo itself is not reproduced.
  return <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 18V5l11-2v13" /><circle cx="6" cy="18" r="3" /><circle cx="17" cy="16" r="3" /></svg>
}
