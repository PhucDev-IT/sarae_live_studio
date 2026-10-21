import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from 'react'
import {
  Alert, Button, ConfirmModal, EmptyState, Input, LoadingState, SearchInput, SegmentedControl, Select, Sheet, SkeletonCard,
  TagsInput, Textarea, useToast,
} from './ui/core'
import { Check, CloudUpload, Film, ImagePlus, Package, Play, RefreshCw, Trash2, UserRound, X } from 'lucide-react'
import { apiRequest, assetUrl, durationLabel, errorText, sizeLabel, type Presenter, type Product } from './mediaApi'
import {
  ACCEPTED_VIDEO, deleteCloudMedia, listCloudMedia, patchCloudMedia, putCover, setCloudMediaPresenters, setCloudMediaProducts,
  type CloudMedia,
} from './cloudMediaApi'
import { uploads, useUploads } from './uploadStore'
import { captureElement, captureFrame } from './videoFrame'
import './cloud-media.css'

type Linked = 'all'|'no-product'|'no-presenter'
type SortKey = 'newest'|'name'|'longest'|'largest'
type Tab = 'info'|'products'|'presenters'
type PickItem = { id:number; title:string; subtitle?:string; image?:string|null }

const parseTags = (value:string) => value.split(',').map(tag=>tag.trim()).filter(Boolean)
const sameSet = (a:number[], b:number[]) => a.length === b.length && a.every(x=>b.includes(x))
const fold = (text:string) => text.normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd').replace(/Đ/g, 'D').toLowerCase()

/** The User's video library on Sarae cloud storage (shared by all their Windows Agents). */
export default function CloudMediaPage(){
  const toast = useToast()
  const [items,setItems] = useState<CloudMedia[]>([])
  const [products,setProducts] = useState<Product[]>([])
  const [presenters,setPresenters] = useState<Presenter[]>([])
  const [loading,setLoading] = useState(true)
  const [loadError,setLoadError] = useState('')
  const [query,setQuery] = useState('')
  const [productFilter,setProductFilter] = useState('all')
  const [presenterFilter,setPresenterFilter] = useState('all')
  const [linked,setLinked] = useState<Linked>('all')
  const [sort,setSort] = useState<SortKey>('newest')
  const [openId,setOpenId] = useState<string|null>(null)
  const [dragging,setDragging] = useState(false)
  const dragDepth = useRef(0)
  const picker = useRef<HTMLInputElement>(null)
  const tasks = useUploads()
  // Recomputed whenever the upload queue changes (forget() also notifies it).
  const interrupted = useMemo(()=>{void tasks;return uploads.interrupted()},[tasks])

  const load = useCallback(async () => {
    setLoadError('')
    try{
      const [library, p, a] = await Promise.all([listCloudMedia(), apiRequest<Product[]>('/products?limit=500'), apiRequest<Presenter[]>('/presenters?limit=500')])
      setItems(library.items);setProducts(p);setPresenters(a)
    }catch(e){setLoadError(errorText(e, 'Không tải được thư viện'))}
    finally{setLoading(false)}
  },[])
  useEffect(()=>{void load()},[load])
  useEffect(()=>uploads.onMediaAdded(()=>void load()),[load])
  useCoverBackfill(items, updated=>setItems(list=>list.map(m=>m.id === updated.id ? updated : m)))

  const productName = useMemo(()=>new Map(products.map(p=>[p.id, p.name])),[products])
  const presenterName = useMemo(()=>new Map(presenters.map(p=>[p.id, p.name])),[presenters])
  const filtered = useMemo(()=>{
    const q = fold(query.trim())
    const list = items.filter(item=>(!q || fold([item.display_name, item.filename, item.tags, ...item.product_ids.map(id=>productName.get(id) ?? '')].join(' ')).includes(q))
      && (productFilter === 'all' || item.product_ids.includes(Number(productFilter)))
      && (presenterFilter === 'all' || item.presenter_ids.includes(Number(presenterFilter)))
      && (linked === 'all' || (linked === 'no-product' ? !item.product_ids.length : !item.presenter_ids.length)))
    const by:Record<SortKey,(a:CloudMedia,b:CloudMedia)=>number> = {
      newest:(a,b)=>b.created_at.localeCompare(a.created_at), name:(a,b)=>a.display_name.localeCompare(b.display_name, 'vi'),
      longest:(a,b)=>(b.duration_seconds ?? 0) - (a.duration_seconds ?? 0), largest:(a,b)=>b.size_bytes - a.size_bytes,
    }
    return [...list].sort(by[sort])
  },[items, query, productFilter, presenterFilter, linked, sort, productName])
  const stats = useMemo(()=>({bytes:items.reduce((s,i)=>s + i.size_bytes, 0), noProduct:items.filter(i=>!i.product_ids.length).length}),[items])
  const filtering = !!query.trim() || productFilter !== 'all' || presenterFilter !== 'all' || linked !== 'all'
  const opened = items.find(m=>m.id === openId) ?? null

  const pick = () => picker.current?.click()
  const onDrag = (event:DragEvent, delta:number) => {
    if (!event.dataTransfer.types.includes('Files')) return
    event.preventDefault()
    dragDepth.current = Math.max(0, dragDepth.current + delta)
    setDragging(dragDepth.current > 0)
  }
  const onDrop = (event:DragEvent) => {
    event.preventDefault()
    dragDepth.current = 0;setDragging(false)
    if (event.dataTransfer.files.length) uploads.add(event.dataTransfer.files)
  }

  return <div className="cm-page" onDragEnter={e=>onDrag(e, 1)} onDragOver={e=>onDrag(e, 0)} onDragLeave={e=>onDrag(e, -1)} onDrop={onDrop}>
    <input ref={picker} type="file" accept={ACCEPTED_VIDEO} multiple hidden onChange={e=>{if (e.target.files) uploads.add(e.target.files);e.target.value = ''}}/>

    <section className="cm-hero">
      <div className="cm-hero-stats">
        <Stat label="Video" value={String(items.length)}/>
        <Stat label="Dung lượng" value={sizeLabel(stats.bytes)}/>
        <Stat label="Chưa gắn sản phẩm" value={String(stats.noProduct)} tone={stats.noProduct ? 'warn' : undefined}
          onClick={stats.noProduct ? ()=>setLinked('no-product') : undefined}/>
      </div>
      <div className="cm-hero-actions">
        <Button variant="ghost" aria-label="Tải lại" title="Tải lại" onClick={()=>{setLoading(true);void load()}}><RefreshCw size={16}/></Button>
        <Button leftIcon={<CloudUpload size={16}/>} onClick={pick}>Tải video lên</Button>
      </div>
    </section>

    {interrupted.length > 0 && <Alert tone="warning" title={`${interrupted.length} video tải dở lần trước`}>
      <div className="cm-resume">
        <span>{interrupted.map(p=>p.name).join(', ')}. Chọn lại đúng file để tải tiếp từ chỗ đã dừng.</span>
        <span className="cm-resume-actions">
          <Button size="sm" variant="outline" onClick={pick}>Chọn file</Button>
          <Button size="sm" variant="ghost" onClick={()=>interrupted.forEach(p=>uploads.forget(p))}>Bỏ qua</Button>
        </span>
      </div>
    </Alert>}

    <section className="cm-toolbar" aria-label="Lọc video">
      <SearchInput value={query} onChange={e=>setQuery(e.target.value)} placeholder="Tìm theo tên video, tags, sản phẩm…" aria-label="Tìm video"/>
      <Select value={productFilter} onChange={setProductFilter} options={[{value:'all',label:'Mọi sản phẩm'},...products.map(p=>({value:String(p.id),label:p.name}))]}/>
      <Select value={presenterFilter} onChange={setPresenterFilter} options={[{value:'all',label:'Mọi người giới thiệu'},...presenters.map(p=>({value:String(p.id),label:p.name}))]}/>
      <Select value={linked} onChange={value=>setLinked(value as Linked)} options={[{value:'all',label:'Tất cả video'},{value:'no-product',label:'Chưa gắn sản phẩm'},{value:'no-presenter',label:'Chưa gắn người'}]}/>
      <Select value={sort} onChange={value=>setSort(value as SortKey)} options={[{value:'newest',label:'Mới nhất'},{value:'name',label:'Tên A → Z'},{value:'longest',label:'Dài nhất'},{value:'largest',label:'Dung lượng lớn nhất'}]}/>
    </section>

    {loading ? <div className="cm-grid">{Array.from({length:10},(_,i)=><SkeletonCard key={i} lines={2}/>)}</div>
    : loadError ? <LoadingState error={loadError} errorTitle="Không tải được thư viện" onRetry={()=>{setLoading(true);void load()}}>{null}</LoadingState>
    : items.length === 0 ? <button type="button" className="cm-empty" onClick={pick}>
        <CloudUpload size={36}/><strong>Kéo thả video vào đây hoặc bấm để chọn</strong>
        <span>MP4, MOV, M4V. Video được lưu trên kho của Sarae, chỉ tài khoản của bạn thấy và dùng chung cho mọi Windows Agent.</span>
      </button>
    : filtered.length === 0 ? <EmptyState icon={<Film size={32}/>} title="Không có video khớp bộ lọc" description="Thử từ khóa khác hoặc bỏ bớt bộ lọc."
        actionLabel="Xóa bộ lọc" onAction={()=>{setQuery('');setProductFilter('all');setPresenterFilter('all');setLinked('all')}}/>
    : <>
      {filtering && <p className="cm-count">{filtered.length} / {items.length} video</p>}
      <div className="cm-grid">{filtered.map(item=><VideoCard key={item.id} item={item} productName={productName} presenterName={presenterName} onOpen={()=>setOpenId(item.id)}/>)}</div>
    </>}

    {dragging && <div className="cm-drop-overlay"><CloudUpload size={40}/><strong>Thả video để tải lên</strong></div>}

    <MediaDetail media={opened} products={products} presenters={presenters} onClose={()=>setOpenId(null)}
      onChanged={media=>setItems(list=>list.map(m=>m.id === media.id ? media : m))}
      onDeleted={media=>{setOpenId(null);setItems(list=>list.filter(m=>m.id !== media.id));toast.push({tone:'success',title:'Đã xóa khỏi thư viện',description:media.display_name})}}/>
  </div>
}

function Stat({label,value,tone,onClick}:{label:string;value:string;tone?:'warn';onClick?:()=>void}){
  const body = <><strong>{value}</strong><span>{label}</span></>
  return onClick ? <button type="button" className={'cm-stat is-link' + (tone ? ' is-' + tone : '')} onClick={onClick}>{body}</button>
    : <div className={'cm-stat' + (tone ? ' is-' + tone : '')}>{body}</div>
}

function VideoCard({item,productName,presenterName,onOpen}:{item:CloudMedia;productName:Map<number,string>;presenterName:Map<number,string>;onOpen:()=>void}){
  const duration = durationLabel(item.duration_seconds)
  const [broken,setBroken] = useState(false)
  const firstProduct = item.product_ids[0] != null ? productName.get(item.product_ids[0]) : null
  return <button type="button" className="cm-card" onClick={onOpen} aria-label={'Mở ' + item.display_name}>
    <span className="cm-cover">
      {item.thumb_url && !broken ? <img src={assetUrl(item.thumb_url) ?? undefined} alt="" loading="lazy" onError={()=>setBroken(true)}/>
        : <span className="cm-cover-empty"><Film size={26}/><small>Đang tạo ảnh bìa…</small></span>}
      <span className="cm-play"><Play size={18} fill="currentColor"/></span>
      {duration && <span className="cm-duration">{duration}</span>}
    </span>
    <span className="cm-card-body">
      <strong title={item.display_name}>{item.display_name}</strong>
      <span className="cm-meta">{sizeLabel(item.size_bytes)}{item.width && item.height ? ` · ${item.width}×${item.height}` : ''}</span>
      <span className="cm-chips">
        {firstProduct ? <span className="cm-chip"><Package size={12}/>{firstProduct}{item.product_ids.length > 1 ? ` +${item.product_ids.length - 1}` : ''}</span>
          : <span className="cm-chip is-warn"><Package size={12}/>Chưa gắn sản phẩm</span>}
        {item.presenter_ids.length > 0 && <span className="cm-chip"><UserRound size={12}/>{presenterName.get(item.presenter_ids[0]) ?? ''}{item.presenter_ids.length > 1 ? ` +${item.presenter_ids.length - 1}` : ''}</span>}
      </span>
    </span>
  </button>
}

/** Videos stored before covers existed (or whose cover failed at upload): capture one frame in the background. */
function useCoverBackfill(items:CloudMedia[], onUpdated:(media:CloudMedia)=>void){
  const tried = useRef(new Set<string>())
  const running = useRef(false)
  const [round,setRound] = useState(0)
  useEffect(()=>{
    if (running.current) return
    const next = items.find(m=>m.available && !m.has_thumbnail && m.stream_url && !tried.current.has(m.id))
    if (!next) return
    running.current = true
    tried.current.add(next.id)  // one attempt per video per visit; a failure never loops
    void captureFrame(assetUrl(next.stream_url) as string)
      .then(frame=>frame.blob ? putCover(next.id, frame.blob, frame).then(onUpdated) : undefined)
      .catch(()=>undefined)
      .finally(()=>{running.current = false;setRound(r=>r + 1)})
  },[items, onUpdated, round])
}

function MediaDetail({media,products,presenters,onClose,onChanged,onDeleted}:{media:CloudMedia|null;products:Product[];presenters:Presenter[];
  onClose:()=>void;onChanged:(m:CloudMedia)=>void;onDeleted:(m:CloudMedia)=>void}){
  const toast = useToast()
  const [tab,setTab] = useState<Tab>('info')
  const [name,setName] = useState('')
  const [notes,setNotes] = useState('')
  const [tags,setTags] = useState<string[]>([])
  const [productIds,setProductIds] = useState<number[]>([])
  const [presenterIds,setPresenterIds] = useState<number[]>([])
  const [busy,setBusy] = useState(false)
  const [coverBusy,setCoverBusy] = useState(false)
  const [error,setError] = useState('')
  const [confirmDelete,setConfirmDelete] = useState(false)
  const player = useRef<HTMLVideoElement>(null)
  const mediaId = media?.id
  // Every save returns freshly signed URLs; keep the first ones so the player does not reload and jump to 0:00.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const streamSrc = useMemo(()=>assetUrl(media?.stream_url) ?? undefined,[mediaId])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const posterSrc = useMemo(()=>assetUrl(media?.thumb_url) ?? undefined,[mediaId])
  useEffect(()=>{
    if (!media) return
    setTab('info');setName(media.display_name);setNotes(media.notes);setTags(parseTags(media.tags))
    setProductIds(media.product_ids);setPresenterIds(media.presenter_ids);setError('')
  // Reset the form only when another video is opened, not when this one is refreshed after saving.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  },[mediaId])

  const dirty = !!media && (name !== media.display_name || notes !== media.notes || tags.join(', ') !== parseTags(media.tags).join(', ')
    || !sameSet(productIds, media.product_ids) || !sameSet(presenterIds, media.presenter_ids))

  const save = async () => {
    if (!media) return
    setBusy(true);setError('')
    try{
      let saved = media
      if (name !== media.display_name || notes !== media.notes || tags.join(', ') !== parseTags(media.tags).join(', '))
        saved = await patchCloudMedia(media.id, {display_name:name, notes, tags:tags.join(', ')})
      if (!sameSet(productIds, media.product_ids)) saved = await setCloudMediaProducts(media.id, productIds)
      if (!sameSet(presenterIds, media.presenter_ids)) saved = await setCloudMediaPresenters(media.id, presenterIds)
      onChanged(saved)
      toast.push({tone:'success', title:'Đã lưu', description:saved.display_name})
    }catch(e){setError(errorText(e, 'Không lưu được video'))}
    finally{setBusy(false)}
  }
  const setCover = async () => {
    if (!media || !player.current) return
    setCoverBusy(true)
    try{
      const blob = await captureElement(player.current)
      if (!blob) throw new Error('Không chụp được khung hình. Hãy phát video tới đoạn muốn chọn rồi thử lại.')
      onChanged(await putCover(media.id, blob, {duration:player.current.duration, width:player.current.videoWidth, height:player.current.videoHeight}))
      toast.push({tone:'success', title:'Đã đặt ảnh bìa'})
    }catch(e){toast.push({tone:'danger', title:'Chưa đặt được ảnh bìa', description:errorText(e, '')})}
    finally{setCoverBusy(false)}
  }
  const remove = async () => {
    if (!media) return
    setBusy(true)
    try{onDeleted(await deleteCloudMedia(media.id))}
    catch(e){setError(errorText(e, 'Không xóa được video'))}
    finally{setBusy(false);setConfirmDelete(false)}
  }
  const productItems = useMemo<PickItem[]>(()=>products.map(p=>({id:p.id, title:p.name, subtitle:p.sku, image:p.image_url})),[products])
  const presenterItems = useMemo<PickItem[]>(()=>presenters.map(p=>({id:p.id, title:p.name, subtitle:p.description, image:p.avatar_url})),[presenters])

  return <>
    <Sheet open={media !== null} title={media?.display_name ?? 'Video'} width={940} onClose={()=>{if (!busy) onClose()}} footer={<div className="cm-sheet-footer">
      <Button variant="danger" leftIcon={<Trash2 size={15}/>} disabled={busy} onClick={()=>setConfirmDelete(true)}>Xóa</Button>
      <span className="cm-spacer">{dirty ? 'Có thay đổi chưa lưu' : ''}</span>
      <Button variant="ghost" disabled={busy} onClick={onClose}>Đóng</Button>
      <Button loading={busy} disabled={!dirty} onClick={()=>void save()}>Lưu thay đổi</Button>
    </div>}>
      {media && <div className="cm-detail">
        <div className="cm-detail-media">
          <div className="cm-player-frame">
            {streamSrc && <video ref={player} key={media.id} controls preload="metadata" crossOrigin="anonymous" poster={posterSrc} src={streamSrc}/>}
          </div>
          <Button variant="outline" size="sm" leftIcon={<ImagePlus size={15}/>} loading={coverBusy} onClick={()=>void setCover()}>Đặt khung hình đang xem làm ảnh bìa</Button>
          <dl className="cm-facts">
            <Fact label="File gốc">{media.filename}</Fact>
            <Fact label="Dung lượng">{sizeLabel(media.size_bytes)}</Fact>
            <Fact label="Thời lượng">{durationLabel(media.duration_seconds) ?? 'Đang xử lý'}</Fact>
            <Fact label="Kích thước">{media.width && media.height ? `${media.width} × ${media.height}` : '—'}</Fact>
            <Fact label="Tải lên">{new Date(media.created_at).toLocaleString('vi-VN')}</Fact>
          </dl>
        </div>
        <div className="cm-detail-form">
          <SegmentedControl<Tab> value={tab} onChange={setTab} items={[
            {value:'info', label:'Thông tin'},
            {value:'products', label:`Sản phẩm (${productIds.length})`},
            {value:'presenters', label:`Người giới thiệu (${presenterIds.length})`},
          ]}/>
          {tab === 'info' && <div className="cm-fields">
            <Input label="Tên hiển thị" maxLength={200} value={name} onChange={e=>setName(e.target.value)} hint="Chỉ đổi tên trong Sarae, không đổi file đã lưu."/>
            <Textarea label="Ghi chú" rows={4} maxLength={2000} value={notes} onChange={e=>setNotes(e.target.value)}/>
            <TagsInput label="Tags" value={tags} onChange={setTags} placeholder="review, quay studio…" hint="Nhấn Enter hoặc dấu phẩy để thêm tag"/>
          </div>}
          {tab === 'products' && <PickList items={productItems} value={productIds} onChange={setProductIds} icon={<Package size={18}/>}
            noun="sản phẩm" hint="Video sẽ được phát khi LIVE bán các sản phẩm này. Một video có thể dùng cho nhiều sản phẩm."
            empty="Chưa có sản phẩm. Hãy đồng bộ giỏ hàng TikTok ở trang Sản phẩm."/>}
          {tab === 'presenters' && <PickList items={presenterItems} value={presenterIds} onChange={setPresenterIds} icon={<UserRound size={18}/>}
            noun="người" hint="Chọn mọi người xuất hiện trong video." empty="Chưa có người giới thiệu. Thêm ở trang Người giới thiệu."/>}
          {error && <Alert tone="danger">{error}</Alert>}
        </div>
      </div>}
    </Sheet>
    <ConfirmModal open={confirmDelete} title="Xóa video khỏi thư viện?" tone="danger" confirmLabel="Xóa" loading={busy}
      description="Video sẽ ẩn khỏi thư viện và không thể gắn thêm sản phẩm. Lịch sử phiên LIVE vẫn được giữ."
      onConfirm={()=>void remove()} onClose={()=>setConfirmDelete(false)}/>
  </>
}

function Fact({label,children}:{label:string;children:ReactNode}){
  return <div><dt>{label}</dt><dd>{children}</dd></div>
}

/** Searchable checklist that stays usable with hundreds of entries; selected entries are shown as chips on top. */
function PickList({items,value,onChange,icon,noun,hint,empty}:{items:PickItem[];value:number[];onChange:(ids:number[])=>void;icon:ReactNode;noun:string;hint:string;empty:string}){
  const [query,setQuery] = useState('')
  const [onlySelected,setOnlySelected] = useState(false)
  const selected = useMemo(()=>new Set(value),[value])
  const byId = useMemo(()=>new Map(items.map(i=>[i.id, i])),[items])
  // Already-linked entries go first, but only as they were when the tab opened: ticking must not move rows under the cursor.
  const [initial] = useState(()=>new Set(value))
  const visible = useMemo(()=>{
    const q = fold(query.trim())
    return items.filter(i=>(!onlySelected || selected.has(i.id)) && (!q || fold(`${i.title} ${i.subtitle ?? ''}`).includes(q)))
      .sort((a,b)=>Number(initial.has(b.id)) - Number(initial.has(a.id)))
  },[items, query, onlySelected, selected, initial])
  const toggle = (id:number) => onChange(selected.has(id) ? value.filter(x=>x !== id) : [...value, id])
  if (!items.length) return <div className="cm-pick-empty">{icon}<span>{empty}</span></div>
  return <div className="cm-pick">
    <p className="cm-pick-hint">{hint}</p>
    {value.length > 0 && <div className="cm-pick-chips">{value.map(id=><span key={id} className="cm-pick-chip">
      {byId.get(id)?.title ?? `#${id}`}<button type="button" aria-label="Bỏ chọn" onClick={()=>toggle(id)}><X size={12}/></button></span>)}
      <button type="button" className="cm-pick-clear" onClick={()=>onChange([])}>Bỏ chọn tất cả</button></div>}
    <div className="cm-pick-bar">
      <SearchInput value={query} onChange={e=>setQuery(e.target.value)} placeholder={`Tìm ${noun}…`} aria-label={`Tìm ${noun}`}/>
      <SegmentedControl<'all'|'selected'> value={onlySelected ? 'selected' : 'all'} onChange={v=>setOnlySelected(v === 'selected')}
        items={[{value:'all',label:`Tất cả (${items.length})`},{value:'selected',label:`Đã chọn (${value.length})`}]}/>
    </div>
    <ul className="cm-pick-list" role="listbox" aria-multiselectable="true">
      {visible.length === 0 ? <li className="cm-pick-none">Không có {noun} khớp “{query}”</li>
      : visible.map(item=>{
        const on = selected.has(item.id)
        return <li key={item.id}><button type="button" role="option" aria-selected={on} className={'cm-pick-row' + (on ? ' is-on' : '')} onClick={()=>toggle(item.id)}>
          <span className="cm-pick-box">{on && <Check size={13}/>}</span>
          <span className="cm-pick-avatar">{item.image ? <img src={assetUrl(item.image) ?? undefined} alt="" loading="lazy"/> : icon}</span>
          <span className="cm-pick-text"><strong>{item.title}</strong>{item.subtitle && <small>{item.subtitle}</small>}</span>
        </button></li>
      })}
    </ul>
  </div>
}
