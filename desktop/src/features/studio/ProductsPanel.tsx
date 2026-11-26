import { useMemo, useState, type FormEvent } from 'react'
import { Film, Package, Pencil, Plus, Search, Trash2 } from 'lucide-react'
import { studioClient, type Media, type Product } from '../../services/studio'
import { EmptyState, ErrorNotice, Modal, errorMessage } from './ui'

type Props = {
  products: Product[]; media: Media[]; loading: boolean; disabled: boolean
  onRefresh: () => Promise<void>; onOpenMedia: (productId: string) => void; onNotice: (message: string) => void
}
const money = new Intl.NumberFormat('vi-VN', { style: 'currency', currency: 'VND', maximumFractionDigits: 0 })

export function ProductsPanel({ products, media, loading, disabled, onRefresh, onOpenMedia, onNotice }: Props) {
  const [query, setQuery] = useState('')
  const [editing, setEditing] = useState<Product | 'new' | null>(null)
  const [deleting, setDeleting] = useState<Product | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [price, setPrice] = useState('')
  const visible = useMemo(() => products.filter(product => `${product.name} ${product.description}`.toLocaleLowerCase('vi').includes(query.trim().toLocaleLowerCase('vi'))), [products, query])
  const counts = useMemo(() => {
    const result = new Map<string, number>()
    for (const item of media) if (item.product_id) result.set(item.product_id, (result.get(item.product_id) ?? 0) + 1)
    return result
  }, [media])
  const open = (product: Product | 'new') => {
    setName(product === 'new' ? '' : product.name)
    setDescription(product === 'new' ? '' : product.description)
    setPrice(product === 'new' || product.price === null ? '' : String(product.price))
    setError('')
    setEditing(product)
  }
  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (busy || disabled || !editing) return
    const numericPrice = price.trim() ? Number(price.replace(',', '.')) : null
    if (!name.trim()) { setError('Vui lòng nhập tên sản phẩm.'); return }
    if (numericPrice !== null && (!Number.isFinite(numericPrice) || numericPrice < 0)) { setError('Giá bán phải là số lớn hơn hoặc bằng 0.'); return }
    setBusy(true)
    setError('')
    try {
      const input = { name: name.trim(), description: description.trim(), price: numericPrice }
      await studioClient.request<{ product: Product }>(editing === 'new' ? 'studio.products.create' : 'studio.products.update', editing === 'new' ? input : { id: editing.id, ...input })
      setEditing(null)
      onNotice(editing === 'new' ? 'Đã tạo sản phẩm.' : 'Đã lưu thay đổi sản phẩm.')
      await onRefresh()
    } catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy(false) }
  }
  const remove = async () => {
    if (!deleting || busy || disabled) return
    setBusy(true)
    setError('')
    try {
      await studioClient.request('studio.products.delete', { id: deleting.id })
      setDeleting(null)
      onNotice('Đã xóa sản phẩm.')
      await onRefresh()
    } catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy(false) }
  }
  const createButton = <button className="studio-button primary" disabled={disabled} onClick={() => open('new')}><Plus size={17} />Thêm sản phẩm</button>
  return <>
    <div className="studio-page-heading"><div><h1>Sản phẩm</h1><p>Danh mục của bạn, được lưu ngay trên máy.</p></div>{createButton}</div>
    <section className="studio-card">
      <div className="studio-toolbar"><label className="studio-search"><Search size={17} /><input aria-label="Tìm sản phẩm" placeholder="Tìm theo tên hoặc mô tả…" value={query} onChange={event => setQuery(event.target.value)} /></label><span className="studio-muted">{visible.length} sản phẩm</span></div>
      {loading ? <div className="studio-empty" role="status">Đang tải sản phẩm…</div> : !visible.length ? <EmptyState icon={<Package size={30} />} title={query ? 'Không tìm thấy sản phẩm' : 'Bắt đầu với sản phẩm đầu tiên'} description={query ? 'Thử tìm bằng tên hoặc mô tả khác.' : 'Thêm tên, giá và mô tả, sau đó nhập video cho sản phẩm.'} action={query ? <button className="studio-button" onClick={() => setQuery('')}>Xóa tìm kiếm</button> : createButton} /> : <div className="studio-table-wrap"><table className="studio-table"><thead><tr><th>Sản phẩm</th><th>Giá bán</th><th>Video</th><th>Cập nhật</th><th><span className="sr-only">Thao tác</span></th></tr></thead><tbody>{visible.map(product => <tr key={product.id}>
        <td><div className="studio-product-name"><span className="studio-product-symbol"><Package size={21} /></span><div><strong className="studio-row-title">{product.name}</strong><p className="studio-row-description">{product.description || 'Chưa có mô tả'}</p></div></div></td>
        <td className="studio-nowrap">{product.price === null ? <span className="studio-muted">Chưa đặt giá</span> : money.format(product.price)}</td>
        <td><button className="studio-button ghost studio-video-link" onClick={() => onOpenMedia(product.id)}><Film size={15} />{counts.get(product.id) ?? 0} video</button></td>
        <td className="studio-muted studio-nowrap">{new Date(product.updated_at).toLocaleDateString('vi-VN')}</td>
        <td><div className="studio-actions"><button className="studio-button ghost icon-only" disabled={disabled || busy} title={`Sửa ${product.name}`} aria-label={`Sửa ${product.name}`} onClick={() => open(product)}><Pencil size={16} /></button><button className="studio-button ghost icon-only danger" disabled={disabled || busy} title={`Xóa ${product.name}`} aria-label={`Xóa ${product.name}`} onClick={() => { setError(''); setDeleting(product) }}><Trash2 size={16} /></button></div></td>
      </tr>)}</tbody></table></div>}
    </section>
    {editing && <Modal title={editing === 'new' ? 'Thêm sản phẩm' : 'Chỉnh sửa sản phẩm'} onClose={() => { if (!busy) setEditing(null) }}><form className="studio-form" onSubmit={event => void save(event)}>
      <label className="studio-field">Tên sản phẩm <span className="studio-required">*</span><input autoFocus required maxLength={200} value={name} disabled={busy} placeholder="Ví dụ: Áo sơ mi linen" onChange={event => setName(event.target.value)} /></label>
      <label className="studio-field">Giá bán (VNĐ)<input inputMode="decimal" value={price} disabled={busy} placeholder="Để trống nếu chưa có giá" onChange={event => setPrice(event.target.value)} /></label>
      <label className="studio-field">Mô tả<textarea rows={5} maxLength={10000} value={description} disabled={busy} placeholder="Chất liệu, màu sắc, kích thước, ghi chú…" onChange={event => setDescription(event.target.value)} /></label>
      {error && <ErrorNotice message={error} />}
      {disabled && <ErrorNotice message="Studio Core đang mất kết nối. Kết nối lại trước khi lưu." />}
      <div className="studio-form-actions"><button type="button" className="studio-button" disabled={busy} onClick={() => setEditing(null)}>Hủy</button><button className="studio-button primary" disabled={busy || disabled}>{busy ? 'Đang lưu…' : editing === 'new' ? 'Tạo sản phẩm' : 'Lưu thay đổi'}</button></div>
    </form></Modal>}
    {deleting && <Modal title="Xóa sản phẩm?" onClose={() => { if (!busy) setDeleting(null) }}><div className="studio-form"><p>Bạn sắp xóa <strong>{deleting.name}</strong> khỏi danh mục. Thao tác này không thể hoàn tác.</p><p className="studio-muted">Video đã nhập vẫn nằm trong thư viện và sẽ không còn liên kết với sản phẩm này.</p>{error && <ErrorNotice message={error} />}<div className="studio-form-actions"><button className="studio-button" disabled={busy} onClick={() => setDeleting(null)}>Giữ lại</button><button className="studio-button danger-solid" disabled={busy || disabled} onClick={() => void remove()}>{busy ? 'Đang xóa…' : 'Xóa sản phẩm'}</button></div></div></Modal>}
  </>
}
