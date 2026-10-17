import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type FormEvent, type ReactNode } from 'react'
import { Badge, Button, ConfirmModal, EmptyState, Input, SearchInput, SegmentedControl, Select, Sheet, Switch, Table, Textarea, useToast, type TableColumn } from './ui/core'
import {
  CircleAlert, Copy, Edit3, Eye, ImagePlus, Link2, Package, Plus, Radio, Trash2, Upload, Video, X,
} from 'lucide-react'
import { apiRequest, errorText } from './mediaApi'
import { sessionsApi, type LiveSession } from './liveSessionsApi'
import './products.css'

type Product = { id:number; sku:string; name:string; description:string; price:number|null; image_url:string; status:'ACTIVE'|'INACTIVE'; created_at:string }
type Form = { name:string; sku:string; price:string; image_url:string; description:string; active:boolean }
type StatusFilter = 'ALL' | 'ACTIVE' | 'INACTIVE'
type VideoFilter = 'ALL' | 'READY' | 'MISSING'
/** Row shape for the core Table, which needs an index signature. */
type Row = Product & Record<string, unknown>

const EMPTY:Form = {name:'', sku:'', price:'', image_url:'', description:'', active:true}
const MAX_IMAGE_MB = 5
const vnd = new Intl.NumberFormat('vi-VN', {style:'currency', currency:'VND', maximumFractionDigits:0})
const digits = new Intl.NumberFormat('vi-VN', {maximumFractionDigits:0})
const json = (data:unknown, method:string):RequestInit => ({method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)})
const toForm = (p:Product):Form => ({name:p.name, sku:p.sku, price:p.price == null ? '' : String(Math.round(p.price)), image_url:p.image_url, description:p.description, active:p.status === 'ACTIVE'})
const toBody = (f:Form) => ({name:f.name.trim(), sku:f.sku.trim(), price:f.price === '' ? null : Number(f.price), image_url:f.image_url.trim(), description:f.description, status:f.active ? 'ACTIVE' : 'INACTIVE'})

/** Product catalog: what can be sold on LIVE, with photo, code, price and LIVE-video readiness. */
export default function ProductsPage(){
  const toast = useToast()
  const [products,setProducts] = useState<Product[]>([])
  const [videoCounts,setVideoCounts] = useState<Record<number,number>>({})
  const [sessions,setSessions] = useState<LiveSession[]>([])
  const [loaded,setLoaded] = useState(false)
  const [query,setQuery] = useState('')
  const [statusFilter,setStatusFilter] = useState<StatusFilter>('ALL')
  const [videoFilter,setVideoFilter] = useState<VideoFilter>('ALL')
  const [editing,setEditing] = useState<number|'new'|null>(null)
  const [form,setForm] = useState<Form>(EMPTY)
  const [errors,setErrors] = useState<Partial<Record<keyof Form,string>>>({})
  const [busy,setBusy] = useState(false)
  const [toDelete,setToDelete] = useState<Product|null>(null)

  const load = useCallback(async ()=>{
    const [p, live, s] = await Promise.all([
      apiRequest<Product[]>('/products?limit=500'),
      apiRequest<{id:number;video_count:number}[]>('/live/products'),
      sessionsApi.list().catch(()=>[] as LiveSession[]),
    ])
    setProducts(p);setVideoCounts(Object.fromEntries(live.map(x=>[x.id,x.video_count])));setSessions(s);setLoaded(true)
  },[])
  useEffect(()=>{void load().catch(e=>{setLoaded(true);toast.push({tone:'danger',title:'Không tải được sản phẩm',description:errorText(e, '')})})},[load,toast])

  const q = query.trim().toLocaleLowerCase('vi')
  const visible = products.filter(p=>
    (statusFilter === 'ALL' || p.status === statusFilter)
    && (videoFilter === 'ALL' || (videoFilter === 'READY') === !!videoCounts[p.id])
    && (!q || `${p.name} ${p.sku}`.toLocaleLowerCase('vi').includes(q)))
  const stats = useMemo(()=>({
    total:products.length,
    active:products.filter(p=>p.status === 'ACTIVE').length,
    ready:products.filter(p=>p.status === 'ACTIVE' && videoCounts[p.id]).length,
    missing:products.filter(p=>p.status === 'ACTIVE' && !videoCounts[p.id]).length,
  }),[products,videoCounts])
  const openSessions = (id:number) => sessions.filter(s=>s.status !== 'ENDED' && s.product_ids.includes(id))

  const openNew = () => {setForm(EMPTY);setErrors({});setEditing('new')}
  const openEdit = (p:Product) => {setForm(toForm(p));setErrors({});setEditing(p.id)}
  const duplicate = (p:Product) => {setForm({...toForm(p), name:`${p.name} (bản sao)`, sku:''});setErrors({});setEditing('new')}
  const close = () => {if (!busy) setEditing(null)}

  const save = async (andNew:boolean) => {
    const name = form.name.trim()
    if (!name) {setErrors({name:'Nhập tên sản phẩm'});return}
    setBusy(true);setErrors({})
    try{
      const isNew = editing === 'new'
      const saved = await apiRequest<Product>(isNew ? '/products' : `/products/${editing}`, json(toBody(form), isNew ? 'POST' : 'PUT'))
      setProducts(list=>[saved, ...list.filter(p=>p.id !== saved.id)].sort((a,b)=>b.id - a.id))
      toast.push({tone:'success', title:isNew ? `Đã thêm “${saved.name}”` : 'Đã lưu thay đổi', description:isNew ? `Mã ${saved.sku}` : undefined})
      if (andNew) setForm(f=>({...EMPTY, active:f.active}))
      else setEditing(null)
    }catch(e){
      const message = errorText(e, 'Không lưu được sản phẩm')
      if (/mã sản phẩm/i.test(message)) setErrors({sku:message})
      else toast.push({tone:'danger', title:'Không lưu được sản phẩm', description:message})
    }finally{setBusy(false)}
  }
  const toggleStatus = async (p:Product) => {
    const next:Product = {...p, status:p.status === 'ACTIVE' ? 'INACTIVE' : 'ACTIVE'}
    setProducts(list=>list.map(x=>x.id === p.id ? next : x))
    try{await apiRequest(`/products/${p.id}`, json(toBody(toForm(next)), 'PUT'));toast.push({tone:'success', title:next.status === 'ACTIVE' ? `Đã hiện “${p.name}”` : `Đã ẩn “${p.name}”`})}
    catch(e){setProducts(list=>list.map(x=>x.id === p.id ? p : x));toast.push({tone:'danger', title:'Không đổi được trạng thái', description:errorText(e, '')})}
  }
  const remove = async () => {
    const p = toDelete
    if (!p) return
    setBusy(true)
    try{await apiRequest(`/products/${p.id}`, {method:'DELETE'});setToDelete(null);toast.push({tone:'success', title:`Đã xóa “${p.name}”`});await load()}
    catch(e){toast.push({tone:'danger', title:'Không xóa được', description:errorText(e, '')})}
    finally{setBusy(false)}
  }

  const columns:TableColumn<Row>[] = [
    {key:'name', header:'Sản phẩm', render:p=><button type="button" className={'pp-row-main' + (p.status === 'INACTIVE' ? ' is-hidden' : '')} onClick={()=>openEdit(p)}>
      <span className="pp-row-thumb"><ProductImage src={p.image_url} name={p.name}/></span>
      <span className="pp-row-text"><strong>{p.name}</strong><small>{p.sku}{p.description ? ' · ' + p.description : ''}</small></span>
    </button>},
    {key:'price', header:'Giá bán', align:'right', render:p=><span className="pp-num">{p.price == null ? '—' : vnd.format(p.price)}</span>},
    {key:'videos', header:'Video LIVE', render:p=><VideoBadge count={videoCounts[p.id]}/>},
    {key:'status', header:'Đang bán', render:p=><Switch aria-label={`Đang bán ${p.name}`} checked={p.status === 'ACTIVE'} onChange={()=>void toggleStatus(p)}/>},
    {key:'actions', header:'Thao tác', align:'right', render:p=><div className="pp-row-actions">
      <IconButton title="Sửa" onClick={()=>openEdit(p)}><Edit3 size={15}/></IconButton>
      <IconButton title="Nhân bản" onClick={()=>duplicate(p)}><Copy size={15}/></IconButton>
      <IconButton title="Xóa" danger onClick={()=>setToDelete(p)}><Trash2 size={15}/></IconButton>
    </div>},
  ]

  const editingProduct = typeof editing === 'number' ? products.find(p=>p.id === editing) : undefined
  const deleteUsage = toDelete ? [
    videoCounts[toDelete.id] ? `${videoCounts[toDelete.id]} video LIVE sẽ bị gỡ liên kết` : '',
    openSessions(toDelete.id).length ? `bị gỡ khỏi ${openSessions(toDelete.id).length} phiên LIVE chưa kết thúc` : '',
  ].filter(Boolean).join(', ') : ''

  return <div className="pp-page">
    <section className="pp-stats" aria-label="Tổng quan sản phẩm">
      <Stat icon={<Package size={18}/>} label="Tổng sản phẩm" value={stats.total} tone="violet"/>
      <Stat icon={<Eye size={18}/>} label="Đang bán" value={stats.active} hint={`${stats.total - stats.active} tạm ẩn`} tone="blue"/>
      <Stat icon={<Radio size={18}/>} label="Sẵn sàng LIVE" value={stats.ready} hint="đang bán & đã có video" tone="mint"/>
      <Stat icon={<CircleAlert size={18}/>} label="Thiếu video" value={stats.missing} hint={stats.missing ? 'chưa đưa lên LIVE được' : 'không có'} tone={stats.missing ? 'peach' : 'muted'}
        onClick={stats.missing ? ()=>{setStatusFilter('ACTIVE');setVideoFilter('MISSING')} : undefined}/>
    </section>

    <section className="pp-panel">
      <div className="pp-toolbar">
        <div className="pp-search"><SearchInput value={query} onChange={e=>setQuery(e.target.value)} placeholder="Tìm theo tên hoặc mã sản phẩm…" aria-label="Tìm sản phẩm"/></div>
        <SegmentedControl<StatusFilter> value={statusFilter} onChange={setStatusFilter} items={[{value:'ALL', label:'Tất cả'}, {value:'ACTIVE', label:'Đang bán'}, {value:'INACTIVE', label:'Tạm ẩn'}]}/>
        <div className="pp-select"><Select value={videoFilter} onChange={v=>setVideoFilter(v as VideoFilter)} options={[{value:'ALL', label:'Mọi trạng thái video'}, {value:'READY', label:'Đã có video LIVE'}, {value:'MISSING', label:'Chưa có video'}]}/></div>
        <Button leftIcon={<Plus size={17}/>} onClick={openNew}>Thêm sản phẩm</Button>
      </div>
      <div className="pp-count">{loaded ? `${visible.length} / ${products.length} sản phẩm` : 'Đang tải…'}
        {(q || statusFilter !== 'ALL' || videoFilter !== 'ALL') && <button type="button" className="pp-clear" onClick={()=>{setQuery('');setStatusFilter('ALL');setVideoFilter('ALL')}}>Xóa bộ lọc</button>}
      </div>

      {loaded && !visible.length ? <div className="pp-empty"><EmptyState icon={<Package size={30}/>}
        title={products.length ? 'Không có sản phẩm phù hợp' : 'Chưa có sản phẩm nào'}
        description={products.length ? 'Thử từ khóa hoặc bộ lọc khác.' : 'Thêm sản phẩm bạn sẽ bán trên LIVE: tên, ảnh, giá và mô tả để AI trả lời bình luận.'}
        actionLabel={products.length ? undefined : 'Thêm sản phẩm đầu tiên'} onAction={openNew}/></div>
      : <div className="pp-table"><Table<Row> data={visible as Row[]} columns={columns}/></div>}
    </section>

    <Sheet open={editing !== null} onClose={close} width={560} title={editing === 'new' ? 'Thêm sản phẩm' : 'Chỉnh sửa sản phẩm'} footer={<div className="pp-sheet-footer">
      <Button variant="ghost" disabled={busy} onClick={close}>Hủy</Button>
      {editing === 'new' && <Button variant="outline" disabled={busy} onClick={()=>void save(true)}>Lưu & thêm tiếp</Button>}
      <Button type="submit" form="pp-form" loading={busy}>{editing === 'new' ? 'Thêm sản phẩm' : 'Lưu thay đổi'}</Button>
    </div>}>
      {editing !== null && <form id="pp-form" className="pp-form" onSubmit={(e:FormEvent)=>{e.preventDefault();void save(false)}}>
        <ImageField value={form.image_url} name={form.name} onChange={url=>setForm(f=>({...f, image_url:url}))}/>
        <Input label="Tên sản phẩm *" autoFocus maxLength={200} value={form.name} error={errors.name} placeholder="VD: Áo trễ vai trắng"
          onChange={e=>setForm(f=>({...f, name:e.target.value}))}/>
        <div className="pp-form-row">
          <Input label="Mã sản phẩm (SKU)" maxLength={64} value={form.sku} error={errors.sku} placeholder="Để trống để tự tạo"
            hint={editing === 'new' ? 'Tự tạo dạng SP-XXXXXX nếu để trống' : undefined} onChange={e=>setForm(f=>({...f, sku:e.target.value}))}/>
          <Input label="Giá bán" inputMode="numeric" suffix="₫" placeholder="0" value={form.price === '' ? '' : digits.format(Number(form.price))}
            onChange={e=>setForm(f=>({...f, price:e.target.value.replace(/\D/g, '').slice(0, 13)}))}/>
        </div>
        <Textarea label="Mô tả" rows={6} maxLength={10000} value={form.description}
          placeholder="Chất liệu, size, màu, điểm nổi bật, chính sách… AI dùng mô tả này khi trả lời bình luận trên LIVE."
          hint={`${form.description.length.toLocaleString('vi-VN')}/10.000 ký tự`} onChange={e=>setForm(f=>({...f, description:e.target.value}))}/>
        <div className="pp-switch-row">
          <Switch label="Đang bán" checked={form.active} onChange={e=>setForm(f=>({...f, active:e.target.checked}))}/>
          <small>{form.active ? 'Hiện khi chọn sản phẩm cho phiên LIVE.' : 'Tạm ẩn: không hiện khi chọn sản phẩm cho phiên LIVE.'}</small>
        </div>
        {editingProduct && <div className="pp-meta">
          <VideoBadge count={videoCounts[editingProduct.id]}/>
          <a href="#/product-videos">Gán / sắp xếp video LIVE →</a>
          <span>Tạo {new Date(editingProduct.created_at).toLocaleDateString('vi-VN')}</span>
        </div>}
      </form>}
    </Sheet>

    <ConfirmModal open={!!toDelete} loading={busy} onClose={()=>setToDelete(null)} onConfirm={()=>void remove()} confirmLabel="Xóa sản phẩm"
      title={toDelete ? `Xóa “${toDelete.name}”?` : 'Xóa sản phẩm?'}
      description={`Không thể hoàn tác.${deleteUsage ? ' Sản phẩm sẽ ' + deleteUsage + '.' : ''} Nếu chỉ muốn ngừng bán tạm thời, hãy dùng “Tạm ẩn”.`}/>
  </div>
}

function Stat({icon,label,value,hint,tone,onClick}:{icon:ReactNode;label:string;value:number;hint?:string;tone:string;onClick?:()=>void}){
  const body = <><span className={'pp-stat-icon ' + tone}>{icon}</span><span className="pp-stat-text"><small>{label}</small><strong>{value.toLocaleString('vi-VN')}</strong>{hint && <em>{hint}</em>}</span></>
  return onClick ? <button type="button" className="pp-stat is-link" onClick={onClick} title="Lọc sản phẩm thiếu video">{body}</button> : <div className="pp-stat">{body}</div>
}

function VideoBadge({count}:{count?:number}){
  return count ? <Badge tone="success"><Video size={12}/>{count} video</Badge> : <a className="pp-novideo" href="#/product-videos" title="Gán video LIVE"><CircleAlert size={13}/>Chưa có video</a>
}

function IconButton({title,danger,onClick,children}:{title:string;danger?:boolean;onClick:()=>void;children:ReactNode}){
  return <button type="button" className={'pp-icon' + (danger ? ' is-danger' : '')} title={title} aria-label={title} onClick={onClick}>{children}</button>
}

/** Product photo, falling back to the product's initials when missing or broken. */
function ProductImage({src,name}:{src:string;name:string}){
  const [failedSrc,setFailedSrc] = useState('')
  if (src && src !== failedSrc) return <img src={src} alt="" loading="lazy" onError={()=>setFailedSrc(src)}/>
  const initials = name.trim().split(/\s+/).slice(0, 2).map(w=>w[0]?.toUpperCase() ?? '').join('')
  return <span className="pp-initials" aria-hidden="true">{initials || <Package size={22}/>}</span>
}

/** Photo picker: upload (click, drop or paste) to the Backend, or paste an image URL. */
function ImageField({value,name,onChange}:{value:string;name:string;onChange:(url:string)=>void}){
  const toast = useToast()
  const input = useRef<HTMLInputElement>(null)
  const [uploading,setUploading] = useState(false)
  const [over,setOver] = useState(false)
  const [urlMode,setUrlMode] = useState(false)
  const [url,setUrl] = useState('')
  const upload = async (file?:File) => {
    if (!file) return
    if (!file.type.startsWith('image/')) {toast.push({tone:'danger', title:'File không phải ảnh'});return}
    if (file.size > MAX_IMAGE_MB * 1024 * 1024) {toast.push({tone:'danger', title:`Ảnh tối đa ${MAX_IMAGE_MB} MB`});return}
    setUploading(true)
    try{
      const body = new FormData()
      body.append('file', file)
      onChange((await apiRequest<{url:string}>('/products/images', {method:'POST', body})).url)
    }catch(e){toast.push({tone:'danger', title:'Không tải được ảnh lên', description:errorText(e, '')})}
    finally{setUploading(false)}
  }
  const onDrop = (e:DragEvent) => {e.preventDefault();setOver(false);void upload(e.dataTransfer.files[0])}
  return <div className="pp-image-field">
    <div className={'pp-drop' + (over ? ' is-over' : '') + (value ? ' has-image' : '')} role="button" tabIndex={0}
      onClick={()=>input.current?.click()} onKeyDown={e=>{if (e.key === 'Enter' || e.key === ' ') {e.preventDefault();input.current?.click()}}}
      onDragOver={e=>{e.preventDefault();setOver(true)}} onDragLeave={()=>setOver(false)} onDrop={onDrop}
      onPaste={e=>void upload(Array.from(e.clipboardData.files)[0])} aria-label="Chọn ảnh sản phẩm">
      {value ? <ProductImage src={value} name={name}/> : <span className="pp-drop-hint">
        <ImagePlus size={26}/><strong>{uploading ? 'Đang tải ảnh…' : 'Kéo thả, dán hoặc bấm để chọn ảnh'}</strong><small>JPG, PNG, WEBP · tối đa {MAX_IMAGE_MB} MB</small>
      </span>}
      {uploading && value && <span className="pp-drop-busy">Đang tải…</span>}
    </div>
    <input ref={input} type="file" accept="image/jpeg,image/png,image/webp,image/gif" hidden onChange={e=>{void upload(e.target.files?.[0]);e.target.value = ''}}/>
    <div className="pp-image-actions">
      <Button type="button" size="sm" variant="outline" leftIcon={<Upload size={14}/>} disabled={uploading} onClick={()=>input.current?.click()}>{value ? 'Đổi ảnh' : 'Tải ảnh lên'}</Button>
      <Button type="button" size="sm" variant="ghost" leftIcon={<Link2 size={14}/>} onClick={()=>{setUrl(value);setUrlMode(m=>!m)}}>Dùng URL</Button>
      {value && <Button type="button" size="sm" variant="ghost" leftIcon={<X size={14}/>} onClick={()=>onChange('')}>Bỏ ảnh</Button>}
    </div>
    {urlMode && <div className="pp-url-row">
      <Input aria-label="URL ảnh" type="url" value={url} placeholder="https://…" onChange={e=>setUrl(e.target.value)}
        onKeyDown={e=>{if (e.key === 'Enter') {e.preventDefault();onChange(url.trim());setUrlMode(false)}}}/>
      <Button type="button" size="sm" onClick={()=>{onChange(url.trim());setUrlMode(false)}}>Áp dụng</Button>
    </div>}
  </div>
}
