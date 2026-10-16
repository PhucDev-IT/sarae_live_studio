import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import {
  Alert, Badge, Button, Card, ConfirmModal, EmptyState, FileUpload, FilterBar, Input, LoadingState, Modal, MultiSelect, Pagination, Popover,
  SearchInput, SegmentedControl, Select, Sheet, SkeletonCard, StatCard, Switch, Table, TagsInput, useToast, type TableColumn,
} from './ui/core'
import {
  AlertTriangle, Copy, Eye, Film, FolderOpen, HardDrive, LayoutGrid, Link2, List, MoreHorizontal, Package, Pencil, Play,
  RefreshCw, Trash2, Upload, User,
} from 'lucide-react'
import { apiRequest, durationLabel, errorText, fileUrl, sizeLabel, setVideoProducts, type CatalogItem, type Media } from './mediaApi'
import VideoThumb, { type ClientMeta } from './VideoThumb'
import './media.css'

type SourceFilter = 'all'|'local'|'upload'
type SortKey = 'newest'|'oldest'|'name'|'size'
type ViewMode = 'grid'|'list'

const PAGE_SIZE = 24
const VIEW_KEY = 'sarae-media-view'
/** Product badges shown on a card before collapsing the rest into "+N". */
const CARD_PRODUCTS = 2

const parseTags = (value:string) => value.split(',').map(tag => tag.trim()).filter(Boolean)
const dateLabel = (value:string) => new Date(value).toLocaleString('vi-VN', { dateStyle:'short', timeStyle:'short' })
const readView = ():ViewMode => { try { return localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'grid' } catch { return 'grid' } }

export default function MediaPage(){
  const toast = useToast()
  const [items,setItems] = useState<Media[]>([])
  const [products,setProducts] = useState<CatalogItem[]>([])
  const [presenters,setPresenters] = useState<CatalogItem[]>([])
  const [loading,setLoading] = useState(true)
  const [loadError,setLoadError] = useState('')
  const [meta,setMeta] = useState<Record<number,ClientMeta>>({})

  const [query,setQuery] = useState('')
  const [source,setSource] = useState<SourceFilter>('all')
  const [productFilter,setProductFilter] = useState('all')
  const [sort,setSort] = useState<SortKey>('newest')
  const [view,setView] = useState<ViewMode>(readView)
  const [page,setPage] = useState(1)
  const [hideUnlinkedHint,setHideUnlinkedHint] = useState(false)

  const [uploadOpen,setUploadOpen] = useState(false)
  const [folderOpen,setFolderOpen] = useState(false)
  const [editing,setEditing] = useState<Media|null>(null)
  const [deleting,setDeleting] = useState<Media|null>(null)
  const [preview,setPreview] = useState<Media|null>(null)
  const [busy,setBusy] = useState(false)
  const [formError,setFormError] = useState('')

  const [file,setFile] = useState<File|null>(null)
  const [productIds,setProductIds] = useState<string[]>([])
  const [presenterId,setPresenterId] = useState('')
  const [tags,setTags] = useState<string[]>([])
  const [folder,setFolder] = useState('')
  const [recursive,setRecursive] = useState(false)

  const load = async () => {
    setLoadError('')
    try{
      const [videos, p, a] = await Promise.all([
        apiRequest<Media[]>('/media?limit=500'),
        apiRequest<CatalogItem[]>('/products?limit=500'),
        apiRequest<CatalogItem[]>('/presenters?limit=500'),
      ])
      setItems(videos);setProducts(p);setPresenters(a)
    }catch(e){setLoadError(errorText(e, 'Không tải được dữ liệu'))}
    finally{setLoading(false)}
  }
  useEffect(()=>{void load()},[])
  useEffect(()=>{try{localStorage.setItem(VIEW_KEY, view)}catch{/* storage unavailable */}},[view])

  const productName = useMemo(()=>new Map(products.map(p=>[p.id,p.name])),[products])
  const presenterName = useMemo(()=>new Map(presenters.map(p=>[p.id,p.name])),[presenters])

  const stats = useMemo(()=>{
    const local = items.filter(item=>item.local_path).length
    const linked = items.filter(item=>item.product_ids.length > 0).length
    const links = items.reduce((sum,item)=>sum + item.product_ids.length, 0)
    const bytes = items.reduce((sum,item)=>sum + item.size_bytes, 0)
    return { local, upload:items.length - local, linked, unlinked:items.length - linked, links, bytes }
  },[items])

  const filtered = useMemo(()=>{
    const needle = query.trim().toLocaleLowerCase('vi')
    const result = items.filter(item=>{
      if (source === 'local' && !item.local_path) return false
      if (source === 'upload' && item.local_path) return false
      if (productFilter === 'none' && item.product_ids.length > 0) return false
      if (productFilter !== 'all' && productFilter !== 'none' && !item.product_ids.includes(Number(productFilter))) return false
      if (!needle) return true
      const haystack = [item.filename, item.tags, ...item.product_ids.map(id=>productName.get(id)), presenterName.get(item.presenter_id ?? -1)].join(' ')
      return haystack.toLocaleLowerCase('vi').includes(needle)
    })
    const byDate = (a:Media,b:Media) => a.created_at.localeCompare(b.created_at) || a.id - b.id
    return result.sort(
      sort === 'oldest' ? byDate
      : sort === 'name' ? (a,b)=>a.filename.localeCompare(b.filename,'vi',{numeric:true})
      : sort === 'size' ? (a,b)=>b.size_bytes - a.size_bytes
      : (a,b)=>byDate(b,a)
    )
  },[items,query,source,productFilter,sort,productName,presenterName])

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const current = Math.min(page, pageCount)
  const visible = filtered.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE)
  const filtering = query !== '' || source !== 'all' || productFilter !== 'all'

  const reportMeta = (id:number, next:ClientMeta) => setMeta(prev=>prev[id]?.missing === next.missing && prev[id]?.duration === next.duration ? prev : { ...prev, [id]:{ ...prev[id], ...next } })
  const durationOf = (item:Media) => item.duration_seconds ?? meta[item.id]?.duration
  const resolutionOf = (item:Media) => {
    const width = item.width ?? meta[item.id]?.width, height = item.height ?? meta[item.id]?.height
    return width && height ? { width, height } : null
  }

  const resetForm = (item?:Media) => {
    setFile(null);setFormError('')
    setProductIds((item?.product_ids ?? []).map(String))
    setPresenterId(item?.presenter_id == null ? '' : String(item.presenter_id))
    setTags(parseTags(item?.tags ?? ''))
  }
  const openUpload = () => {resetForm();setUploadOpen(true)}
  const openEdit = (item:Media) => {resetForm(item);setEditing(item)}
  const clearFilters = () => {setQuery('');setSource('all');setProductFilter('all');setPage(1)}
  /** Wraps a filter setter so any filter change jumps back to the first page. */
  const resetting = <T,>(set:(value:T)=>void) => (value:T) => {set(value);setPage(1)}

  const upload = async (event:FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!file) {setFormError('Chọn file MP4 để upload');return}
    setBusy(true);setFormError('')
    try{
      const body = new FormData()
      body.append('file',file)
      if(presenterId) body.append('presenter_id',presenterId)
      body.append('tags',tags.join(', '))
      const created = await apiRequest<Media>('/media/upload',{method:'POST',body})
      setUploadOpen(false)
      try{
        if (productIds.length) await setVideoProducts(created.id, productIds.map(Number))
        toast.push({tone:'success',title:'Đã upload video',description:file.name})
      }catch(e){toast.push({tone:'warning',title:'Đã upload nhưng chưa gắn đủ sản phẩm',description:errorText(e, 'Thử gắn lại trong phần chỉnh sửa video.')})}
      await load()
    }catch(e){setFormError(errorText(e, 'Có lỗi khi upload video'))}
    finally{setBusy(false)}
  }
  const saveEdit = async (event:FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!editing) return
    setBusy(true);setFormError('')
    try{
      await apiRequest<Media>('/media/'+editing.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({
        presenter_id:presenterId?Number(presenterId):null, tags:tags.join(', '),
      })})
      await setVideoProducts(editing.id, productIds.map(Number))
      setEditing(null)
      toast.push({tone:'success',title:'Đã lưu thay đổi',description:editing.filename})
      await load()
    }catch(e){setFormError(errorText(e, 'Có lỗi khi lưu video'));await load()}
    finally{setBusy(false)}
  }
  const scan = async (event:FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setBusy(true);setFormError('')
    try {
      const result = await apiRequest<{found:number;created:number;existing:number}>('/media/local/scan',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folder,recursive}),
      })
      setFolderOpen(false)
      toast.push({tone:'success',title:`Đã thêm ${result.created} video`,description:`Tìm thấy ${result.found} file · ${result.existing} video đã có trong thư viện.`})
      await load()
    } catch(e) {setFormError(errorText(e, 'Không quét được thư mục'))}
    finally {setBusy(false)}
  }
  const remove = async () => {
    if (!deleting) return
    setBusy(true)
    try{
      await apiRequest<void>('/media/'+deleting.id,{method:'DELETE'})
      toast.push({tone:'success',title:deleting.local_path?'Đã bỏ video khỏi thư viện':'Đã xóa video',description:deleting.filename})
      setDeleting(null)
      await load()
    }catch(e){toast.push({tone:'danger',title:'Không xóa được video',description:errorText(e, '')})}
    finally{setBusy(false)}
  }
  const copyPath = async (item:Media) => {
    if (!item.local_path) return
    try{await navigator.clipboard.writeText(item.local_path);toast.push({tone:'success',title:'Đã copy đường dẫn'})}
    catch{toast.push({tone:'danger',title:'Không copy được',description:item.local_path})}
  }

  const actionsFor = (item:Media):MenuAction[] => [
    { label:'Xem video', icon:<Eye size={15}/>, onSelect:()=>setPreview(item) },
    { label:'Chỉnh sửa', icon:<Pencil size={15}/>, onSelect:()=>openEdit(item) },
    ...(item.local_path ? [{ label:'Copy đường dẫn', icon:<Copy size={15}/>, onSelect:()=>void copyPath(item) }] : []),
    { label:item.local_path?'Bỏ khỏi thư viện':'Xóa video', icon:<Trash2 size={15}/>, danger:true, separatorBefore:true, onSelect:()=>setDeleting(item) },
  ]
  const productLabel = (id:number) => productName.get(id) ?? 'Sản phẩm #' + id
  /** Product badges for a video; `limit` collapses the remainder into a "+N" badge listing them on hover. */
  const linkBadges = (item:Media, limit = Infinity) => <>
    {item.product_ids.length === 0
      ? <Badge tone="warning">Chưa gắn sản phẩm</Badge>
      : item.product_ids.slice(0, limit).map(id=><Badge key={id} tone="primary"><Package size={12}/>{productLabel(id)}</Badge>)}
    {item.product_ids.length > limit && <span title={item.product_ids.slice(limit).map(productLabel).join(', ')}><Badge tone="primary">+{item.product_ids.length - limit} sản phẩm</Badge></span>}
    {item.presenter_id != null && <Badge><User size={12}/>{presenterName.get(item.presenter_id) ?? 'Người giới thiệu #' + item.presenter_id}</Badge>}
  </>

  const columns:TableColumn<Media>[] = [
    { key:'filename', header:'Video', render:item=><div className="media-row-title">
      <button className="media-row-thumb" onClick={()=>setPreview(item)} aria-label={'Xem '+item.filename}><VideoThumb id={item.id} onMeta={next=>reportMeta(item.id,next)}/></button>
      <div><strong title={item.filename}>{item.filename}</strong><small>{item.local_path ? 'Local' : 'Upload'} · {dateLabel(item.created_at)}</small></div>
    </div> },
    { key:'duration_seconds', header:'Thời lượng', width:110, render:item=>durationLabel(durationOf(item)) ?? '—' },
    { key:'size_bytes', header:'Dung lượng', width:110, render:item=>sizeLabel(item.size_bytes) },
    { key:'product_ids', header:'Sản phẩm · người giới thiệu', render:item=><div className="media-badges">{linkBadges(item, CARD_PRODUCTS)}{meta[item.id]?.missing && <MissingBadge/>}</div> },
    { key:'tags', header:'Tags', render:item=><TagList tags={parseTags(item.tags)}/> },
    { key:'actions', header:'', width:56, align:'right', render:item=><ActionMenu actions={actionsFor(item)}/> },
  ]

  const productOptions = products.map(p=>({value:String(p.id),label:p.name}))
  const presenterOptions = [{value:'',label:'Chưa liên kết'},...presenters.map(p=>({value:String(p.id),label:p.name}))]
  const linkFields = <>
    <MultiSelect label="Sản phẩm sử dụng video" placeholder="Chưa gắn sản phẩm" maxVisible={4} value={productIds} options={productOptions} onChange={setProductIds}
      hint="Một video có thể dùng cho nhiều sản phẩm. Sản phẩm mới gắn sẽ phát video này ở cuối danh sách; đổi thứ tự tại trang Video theo sản phẩm."/>
    <Select label="Người giới thiệu" value={presenterId} options={presenterOptions} onChange={setPresenterId}/>
    <TagsInput label="Tags" hint="Nhấn Enter hoặc dấu phẩy để thêm tag" value={tags} onChange={setTags} placeholder="áo trễ vai, review, quay studio…"/>
  </>

  return <div className="media-page">
    <section className="media-stats" aria-label="Thống kê thư viện">
      <StatCard label="Tổng video" icon={<Film size={18}/>} value={items.length} delta={`${stats.local} local · ${stats.upload} upload`} trend="neutral"/>
      <StatCard label="Dung lượng" icon={<HardDrive size={18}/>} value={sizeLabel(stats.bytes)} delta={items.length ? `TB ${sizeLabel(stats.bytes / items.length)}/video` : 'Chưa có dữ liệu'} trend="neutral"/>
      <StatCard label="Đã gắn sản phẩm" icon={<Link2 size={18}/>} value={`${stats.linked}/${items.length}`} delta={items.length ? `${Math.round(stats.linked / items.length * 100)}% thư viện · ${stats.links} liên kết` : '—'} trend={stats.linked === items.length ? 'up' : 'neutral'}/>
      <StatCard label="Chưa gắn sản phẩm" icon={<AlertTriangle size={18}/>} value={stats.unlinked} delta={stats.unlinked ? 'Cần gắn trước khi lên LIVE' : 'Đã gắn đầy đủ'} trend={stats.unlinked ? 'down' : 'up'}/>
    </section>

    {!loading && stats.unlinked > 0 && !hideUnlinkedHint && <Alert tone="warning" title={`${stats.unlinked} video chưa gắn sản phẩm`} onClose={()=>setHideUnlinkedHint(true)}>
      <div className="media-hint">
        <span>Gắn sản phẩm giúp chọn đúng video khi chuẩn bị phiên LIVE.</span>
        <Button size="sm" variant="outline" onClick={()=>resetting(setProductFilter)('none')}>Lọc các video này</Button>
        <Button size="sm" variant="ghost" onClick={()=>{window.location.hash='/product-videos'}}>Gắn theo sản phẩm →</Button>
      </div>
    </Alert>}

    <Card
      className="media-library"
      padding={false}
      title="Thư viện MP4"
      description={filtering ? `${filtered.length} / ${items.length} video khớp bộ lọc` : `${items.length} video trên máy chạy Backend`}
      actions={<div className="media-actions">
        <Button variant="ghost" aria-label="Tải lại" title="Tải lại" onClick={()=>{setLoading(true);void load()}}><RefreshCw size={16}/></Button>
        <Button variant="outline" leftIcon={<FolderOpen size={16}/>} onClick={()=>{setFormError('');setFolderOpen(true)}}>Liên kết thư mục</Button>
        <Button leftIcon={<Upload size={16}/>} onClick={openUpload}>Upload MP4</Button>
      </div>}
    >
      <FilterBar className="media-filters" actions={<SegmentedControl<ViewMode> value={view} onChange={setView} items={[
        {value:'grid',label:<span className="media-seg"><LayoutGrid size={15}/>Lưới</span>},
        {value:'list',label:<span className="media-seg"><List size={15}/>Danh sách</span>},
      ]}/>}>
        <SearchInput value={query} onChange={e=>resetting(setQuery)(e.target.value)} placeholder="Tìm theo tên file, tags, sản phẩm…" aria-label="Tìm video"/>
        <Select value={source} onChange={value=>resetting(setSource)(value as SourceFilter)} options={[{value:'all',label:'Mọi nguồn'},{value:'local',label:'Local (không copy)'},{value:'upload',label:'Đã upload'}]}/>
        <Select value={productFilter} onChange={resetting(setProductFilter)} options={[{value:'all',label:'Mọi sản phẩm'},{value:'none',label:'⚠ Chưa gắn sản phẩm'},...products.map(p=>({value:String(p.id),label:p.name}))]}/>
        <Select value={sort} onChange={value=>resetting(setSort)(value as SortKey)} options={[{value:'newest',label:'Mới nhất'},{value:'oldest',label:'Cũ nhất'},{value:'name',label:'Tên A → Z'},{value:'size',label:'Dung lượng lớn nhất'}]}/>
      </FilterBar>

      <div className="media-body">
        {loading ? <div className="media-grid">{Array.from({length:8},(_,i)=><SkeletonCard key={i} lines={2}/>)}</div>
        : loadError ? <LoadingState error={loadError} errorTitle="Không tải được thư viện" onRetry={()=>{setLoading(true);void load()}}>{null}</LoadingState>
        : filtered.length === 0 ? (filtering
          ? <EmptyState icon={<Film size={32}/>} title="Không có video khớp bộ lọc" description="Thử từ khóa khác hoặc bỏ bớt điều kiện lọc." actionLabel="Xóa bộ lọc" onAction={clearFilters}/>
          : <EmptyState icon={<Film size={32}/>} title="Thư viện chưa có video" description="Upload file MP4 hoặc liên kết một thư mục trên máy chạy Backend/OBS." actionLabel="Upload MP4 đầu tiên" onAction={openUpload}/>)
        : view === 'grid'
          ? <div className="media-grid">{visible.map(item=>{
            const duration = durationLabel(durationOf(item)), resolution = resolutionOf(item)
            return <article className="media-card" key={item.id}>
              <button className="media-thumb" onClick={()=>setPreview(item)} aria-label={'Xem '+item.filename}>
                <VideoThumb id={item.id} onMeta={next=>reportMeta(item.id,next)}/>
                <span className="media-thumb-play"><Play size={18} fill="currentColor"/></span>
                <span className="media-chip media-chip--source">{item.local_path ? <><FolderOpen size={12}/>Local</> : <><HardDrive size={12}/>Upload</>}</span>
                {duration && <span className="media-chip media-chip--duration">{duration}</span>}
              </button>
              <div className="media-card-body">
                <div className="media-card-title"><strong title={item.filename}>{item.filename}</strong><ActionMenu actions={actionsFor(item)}/></div>
                <small className="media-card-meta">{sizeLabel(item.size_bytes)}{resolution && <> · {resolution.width}×{resolution.height} · {resolution.height > resolution.width ? 'Dọc' : 'Ngang'}</>}</small>
                <div className="media-badges">{linkBadges(item, CARD_PRODUCTS)}{meta[item.id]?.missing && <MissingBadge/>}</div>
                <TagList tags={parseTags(item.tags)} limit={3}/>
              </div>
            </article>
          })}</div>
          : <Table<Media> columns={columns} data={visible}/>}
      </div>

      {!loading && pageCount > 1 && <div className="media-footer">
        <span>{(current - 1) * PAGE_SIZE + 1}–{Math.min(current * PAGE_SIZE, filtered.length)} / {filtered.length} video</span>
        <Pagination page={current} total={pageCount} onChange={setPage}/>
      </div>}
    </Card>

    <Modal open={uploadOpen} title="Upload video MP4" size="md" onClose={()=>{if(!busy)setUploadOpen(false)}} footer={<>
      <Button variant="ghost" disabled={busy} onClick={()=>setUploadOpen(false)}>Hủy</Button>
      <Button type="submit" form="media-upload-form" loading={busy} leftIcon={<Upload size={16}/>}>Upload video</Button>
    </>}>
      <form id="media-upload-form" className="media-form" onSubmit={event=>void upload(event)}>
        <FileUpload accept=".mp4,video/mp4" maxSizeMb={500} label="Kéo thả file MP4 vào đây hoặc bấm để chọn" onFiles={files=>{setFile(files[0] ?? null);setFormError('')}}/>
        {file && <div className="media-file-picked"><Film size={16}/><strong>{file.name}</strong><span>{sizeLabel(file.size)}</span></div>}
        {linkFields}
        {formError && <Alert tone="danger">{formError}</Alert>}
      </form>
    </Modal>

    <Modal open={folderOpen} title="Liên kết thư mục video" size="md" onClose={()=>{if(!busy)setFolderOpen(false)}} footer={<>
      <Button variant="ghost" disabled={busy} onClick={()=>setFolderOpen(false)}>Hủy</Button>
      <Button type="submit" form="media-folder-form" loading={busy} leftIcon={<FolderOpen size={16}/>}>Quét và liên kết</Button>
    </>}>
      <form id="media-folder-form" className="media-form" onSubmit={event=>void scan(event)}>
        <Alert tone="info" title="Không copy file">Chỉ quét file MP4 trong thư mục trên máy chạy Backend/OBS. File gốc được giữ nguyên tại chỗ.</Alert>
        <Input label="Thư mục Windows" required value={folder} onChange={e=>setFolder(e.target.value)} placeholder="D:\Sarae\Videos"/>
        <Switch label="Quét cả thư mục con" checked={recursive} onChange={e=>setRecursive(e.target.checked)}/>
        {formError && <Alert tone="danger">{formError}</Alert>}
      </form>
    </Modal>

    <Sheet open={editing !== null} title="Thông tin video" width={480} onClose={()=>{if(!busy)setEditing(null)}} footer={<div className="media-sheet-actions">
      <Button variant="ghost" disabled={busy} onClick={()=>setEditing(null)}>Hủy</Button>
      <Button type="submit" form="media-edit-form" loading={busy}>Lưu thay đổi</Button>
    </div>}>
      {editing && <div className="media-form">
        <video className="media-player media-player--sheet" src={fileUrl(editing.id)} controls preload="metadata" playsInline/>
        <dl className="media-facts">
          <Fact label="Tên file">{editing.filename}</Fact>
          <Fact label="Nguồn">{editing.local_path ? 'Local · không copy' : 'Upload · lưu trên Backend'}</Fact>
          {editing.local_path && <Fact label="Đường dẫn"><span className="media-path">{editing.local_path}</span><button type="button" className="media-icon-btn" aria-label="Copy đường dẫn" title="Copy đường dẫn" onClick={()=>void copyPath(editing)}><Copy size={14}/></button></Fact>}
          <Fact label="Dung lượng">{sizeLabel(editing.size_bytes)}</Fact>
          <Fact label="Thời lượng">{durationLabel(durationOf(editing)) ?? '—'}</Fact>
          <Fact label="Độ phân giải">{(()=>{const r = resolutionOf(editing);return r ? `${r.width}×${r.height}` : '—'})()}</Fact>
          <Fact label="Ngày thêm">{dateLabel(editing.created_at)}</Fact>
          <Fact label="Đang dùng cho">{editing.product_ids.length ? <div className="media-badges">{linkBadges({...editing, presenter_id:null})}</div> : 'Chưa gắn sản phẩm'}</Fact>
        </dl>
        <form id="media-edit-form" className="media-form" onSubmit={event=>void saveEdit(event)}>
          {linkFields}
          {formError && <Alert tone="danger">{formError}</Alert>}
        </form>
      </div>}
    </Sheet>

    <Modal open={preview !== null} title={preview?.filename ?? 'Xem video'} size="lg" onClose={()=>setPreview(null)} footer={preview && <>
      <div className="media-badges media-preview-links">{linkBadges(preview)}</div>
      <Button variant="outline" leftIcon={<Pencil size={15}/>} onClick={()=>{const item = preview;setPreview(null);openEdit(item)}}>Chỉnh sửa</Button>
    </>}>
      {preview && <video className="media-player" src={fileUrl(preview.id)} controls autoPlay playsInline/>}
    </Modal>

    <ConfirmModal
      open={deleting !== null}
      loading={busy}
      onClose={()=>{if(!busy)setDeleting(null)}}
      onConfirm={()=>void remove()}
      title={deleting?.local_path ? 'Bỏ video khỏi thư viện?' : 'Xóa video?'}
      description={deleting?.local_path ? `"${deleting.filename}" sẽ bị bỏ khỏi thư viện. File gốc trên ổ đĩa được giữ nguyên.` : `"${deleting?.filename ?? ''}" và file đã upload sẽ bị xóa vĩnh viễn.`}
      confirmLabel={deleting?.local_path ? 'Bỏ khỏi thư viện' : 'Xóa video'}
    />
  </div>
}

type MenuAction = { label:string; icon:ReactNode; onSelect:()=>void; danger?:boolean; separatorBefore?:boolean }

/** Row/card overflow menu. Controlled so the popover closes after an item is chosen. */
function ActionMenu({actions}:{actions:MenuAction[]}){
  const [open,setOpen] = useState(false)
  return <Popover open={open} onOpenChange={setOpen} align="end" trigger={<button type="button" className="media-icon-btn" aria-label="Thao tác" aria-expanded={open}><MoreHorizontal size={17}/></button>}>
    <div className="ui-menu-list media-menu">{actions.map(action=><div key={action.label}>
      {action.separatorBefore && <hr/>}
      <div className="ui-menu-list__row"><button type="button" className={action.danger ? 'is-danger' : ''} onClick={()=>{setOpen(false);action.onSelect()}}>{action.icon}<span>{action.label}</span></button></div>
    </div>)}</div>
  </Popover>
}

function TagList({tags,limit}:{tags:string[];limit?:number}){
  if (!tags.length) return null
  const shown = limit ? tags.slice(0, limit) : tags
  return <div className="media-tags">{shown.map(tag=><span key={tag}>#{tag}</span>)}{shown.length < tags.length && <span className="media-tags-more">+{tags.length - shown.length}</span>}</div>
}

function MissingBadge(){
  return <Badge tone="danger"><AlertTriangle size={12}/>Không đọc được file</Badge>
}

function Fact({label,children}:{label:string;children:ReactNode}){
  return <div className="media-fact"><dt>{label}</dt><dd>{children}</dd></div>
}
