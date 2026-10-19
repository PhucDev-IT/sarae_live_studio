import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Badge, Button, ConfirmModal, EmptyState, Input, SearchInput, SegmentedControl, Select, Sheet, Switch, Table, Textarea, useToast, type TableColumn } from './ui/core'
import {
  CircleAlert, Copy, Edit3, Eye, Package, Plus, Radio, Trash2, Video,
} from 'lucide-react'
import { apiRequest, errorText, onAgent } from './mediaApi'
import { useAgent } from './agentContext'
import { sessionsApi, type LiveSession } from './liveSessionsApi'
import { IconButton, ImageField, Stat, Thumb } from './CatalogParts'

type TikTokComparison = {scanned_at:string|null; summary:{tiktok:number;sarae:number;matched:number;unmatched_tiktok:number;ambiguous:number;missing_in_tiktok:number};products:Array<{name:string;match_status:string}>;missing_in_tiktok:Array<{id:number;name:string}>}
type Product = { id:number; sku:string; name:string; description:string; price:number|null; image_url:string; status:'ACTIVE'|'INACTIVE'; created_at:string }
type Form = { name:string; sku:string; price:string; image_url:string; description:string; active:boolean }
type StatusFilter = 'ALL' | 'ACTIVE' | 'INACTIVE'
type VideoFilter = 'ALL' | 'READY' | 'MISSING'
/** Row shape for the core Table, which needs an index signature. */
type Row = Product & Record<string, unknown>

const EMPTY:Form = {name:'', sku:'', price:'', image_url:'', description:'', active:true}
const vnd = new Intl.NumberFormat('vi-VN', {style:'currency', currency:'VND', maximumFractionDigits:0})
const digits = new Intl.NumberFormat('vi-VN', {maximumFractionDigits:0})
const json = (data:unknown, method:string):RequestInit => ({method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)})
const toForm = (p:Product):Form => ({name:p.name, sku:p.sku, price:p.price == null ? '' : String(Math.round(p.price)), image_url:p.image_url, description:p.description, active:p.status === 'ACTIVE'})
const toBody = (f:Form) => ({name:f.name.trim(), sku:f.sku.trim(), price:f.price === '' ? null : Number(f.price), image_url:f.image_url.trim(), description:f.description, status:f.active ? 'ACTIVE' : 'INACTIVE'})

/** Product catalog: what can be sold on LIVE, with photo, code, price and LIVE-video readiness. */
export default function ProductsPage(){
  const toast = useToast()
  const {agent} = useAgent()
  const agentId = agent?.id
  const [products,setProducts] = useState<Product[]>([])
  const [tiktok,setTikTok] = useState<TikTokComparison|null>(null)
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
      agentId ? apiRequest<{id:number;video_count:number}[]>(onAgent('/live/products', agentId)) : Promise.resolve([]),
      sessionsApi.list().catch(()=>[] as LiveSession[]),
    ])
    setProducts(p);setVideoCounts(Object.fromEntries(live.map(x=>[x.id,x.video_count])));setSessions(s);setLoaded(true)
  },[agentId])
  useEffect(()=>{void load().catch(e=>{setLoaded(true);toast.push({tone:'danger',title:'Không tải được sản phẩm',description:errorText(e, '')})})},[load,toast])

  useEffect(()=>{
    let active=true
    if (!agentId){setTikTok(null);return}
    void apiRequest<TikTokComparison>('/tiktok-products/'+encodeURIComponent(agentId)).then(v=>{if(active)setTikTok(v)}).catch(()=>{if(active)setTikTok(null)})
    return ()=>{active=false}
  },[agentId])

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
      <span className="pp-row-thumb"><Thumb src={p.image_url} name={p.name} fallback={<Package size={18}/>}/></span>
      <span className="pp-row-text"><strong>{p.name}</strong><small>{p.sku}{p.description ? ' · ' + p.description : ''}</small></span>
    </button>},
    {key:'price', header:'Giá bán', align:'right', render:p=><span className="pp-num">{p.price == null ? '—' : vnd.format(p.price)}</span>},
    {key:'videos', header:agent ? `Video LIVE · ${agent.hostname}` : 'Video LIVE', render:p=><VideoBadge count={videoCounts[p.id]}/>},
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
        onClick={stats.missing ? ()=>{setStatusFilter('ACTIVE');setVideoFilter('MISSING')} : undefined} actionTitle="Lọc sản phẩm thiếu video"/>
    </section>

    {agentId && <section className="pp-panel" style={{padding:'14px 18px',marginBottom:14}}>
      <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:12,flexWrap:'wrap'}}>
        <div>
          <strong>Đối chiếu sản phẩm TikTok LIVE</strong>
          <div className="pp-count">{tiktok?.scanned_at
            ? 'TikTok: '+tiktok.summary.tiktok+' · Khớp: '+tiktok.summary.matched+' · Thiếu trên TikTok: '+tiktok.summary.missing_in_tiktok+' · Không khớp Sarae: '+tiktok.summary.unmatched_tiktok+' · Trùng tên: '+tiktok.summary.ambiguous
            : 'Chưa có danh sách TikTok được đồng bộ từ Windows Agent'}</div>
        </div>
        <Button onClick={()=>void apiRequest<TikTokComparison>('/tiktok-products/'+encodeURIComponent(agentId)).then(setTikTok).catch(e=>toast.push({tone:'danger',title:'Không tải được danh sách TikTok',description:errorText(e,'')}))}>Làm mới đối chiếu</Button>
      </div>
      {!!tiktok?.scanned_at && (tiktok.summary.missing_in_tiktok>0 || tiktok.summary.unmatched_tiktok>0 || tiktok.summary.ambiguous>0) && (
        <details style={{marginTop:10}}><summary>Xem các sản phẩm cần xử lý</summary>
          {tiktok.missing_in_tiktok.map(p=><div key={'s-'+p.id} className="pp-count">Thiếu trên TikTok: {p.name}</div>)}
          {tiktok.products.filter(p=>p.match_status!=='matched').map((p,i)=><div key={'t-'+i} className="pp-count">{p.match_status==='ambiguous'?'Trùng tên':'Không có trong Sarae'}: {p.name}</div>)}
        </details>
      )}
    </section>}
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
        <ImageField value={form.image_url} name={form.name} uploadPath="/products/images" label="Chọn ảnh sản phẩm" fallback={<Package size={30}/>}
          onChange={url=>setForm(f=>({...f, image_url:url}))}/>
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

function VideoBadge({count}:{count?:number}){
  return count ? <Badge tone="success"><Video size={12}/>{count} video</Badge> : <a className="pp-novideo" href="#/product-videos" title="Gán video LIVE"><CircleAlert size={13}/>Chưa có video</a>
}
