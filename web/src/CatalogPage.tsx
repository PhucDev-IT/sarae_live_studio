import { useEffect, useState, type FormEvent } from 'react'
import { Button, Input } from './ui/core'
import { Check, Edit3, Package, Plus, Search, Trash2, Users, X } from 'lucide-react'
import './catalog.css'

type Kind = 'products' | 'presenters'
type Item = { id: number; name: string; status: string; description: string; sku?: string; price?: number | null; image_url?: string; avatar_url?: string }
type Form = { name: string; sku: string; description: string; price: string; image_url: string; avatar_url: string; status: string }
const emptyForm: Form = { name: '', sku: '', description: '', price: '', image_url: '', avatar_url: '', status: 'ACTIVE' }
const apiRoot = ((import.meta.env.VITE_API_URL as string | undefined) || 'http://127.0.0.1:8000').replace(/\/$/, '')

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : 'Đã xảy ra lỗi không xác định'
}
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(apiRoot + '/api/' + path, { ...options, headers: { 'Content-Type': 'application/json', ...options?.headers } })
  if (!response.ok) {
    const body: { detail?: string | { msg?: string }[] } = await response.json().catch(() => ({}))
    const detail = typeof body.detail === 'string' ? body.detail : Array.isArray(body.detail) ? body.detail.map(e => e.msg).join('; ') : ''
    throw new Error(detail || `Yêu cầu thất bại (HTTP ${response.status})`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export default function CatalogPage({ kind }: { kind: Kind }) {
  const isProduct = kind === 'products'
  const singular = isProduct ? 'sản phẩm' : 'người giới thiệu'
  const [items, setItems] = useState<Item[]>([])
  const [search, setSearch] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [editing, setEditing] = useState<number | 'new' | null>(null)
  const [form, setForm] = useState<Form>(emptyForm)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let active = true
    setLoaded(false)
    setError('')
    request<Item[]>(kind + '?limit=500').then(data => {
      if (active) { setItems(data); setLoaded(true) }
    }).catch(err => { if (active) { setError(errorText(err)); setLoaded(true) } })
    return () => { active = false }
  }, [kind])
  const filtered = items.filter(i => (i.name + ' ' + (i.sku || '')).toLocaleLowerCase('vi').includes(search.toLocaleLowerCase('vi')))
  const openNew = () => { setForm(emptyForm); setEditing('new'); setError(''); setSuccess('') }
  const openEdit = (i: Item) => {
    setForm({ name: i.name, sku: i.sku ?? '', description: i.description || '', price: i.price == null ? '' : String(i.price), image_url: i.image_url || '', avatar_url: i.avatar_url || '', status: i.status })
    setEditing(i.id); setError(''); setSuccess('')
  }
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError(''); setSuccess(''); setBusy(true)
    const data = isProduct ? {
      name: form.name.trim(), sku: form.sku.trim(), description: form.description,
      price: form.price.trim() === '' ? null : Number(form.price),
      image_url: form.image_url.trim(), status: form.status,
    } : { name: form.name.trim(), description: form.description, avatar_url: form.avatar_url.trim(), status: form.status }
    try {
      const isNew = editing === 'new'
      const saved = await request<Item>(kind + (isNew ? '' : '/' + editing), {
        method: isNew ? 'POST' : 'PUT', body: JSON.stringify(data),
      })
      setItems(current => [saved, ...current.filter(i => i.id !== saved.id)].sort((a,b) => b.id-a.id))
      setEditing(null)
      setSuccess(`Đã ${isNew ? 'thêm' : 'cập nhật'} ${singular} thành công.`)
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }
  const remove = async (item: Item) => {
    if (!window.confirm(`Xóa ${singular} "${item.name}"? Hành động này không thể hoàn tác.`)) return
    setBusy(true); setError(''); setSuccess('')
    try { await request<void>(kind + '/' + item.id, { method: 'DELETE' }); setItems(current => current.filter(i => i.id !== item.id)); setSuccess('Đã xóa thành công.') }
    catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }
  return <section className="catalog panel">
    <div className="catalog-header">
      <div><h3>{isProduct ? 'Danh sách sản phẩm' : 'Danh sách người giới thiệu'}</h3><p>{loaded ? `${items.length} bản ghi · Dữ liệu từ Backend` : 'Đang tải dữ liệu…'}</p></div>
      <Button leftIcon={<Plus size={17}/>} onClick={openNew}>Thêm {singular}</Button>
    </div>
    <div className="catalog-toolbar"><div className="catalog-search"><Search size={17}/><input value={search} onChange={e=>setSearch(e.target.value)} placeholder={`Tìm theo tên${isProduct ? ', mã sản phẩm' : ''}…`} aria-label="Tìm kiếm"/></div><span>{filtered.length} kết quả</span></div>
    {success && <div className="catalog-message success" role="status"><Check size={17}/>{success}</div>}
    {error && <div className="catalog-message error" role="alert">{error}</div>}
    {!loaded ? <div className="catalog-empty">Đang kết nối dữ liệu…</div> : !filtered.length ? <div className="catalog-empty"><div className="catalog-empty-icon">{isProduct ? <Package size={29}/> : <Users size={29}/>}</div><h3>{search ? 'Không có kết quả phù hợp' : `Chưa có ${singular} nào`}</h3><p>{search ? 'Hãy thử từ khóa khác.' : `Nhấn Thêm ${singular} để bắt đầu quản lý nội dung.`}</p></div> : <div className="catalog-table-wrap"><table className="catalog-table"><thead><tr><th>{isProduct ? 'Sản phẩm' : 'Người giới thiệu'}</th>{isProduct && <th>Mã SKU</th>}{isProduct && <th>Giá tham khảo</th>}<th>Trạng thái</th><th className="catalog-actions-heading">Thao tác</th></tr></thead><tbody>{filtered.map(item => <tr key={item.id}><td><div className="catalog-item">{(isProduct ? item.image_url : item.avatar_url) ? <img src={isProduct ? item.image_url : item.avatar_url} alt="" className="catalog-thumbnail"/> : <span className="catalog-thumbnail catalog-placeholder">{isProduct ? <Package size={20}/> : <Users size={20}/>}</span>}<div><strong>{item.name}</strong><span>{item.description || 'Chưa có mô tả'}</span></div></div></td>{isProduct && <td><code>{item.sku}</code></td>}{isProduct && <td>{item.price == null ? '—' : new Intl.NumberFormat('vi-VN',{style:'currency',currency:'VND'}).format(item.price)}</td>}<td><span className={`catalog-status ${item.status === 'ACTIVE' ? 'active' : 'inactive'}`}>{item.status === 'ACTIVE' ? 'Đang dùng' : 'Tạm ẩn'}</span></td><td><div className="catalog-actions"><button title="Chỉnh sửa" aria-label={`Sửa ${item.name}`} onClick={()=>openEdit(item)}><Edit3 size={16}/></button><button title="Xóa" disabled={busy} aria-label={`Xóa ${item.name}`} onClick={()=>void remove(item)}><Trash2 size={16}/></button></div></td></tr>)}</tbody></table></div>}
    {editing !== null && <div className="catalog-modal-backdrop" onClick={() => !busy && setEditing(null)}><div className="catalog-modal" role="dialog" aria-modal="true" aria-label={editing === 'new' ? `Thêm ${singular}` : `Sửa ${singular}`} onClick={event => event.stopPropagation()}><div className="catalog-modal-heading"><div><h3>{editing === 'new' ? 'Thêm' : 'Chỉnh sửa'} {singular}</h3><p>Cập nhật dữ liệu cho thư viện nội dung Sarae Live Studio.</p></div><button type="button" disabled={busy} onClick={()=>setEditing(null)} aria-label="Đóng"><X size={20}/></button></div><form onSubmit={event => void save(event)} className="catalog-form"><Input label="Tên *" required maxLength={200} value={form.name} onChange={e=>setForm(s=>({...s,name:e.target.value}))} placeholder={isProduct ? 'VD: Áo trễ vai trắng' : 'VD: Người giới thiệu 01'}/>{isProduct && <><Input label="Mã SKU *" required maxLength={64} value={form.sku} onChange={e=>setForm(s=>({...s,sku:e.target.value}))} placeholder="VD: SARAE-001"/><Input label="Giá tham khảo (VND)" type="number" min="0" step="any" value={form.price} onChange={e=>setForm(s=>({...s,price:e.target.value}))} placeholder="Nhập giá nếu có"/></>}<Input label={isProduct ? 'URL ảnh sản phẩm' : 'URL ảnh đại diện'} type="url" value={isProduct ? form.image_url : form.avatar_url} onChange={e=>setForm(s=>({...s,[isProduct ? 'image_url':'avatar_url']:e.target.value}))} placeholder="https://…"/><label className="catalog-field"><span>Mô tả</span><textarea rows={4} maxLength={10000} value={form.description} onChange={e=>setForm(s=>({...s,description:e.target.value}))} placeholder="Thông tin mô tả…"/></label><label className="catalog-field"><span>Trạng thái</span><select value={form.status} onChange={e=>setForm(s=>({...s,status:e.target.value}))}><option value="ACTIVE">Đang sử dụng</option><option value="INACTIVE">Tạm ẩn</option></select></label>{error && <div className="catalog-message error" role="alert">{error}</div>}<div className="catalog-modal-footer"><Button type="button" variant="outline" disabled={busy} onClick={()=>setEditing(null)}>Hủy</Button><Button type="submit" loading={busy}>Lưu thông tin</Button></div></form></div></div>}
  </section>
}
