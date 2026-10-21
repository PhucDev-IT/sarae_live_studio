import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import {
  Alert, Badge, Button, Card, ConfirmModal, EmptyState, FilterBar, Input, LoadingState, MultiSelect, SearchInput, Select, Sheet,
  SkeletonCard, StatCard, TagsInput, Textarea, useToast,
} from './ui/core'
import { ArrowDown, ArrowUp, CloudUpload, Film, HardDrive, Link2, ListVideo, Pencil, Plus, RefreshCw, RotateCcw, Trash2, X } from 'lucide-react'
import { apiRequest, assetUrl, durationLabel, errorText, sizeLabel, type CatalogItem } from './mediaApi'
import {
  ACCEPTED_VIDEO, cancelUpload, deleteCloudMedia, getProductCloudVideos, listCloudMedia, patchCloudMedia, putProductCloudVideos,
  setCloudMediaProducts, uploadVideo, type CloudMedia,
} from './cloudMediaApi'
import VideoThumb from './VideoThumb'
import './media.css'
import './cloud-media.css'

type QueueItem = { key:string; file:File; clientId:string; uploadId?:string; sent:number; state:'queued'|'uploading'|'done'|'error'|'cancelled'; error?:string }

const parseTags = (value:string) => value.split(',').map(tag=>tag.trim()).filter(Boolean)
const newId = () => (globalThis.crypto?.randomUUID?.() ?? `${Date.now()}${Math.random()}`).replace(/[^A-Za-z0-9]/g, '').slice(0, 32)

/** The User's video library on Sarae cloud storage: upload, preview, edit and link to products (shared by all Agents). */
export default function CloudMediaPage(){
  const toast = useToast()
  const [items,setItems] = useState<CloudMedia[]>([])
  const [products,setProducts] = useState<CatalogItem[]>([])
  const [loading,setLoading] = useState(true)
  const [loadError,setLoadError] = useState('')
  const [query,setQuery] = useState('')
  const [productFilter,setProductFilter] = useState('all')
  const [editing,setEditing] = useState<CloudMedia|null>(null)

  const load = async () => {
    setLoadError('')
    try{
      const [library, catalog] = await Promise.all([listCloudMedia(), apiRequest<CatalogItem[]>('/products?limit=500')])
      setItems(library.items);setProducts(catalog)
    }catch(e){setLoadError(errorText(e, 'Không tải được thư viện'))}
    finally{setLoading(false)}
  }
  useEffect(()=>{void load()},[])

  const productName = useMemo(()=>new Map(products.map(p=>[p.id, p.name])),[products])
  const filtered = useMemo(()=>{
    const q = query.trim().toLowerCase()
    return items.filter(item=>(!q || [item.display_name, item.filename, item.tags].some(v=>v.toLowerCase().includes(q)))
      && (productFilter === 'all' || (productFilter === 'none' ? item.product_ids.length === 0 : item.product_ids.includes(Number(productFilter)))))
  },[items, query, productFilter])
  const stats = useMemo(()=>({bytes:items.reduce((s,i)=>s + i.size_bytes, 0), linked:items.filter(i=>i.product_ids.length).length}),[items])

  return <div className="media-page">
    <section className="media-stats" aria-label="Thống kê thư viện">
      <StatCard label="Tổng video" icon={<Film size={18}/>} value={items.length} delta="Dùng chung cho mọi Windows Agent của bạn" trend="neutral"/>
      <StatCard label="Dung lượng" icon={<HardDrive size={18}/>} value={sizeLabel(stats.bytes)} delta="Lưu trên kho của Sarae" trend="neutral"/>
      <StatCard label="Đã gắn sản phẩm" icon={<Link2 size={18}/>} value={`${stats.linked}/${items.length}`} delta={!items.length ? 'Chưa có video' : items.length - stats.linked ? `${items.length - stats.linked} video chưa gắn sản phẩm` : 'Đã gắn đầy đủ'} trend={items.length && stats.linked === items.length ? 'up' : 'neutral'}/>
    </section>

    <Uploader onUploaded={()=>void load()}/>

    <Card className="media-library" padding={false} title="Thư viện video" description={`${filtered.length} / ${items.length} video`}
      actions={<Button variant="ghost" aria-label="Tải lại" title="Tải lại" onClick={()=>{setLoading(true);void load()}}><RefreshCw size={16}/></Button>}>
      <FilterBar className="media-filters">
        <SearchInput value={query} onChange={e=>setQuery(e.target.value)} placeholder="Tìm theo tên, tags…" aria-label="Tìm video"/>
        <Select value={productFilter} onChange={setProductFilter} options={[{value:'all',label:'Mọi sản phẩm'},{value:'none',label:'Chưa gắn sản phẩm'},...products.map(p=>({value:String(p.id),label:p.name}))]}/>
      </FilterBar>
      <div className="media-body">
        {loading ? <div className="media-grid">{Array.from({length:6},(_,i)=><SkeletonCard key={i} lines={2}/>)}</div>
        : loadError ? <LoadingState error={loadError} errorTitle="Không tải được thư viện" onRetry={()=>{setLoading(true);void load()}}>{null}</LoadingState>
        : filtered.length === 0 ? <EmptyState icon={<Film size={32}/>} title={items.length ? 'Không có video khớp bộ lọc' : 'Thư viện chưa có video'}
            description={items.length ? 'Thử từ khóa khác hoặc bỏ bộ lọc.' : 'Tải video MP4/MOV lên ở khung phía trên.'}/>
        : <div className="media-grid">{filtered.map(item=>{
          const duration = durationLabel(item.duration_seconds)
          return <article className="media-card" key={item.id}>
            <button className="media-thumb" onClick={()=>setEditing(item)} aria-label={'Mở ' + item.display_name}>
              <VideoThumb src={item.thumb_url}/>
              {duration && <span className="media-chip media-chip--duration">{duration}</span>}
            </button>
            <div className="media-card-body">
              <div className="media-card-title"><strong title={item.display_name}>{item.display_name}</strong>
                <button type="button" className="media-icon-btn" aria-label="Chỉnh sửa" title="Chỉnh sửa" onClick={()=>setEditing(item)}><Pencil size={15}/></button></div>
              <small className="media-card-meta">{sizeLabel(item.size_bytes)}{item.width && item.height ? ` · ${item.width}×${item.height}` : ''}</small>
              <div className="media-badges">{item.product_ids.length === 0 ? <Badge tone="warning">Chưa gắn sản phẩm</Badge>
                : item.product_ids.slice(0, 2).map(id=><Badge key={id} tone="primary">{productName.get(id) ?? `#${id}`}</Badge>)}
                {item.product_ids.length > 2 && <Badge tone="primary">+{item.product_ids.length - 2}</Badge>}</div>
            </div>
          </article>
        })}</div>}
      </div>
    </Card>

    <ProductVideoOrder products={products} library={items} onChanged={()=>void load()}/>

    <MediaSheet media={editing} products={products} onClose={()=>setEditing(null)}
      onSaved={media=>{setEditing(null);toast.push({tone:'success',title:'Đã lưu',description:media.display_name});void load()}}
      onDeleted={media=>{setEditing(null);toast.push({tone:'success',title:'Đã xóa khỏi thư viện',description:media.display_name});void load()}}/>
  </div>
}

/** Upload queue: one file at a time, chunked and resumable; retry continues from what the storage already has. */
function Uploader({onUploaded}:{onUploaded:()=>void}){
  const [queue,setQueue] = useState<QueueItem[]>([])
  const running = useRef<{key:string;controller:AbortController}|null>(null)
  const input = useRef<HTMLInputElement>(null)
  const update = (key:string, patch:Partial<QueueItem>) => setQueue(q=>q.map(item=>item.key === key ? {...item, ...patch} : item))

  useEffect(()=>{
    if (running.current) return
    const next = queue.find(item=>item.state === 'queued')
    if (!next) return
    const controller = new AbortController()
    running.current = {key:next.key, controller}
    update(next.key, {state:'uploading', error:undefined})
    uploadVideo(next.file, next.clientId, sent=>update(next.key, {sent}), controller.signal, uploadId=>update(next.key, {uploadId}))
      .then(()=>{update(next.key, {state:'done', sent:next.file.size});onUploaded()})
      .catch(e=>{if (!controller.signal.aborted) update(next.key, {state:'error', error:errorText(e, 'Upload thất bại')})})
      .finally(()=>{running.current = null;setQueue(q=>[...q])})
  },[queue, onUploaded])

  const add = (files:FileList|null) => {
    if (!files?.length) return
    setQueue(q=>[...q, ...Array.from(files, file=>({key:newId(), file, clientId:newId(), sent:0, state:'queued' as const}))])
    if (input.current) input.current.value = ''
  }
  const cancel = (item:QueueItem) => {
    if (running.current?.key === item.key) running.current.controller.abort()
    if (item.uploadId) void cancelUpload(item.uploadId).catch(()=>undefined)
    update(item.key, {state:'cancelled'})
  }

  return <Card title="Tải video lên" description="MP4, MOV hoặc M4V. Video được lưu trên kho của Sarae và chỉ tài khoản của bạn thấy được."
    actions={<Button leftIcon={<CloudUpload size={16}/>} onClick={()=>input.current?.click()}>Chọn video</Button>}>
    <input ref={input} type="file" accept={ACCEPTED_VIDEO} multiple hidden onChange={e=>add(e.target.files)}/>
    <div className="cm-drop" onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();add(e.dataTransfer.files)}}>
      {queue.length === 0 ? <p className="cm-muted"><CloudUpload size={18}/> Kéo thả video vào đây hoặc bấm “Chọn video”.</p>
      : <ul className="cm-queue">{queue.map(item=>{
        const percent = item.file.size ? Math.round(item.sent / item.file.size * 100) : 0
        return <li key={item.key} className={'cm-row is-' + item.state}>
          <div className="cm-row-head"><strong title={item.file.name}>{item.file.name}</strong><span>{sizeLabel(item.file.size)}</span></div>
          <div className="cm-bar" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}><span style={{width:`${percent}%`}}/></div>
          <div className="cm-row-foot">
            <span>{item.state === 'queued' ? 'Đang chờ' : item.state === 'uploading' ? `Đang tải ${percent}%` : item.state === 'done' ? 'Hoàn tất'
              : item.state === 'cancelled' ? 'Đã hủy' : item.error}</span>
            {(item.state === 'uploading' || item.state === 'queued') && <Button size="sm" variant="ghost" leftIcon={<X size={14}/>} onClick={()=>cancel(item)}>Hủy</Button>}
            {item.state === 'error' && <Button size="sm" variant="outline" leftIcon={<RotateCcw size={14}/>} onClick={()=>update(item.key, {state:'queued'})}>Thử lại</Button>}
            {(item.state === 'done' || item.state === 'cancelled' || item.state === 'error') &&
              <Button size="sm" variant="ghost" aria-label="Bỏ khỏi danh sách" onClick={()=>setQueue(q=>q.filter(x=>x.key !== item.key))}><X size={14}/></Button>}
          </div>
        </li>
      })}</ul>}
    </div>
  </Card>
}

function MediaSheet({media,products,onClose,onSaved,onDeleted}:{media:CloudMedia|null;products:CatalogItem[];onClose:()=>void;onSaved:(m:CloudMedia)=>void;onDeleted:(m:CloudMedia)=>void}){
  const [name,setName] = useState('')
  const [notes,setNotes] = useState('')
  const [tags,setTags] = useState<string[]>([])
  const [productIds,setProductIds] = useState<string[]>([])
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const [confirmDelete,setConfirmDelete] = useState(false)
  useEffect(()=>{
    if (!media) return
    setName(media.display_name);setNotes(media.notes);setTags(parseTags(media.tags));setProductIds(media.product_ids.map(String));setError('')
  },[media])

  const save = async (event:FormEvent) => {
    event.preventDefault()
    if (!media) return
    setBusy(true);setError('')
    try{
      await patchCloudMedia(media.id, {display_name:name, notes, tags:tags.join(', ')})
      onSaved(await setCloudMediaProducts(media.id, productIds.map(Number)))
    }catch(e){setError(errorText(e, 'Không lưu được video'))}
    finally{setBusy(false)}
  }
  const remove = async () => {
    if (!media) return
    setBusy(true)
    try{onDeleted(await deleteCloudMedia(media.id))}
    catch(e){setError(errorText(e, 'Không xóa được video'))}
    finally{setBusy(false);setConfirmDelete(false)}
  }

  return <>
    <Sheet open={media !== null} title="Thông tin video" width={520} onClose={()=>{if (!busy) onClose()}} footer={<div className="media-sheet-actions">
      <Button variant="danger" leftIcon={<Trash2 size={15}/>} disabled={busy} onClick={()=>setConfirmDelete(true)}>Xóa</Button>
      <Button variant="ghost" disabled={busy} onClick={onClose}>Hủy</Button>
      <Button type="submit" form="cloud-media-form" loading={busy}>Lưu thay đổi</Button>
    </div>}>
      {media && <div className="media-form">
        {media.stream_url && <video className="cm-player" controls preload="metadata" src={assetUrl(media.stream_url) ?? undefined}/>}
        <dl className="media-facts">
          <div className="media-fact"><dt>Tên file gốc</dt><dd>{media.filename}</dd></div>
          <div className="media-fact"><dt>Dung lượng</dt><dd>{sizeLabel(media.size_bytes)}</dd></div>
          <div className="media-fact"><dt>Thời lượng</dt><dd>{durationLabel(media.duration_seconds) ?? 'Đang xử lý'}</dd></div>
        </dl>
        <form id="cloud-media-form" className="media-form" onSubmit={event=>void save(event)}>
          <Input label="Tên hiển thị" maxLength={200} value={name} onChange={e=>setName(e.target.value)} hint="Chỉ đổi tên trong Sarae, không đổi file đã lưu."/>
          <Textarea label="Ghi chú" rows={3} maxLength={2000} value={notes} onChange={e=>setNotes(e.target.value)}/>
          <TagsInput label="Tags" value={tags} onChange={setTags} placeholder="review, quay studio…"/>
          <MultiSelect label="Sản phẩm sử dụng video" placeholder="Chưa gắn sản phẩm" maxVisible={4} value={productIds}
            options={products.map(p=>({value:String(p.id),label:p.name}))} onChange={setProductIds}
            hint="Một video có thể dùng cho nhiều sản phẩm; sản phẩm mới gắn sẽ phát video này ở cuối danh sách."/>
          {error && <Alert tone="danger">{error}</Alert>}
        </form>
      </div>}
    </Sheet>
    <ConfirmModal open={confirmDelete} title="Xóa video khỏi thư viện?" tone="danger" confirmLabel="Xóa" loading={busy}
      description="Video sẽ ẩn khỏi thư viện và không thể gắn thêm sản phẩm. Lịch sử phiên LIVE vẫn được giữ."
      onConfirm={()=>void remove()} onClose={()=>setConfirmDelete(false)}/>
  </>
}

/** Ordered video list of one product (array order = playback order on every Agent). */
function ProductVideoOrder({products,library,onChanged}:{products:CatalogItem[];library:CloudMedia[];onChanged:()=>void}){
  const toast = useToast()
  const [productId,setProductId] = useState('')
  const [order,setOrder] = useState<CloudMedia[]>([])
  const [adding,setAdding] = useState('')
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  useEffect(()=>{
    if (!productId) {setOrder([]);return}
    setError('')
    getProductCloudVideos(Number(productId)).then(r=>setOrder(r.items)).catch(e=>setError(errorText(e, 'Không tải được video của sản phẩm')))
  },[productId])
  const move = (index:number, delta:number) => setOrder(list=>{
    const next = [...list];const [item] = next.splice(index, 1);next.splice(index + delta, 0, item);return next
  })
  const save = async () => {
    setBusy(true);setError('')
    try{
      const saved = await putProductCloudVideos(Number(productId), order.map(m=>m.id))
      setOrder(saved.items);toast.push({tone:'success',title:'Đã lưu thứ tự video'});onChanged()
    }catch(e){setError(errorText(e, 'Không lưu được'))}
    finally{setBusy(false)}
  }
  const candidates = library.filter(m=>m.available && !order.some(o=>o.id === m.id))

  return <Card title="Video theo sản phẩm" description="Chọn sản phẩm rồi sắp xếp video; mọi Windows Agent của bạn dùng chung danh sách này."
    actions={productId ? <Button loading={busy} onClick={()=>void save()}>Lưu thứ tự</Button> : undefined}>
    <div className="cm-order">
      <Select value={productId} onChange={setProductId} options={[{value:'',label:'Chọn sản phẩm…'},...products.map(p=>({value:String(p.id),label:p.name}))]}/>
      {productId && <>
        {order.length === 0 ? <p className="cm-muted"><ListVideo size={16}/> Sản phẩm chưa có video.</p>
        : <ol className="cm-order-list">{order.map((m, i)=><li key={m.id} className={m.available ? '' : 'is-missing'}>
          <span className="cm-order-index">{i + 1}</span>
          <span className="cm-order-name" title={m.display_name}>{m.display_name}{!m.available && <Badge tone="danger">Đã xóa</Badge>}</span>
          <Button size="sm" variant="ghost" aria-label="Lên" disabled={i === 0} onClick={()=>move(i, -1)}><ArrowUp size={14}/></Button>
          <Button size="sm" variant="ghost" aria-label="Xuống" disabled={i === order.length - 1} onClick={()=>move(i, 1)}><ArrowDown size={14}/></Button>
          <Button size="sm" variant="ghost" aria-label="Bỏ" onClick={()=>setOrder(list=>list.filter(x=>x.id !== m.id))}><X size={14}/></Button>
        </li>)}</ol>}
        <div className="cm-order-add">
          <Select value={adding} onChange={setAdding} options={[{value:'',label:'Thêm video từ thư viện…'},...candidates.map(m=>({value:m.id,label:m.display_name}))]}/>
          <Button variant="outline" leftIcon={<Plus size={15}/>} disabled={!adding}
            onClick={()=>{const m = library.find(x=>x.id === adding);if (m) setOrder(list=>[...list, m]);setAdding('')}}>Thêm</Button>
        </div>
      </>}
      {error && <Alert tone="danger">{error}</Alert>}
    </div>
  </Card>
}
