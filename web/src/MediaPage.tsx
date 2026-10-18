import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import {
  Alert, Badge, Button, Card, EmptyState, FilterBar, LoadingState, MultiSelect, Pagination, SearchInput, SegmentedControl, Select, Sheet,
  SkeletonCard, StatCard, Table, TagsInput, useToast, type TableColumn,
} from './ui/core'
import { AlertTriangle, Film, HardDrive, LayoutGrid, Link2, List, MonitorPlay, Package, Pencil, RefreshCw, ScanSearch, User, Users } from 'lucide-react'
import { apiRequest, durationLabel, errorText, listVideos, onAgent, sizeLabel, setVideoPresenters, setVideoProducts, type CatalogItem, type Media, type OwnerAgent } from './mediaApi'
import { WithAgent } from './agentContext'
import VideoThumb from './VideoThumb'
import './media.css'

type Availability = 'all'|'available'|'missing'
type SortKey = 'name'|'size'|'longest'
type ViewMode = 'grid'|'list'

const PAGE_SIZE = 24
const VIEW_KEY = 'sarae-media-view'
/** Product badges shown on a card before collapsing the rest into "+N". */
const CARD_PRODUCTS = 2

const parseTags = (value:string) => value.split(',').map(tag => tag.trim()).filter(Boolean)
const readView = ():ViewMode => { try { return localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'grid' } catch { return 'grid' } }

/** The video library of the Windows Agent this tab works with (switching Agent remounts the library). */
export default function MediaPage(){
  return <WithAgent purpose="Thư viện video">{agent=><MediaLibrary key={agent.id} agent={agent}/>}</WithAgent>
}

/** MP4 files on one Windows Agent, with tags and product / presenter links (links are per Agent). */
function MediaLibrary({agent}:{agent:OwnerAgent}){
  const toast = useToast()
  const [items,setItems] = useState<Media[]>([])
  const [products,setProducts] = useState<CatalogItem[]>([])
  const [presenters,setPresenters] = useState<CatalogItem[]>([])
  const [loading,setLoading] = useState(true)
  const [loadError,setLoadError] = useState('')

  const [query,setQuery] = useState('')
  const [availability,setAvailability] = useState<Availability>('all')
  const [productFilter,setProductFilter] = useState('all')
  const [presenterFilter,setPresenterFilter] = useState('all')
  const [sort,setSort] = useState<SortKey>('name')
  const [view,setView] = useState<ViewMode>(readView)
  const [page,setPage] = useState(1)

  const [editing,setEditing] = useState<Media|null>(null)
  const [busy,setBusy] = useState(false)
  const [scanning,setScanning] = useState(false)
  const [formError,setFormError] = useState('')
  const [productIds,setProductIds] = useState<string[]>([])
  const [presenterIds,setPresenterIds] = useState<string[]>([])
  const [tags,setTags] = useState<string[]>([])

  const load = async () => {
    setLoadError('')
    try{
      const [videos, p, a] = await Promise.all([
        listVideos(agent.id),
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
    const linked = items.filter(item=>item.product_ids.length > 0).length
    const withPeople = items.filter(item=>item.presenter_ids.length > 0).length
    const links = items.reduce((sum,item)=>sum + item.product_ids.length, 0)
    const bytes = items.reduce((sum,item)=>sum + item.size_bytes, 0)
    const missing = items.filter(item=>!item.available).length
    return { linked, links, withPeople, bytes, missing }
  },[items])

  const filtered = useMemo(()=>{
    const needle = query.trim().toLocaleLowerCase('vi')
    const result = items.filter(item=>{
      if (availability === 'available' && !item.available) return false
      if (availability === 'missing' && item.available) return false
      if (productFilter === 'none' && item.product_ids.length > 0) return false
      if (productFilter !== 'all' && productFilter !== 'none' && !item.product_ids.includes(Number(productFilter))) return false
      if (presenterFilter === 'none' && item.presenter_ids.length > 0) return false
      if (presenterFilter !== 'all' && presenterFilter !== 'none' && !item.presenter_ids.includes(Number(presenterFilter))) return false
      if (!needle) return true
      const haystack = [item.filename, item.tags, ...item.product_ids.map(id=>productName.get(id)), ...item.presenter_ids.map(id=>presenterName.get(id))].join(' ')
      return haystack.toLocaleLowerCase('vi').includes(needle)
    })
    return result.sort(
      sort === 'size' ? (a,b)=>b.size_bytes - a.size_bytes
      : sort === 'longest' ? (a,b)=>(b.duration_seconds ?? 0) - (a.duration_seconds ?? 0)
      : (a,b)=>a.filename.localeCompare(b.filename,'vi',{numeric:true})
    )
  },[items,query,availability,productFilter,presenterFilter,sort,productName,presenterName])

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const current = Math.min(page, pageCount)
  const visible = filtered.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE)
  const filtering = query !== '' || availability !== 'all' || productFilter !== 'all' || presenterFilter !== 'all'

  const openEdit = (item:Media) => {
    setFormError('')
    setProductIds(item.product_ids.map(String))
    setPresenterIds(item.presenter_ids.map(String))
    setTags(parseTags(item.tags))
    setEditing(item)
  }
  const clearFilters = () => {setQuery('');setAvailability('all');setProductFilter('all');setPresenterFilter('all');setPage(1)}
  /** Wraps a filter setter so any filter change jumps back to the first page. */
  const resetting = <T,>(set:(value:T)=>void) => (value:T) => {set(value);setPage(1)}

  const saveEdit = async (event:FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!editing) return
    setBusy(true);setFormError('')
    try{
      await apiRequest<Media>(onAgent('/media/'+editing.id, agent.id),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({tags:tags.join(', ')})})
      await Promise.all([setVideoProducts(agent.id, editing.id, productIds.map(Number)), setVideoPresenters(agent.id, editing.id, presenterIds.map(Number))])
      setEditing(null)
      toast.push({tone:'success',title:'Đã lưu thay đổi',description:editing.filename})
      await load()
    }catch(e){setFormError(errorText(e, 'Có lỗi khi lưu video'));await load()}
    finally{setBusy(false)}
  }
  /** Ask the Agent to rescan its folders now (it also syncs about once a minute on its own). */
  const rescan = async () => {
    setScanning(true)
    try{
      await apiRequest(onAgent('/media/rescan', agent.id),{method:'POST'})
      toast.push({tone:'success',title:'Đã quét lại thư mục trên Agent'})
      await load()
    }catch(e){toast.push({tone:'danger',title:'Không quét lại được',description:errorText(e, '')})}
    finally{setScanning(false)}
  }

  const productLabel = (id:number) => productName.get(id) ?? 'Sản phẩm #' + id
  const presenterLabel = (id:number) => presenterName.get(id) ?? 'Người #' + id
  /** Product badges for a video; `limit` collapses the remainder into a "+N" badge listing them on hover. */
  const linkBadges = (item:Media, limit = Infinity) => <>
    {item.product_ids.length === 0
      ? <Badge tone="warning">Chưa gắn sản phẩm</Badge>
      : item.product_ids.slice(0, limit).map(id=><Badge key={id} tone="primary"><Package size={12}/>{productLabel(id)}</Badge>)}
    {item.product_ids.length > limit && <span title={item.product_ids.slice(limit).map(productLabel).join(', ')}><Badge tone="primary">+{item.product_ids.length - limit} sản phẩm</Badge></span>}
  </>
  /** People in a video; same collapsing rule as product badges. */
  const peopleBadges = (item:Media, limit = Infinity) => <>
    {item.presenter_ids.slice(0, limit).map(id=><Badge key={id} tone="info"><User size={12}/>{presenterLabel(id)}</Badge>)}
    {item.presenter_ids.length > limit && <span title={item.presenter_ids.slice(limit).map(presenterLabel).join(', ')}><Badge tone="info">+{item.presenter_ids.length - limit} người</Badge></span>}
  </>

  const columns:TableColumn<Media>[] = [
    { key:'filename', header:'Video', render:item=><div className="media-row-title">
      <button className="media-row-thumb" onClick={()=>openEdit(item)} aria-label={'Sửa '+item.filename}><VideoThumb src={item.thumb_url}/></button>
      <div><strong title={item.filename}>{item.filename}</strong><small>{item.available ? 'Có trên máy' : 'Không còn trên máy'}</small></div>
    </div> },
    { key:'duration_seconds', header:'Thời lượng', width:110, render:item=>durationLabel(item.duration_seconds) ?? '—' },
    { key:'size_bytes', header:'Dung lượng', width:110, render:item=>sizeLabel(item.size_bytes) },
    { key:'product_ids', header:'Sản phẩm · người trong video', render:item=><div className="media-badges">{linkBadges(item, CARD_PRODUCTS)}{peopleBadges(item, CARD_PRODUCTS)}{!item.available && <MissingBadge/>}</div> },
    { key:'tags', header:'Tags', render:item=><TagList tags={parseTags(item.tags)}/> },
    { key:'actions', header:'', width:56, align:'right', render:item=><button type="button" className="media-icon-btn" aria-label="Chỉnh sửa" title="Chỉnh sửa" onClick={()=>openEdit(item)}><Pencil size={15}/></button> },
  ]

  const productOptions = products.map(p=>({value:String(p.id),label:p.name}))
  const presenterOptions = presenters.map(p=>({value:String(p.id),label:p.name}))

  return <div className="media-page">
    <div className={'media-agent' + (agent.online ? ' is-online' : '')}>
      <MonitorPlay size={18}/>
      <span>Video trên máy <b>{agent.hostname}</b> · {agent.online ? 'Online' : 'Offline'} · thêm / bớt thư mục video trong ứng dụng Sarae Agent. File MP4 không được tải lên máy chủ. Đổi máy ở góc trên bên phải.</span>
      <Button size="sm" variant="outline" leftIcon={<ScanSearch size={15}/>} loading={scanning} disabled={!agent.online} onClick={()=>void rescan()}>Quét lại</Button>
    </div>

    <section className="media-stats" aria-label="Thống kê thư viện">
      <StatCard label="Tổng video" icon={<Film size={18}/>} value={items.length} delta={stats.missing ? `${stats.missing} không còn trên máy` : `trên ${agent.hostname}`} trend="neutral"/>
      <StatCard label="Dung lượng" icon={<HardDrive size={18}/>} value={sizeLabel(stats.bytes)} delta={items.length ? `TB ${sizeLabel(stats.bytes / items.length)}/video` : 'Chưa có dữ liệu'} trend="neutral"/>
      <StatCard label="Đã gắn sản phẩm" icon={<Link2 size={18}/>} value={`${stats.linked}/${items.length}`} delta={items.length ? `${Math.round(stats.linked / items.length * 100)}% thư viện · ${stats.links} liên kết` : '—'} trend={stats.linked === items.length ? 'up' : 'neutral'}/>
      <StatCard label="Đã gắn người" icon={<Users size={18}/>} value={`${stats.withPeople}/${items.length}`} delta={items.length - stats.withPeople ? `${items.length - stats.withPeople} video chưa rõ người trong video` : 'Đã gắn đầy đủ'} trend={stats.withPeople === items.length ? 'up' : 'neutral'}/>
    </section>

    <Card
      className="media-library"
      padding={false}
      title="Thư viện video"
      description={filtering ? `${filtered.length} / ${items.length} video khớp bộ lọc` : `${items.length} video trên Windows Agent`}
      actions={<div className="media-actions">
        <Button variant="ghost" aria-label="Tải lại" title="Tải lại" onClick={()=>{setLoading(true);void load()}}><RefreshCw size={16}/></Button>
      </div>}
    >
      <FilterBar className="media-filters" actions={<SegmentedControl<ViewMode> value={view} onChange={setView} items={[
        {value:'grid',label:<span className="media-seg"><LayoutGrid size={15}/>Lưới</span>},
        {value:'list',label:<span className="media-seg"><List size={15}/>Danh sách</span>},
      ]}/>}>
        <SearchInput value={query} onChange={e=>resetting(setQuery)(e.target.value)} placeholder="Tìm theo tên file, tags, sản phẩm…" aria-label="Tìm video"/>
        <Select value={availability} onChange={value=>resetting(setAvailability)(value as Availability)} options={[{value:'all',label:'Mọi video'},{value:'available',label:'Có trên máy'},{value:'missing',label:'Không còn trên máy'}]}/>
        <Select value={productFilter} onChange={resetting(setProductFilter)} options={[{value:'all',label:'Mọi sản phẩm'},{value:'none',label:'Chưa gắn sản phẩm'},...products.map(p=>({value:String(p.id),label:p.name}))]}/>
        <Select value={presenterFilter} onChange={resetting(setPresenterFilter)} options={[{value:'all',label:'Mọi người'},{value:'none',label:'Chưa gắn người'},...presenters.map(p=>({value:String(p.id),label:p.name}))]}/>
        <Select value={sort} onChange={value=>resetting(setSort)(value as SortKey)} options={[{value:'name',label:'Tên A → Z'},{value:'size',label:'Dung lượng lớn nhất'},{value:'longest',label:'Dài nhất'}]}/>
      </FilterBar>

      <div className="media-body">
        {loading ? <div className="media-grid">{Array.from({length:8},(_,i)=><SkeletonCard key={i} lines={2}/>)}</div>
        : loadError ? <LoadingState error={loadError} errorTitle="Không tải được thư viện" onRetry={()=>{setLoading(true);void load()}}>{null}</LoadingState>
        : filtered.length === 0 ? (filtering
          ? <EmptyState icon={<Film size={32}/>} title="Không có video khớp bộ lọc" description="Thử từ khóa khác hoặc bỏ bớt điều kiện lọc." actionLabel="Xóa bộ lọc" onAction={clearFilters}/>
          : <EmptyState icon={<Film size={32}/>} title="Thư viện chưa có video"
            description="Thêm thư mục chứa MP4 trong ứng dụng Sarae Agent trên máy, rồi bấm Quét lại."/>)
        : view === 'grid'
          ? <div className="media-grid">{visible.map(item=>{
            const duration = durationLabel(item.duration_seconds)
            return <article className={'media-card' + (item.available ? '' : ' is-missing')} key={item.id}>
              <button className="media-thumb" onClick={()=>openEdit(item)} aria-label={'Sửa '+item.filename}>
                <VideoThumb src={item.thumb_url}/>
                {duration && <span className="media-chip media-chip--duration">{duration}</span>}
              </button>
              <div className="media-card-body">
                <div className="media-card-title"><strong title={item.filename}>{item.filename}</strong>
                  <button type="button" className="media-icon-btn" aria-label="Chỉnh sửa" title="Chỉnh sửa" onClick={()=>openEdit(item)}><Pencil size={15}/></button></div>
                <small className="media-card-meta">{sizeLabel(item.size_bytes)}</small>
                <div className="media-badges">{linkBadges(item, CARD_PRODUCTS)}{peopleBadges(item, CARD_PRODUCTS)}{!item.available && <MissingBadge/>}</div>
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

    <Sheet open={editing !== null} title="Thông tin video" width={480} onClose={()=>{if(!busy)setEditing(null)}} footer={<div className="media-sheet-actions">
      <Button variant="ghost" disabled={busy} onClick={()=>setEditing(null)}>Hủy</Button>
      <Button type="submit" form="media-edit-form" loading={busy}>Lưu thay đổi</Button>
    </div>}>
      {editing && <div className="media-form">
        <div className="media-sheet-thumb"><VideoThumb src={editing.thumb_url}/></div>
        <dl className="media-facts">
          <Fact label="Tên file">{editing.filename}</Fact>
          <Fact label="Trên máy">{agent.hostname} · {editing.available ? 'có file' : 'không còn file'}</Fact>
          <Fact label="Dung lượng">{sizeLabel(editing.size_bytes)}</Fact>
          <Fact label="Thời lượng">{durationLabel(editing.duration_seconds) ?? '—'}</Fact>
        </dl>
        <form id="media-edit-form" className="media-form" onSubmit={event=>void saveEdit(event)}>
          <MultiSelect label="Sản phẩm sử dụng video" placeholder="Chưa gắn sản phẩm" maxVisible={4} value={productIds} options={productOptions} onChange={setProductIds}
            hint="Một video có thể dùng cho nhiều sản phẩm. Sản phẩm mới gắn sẽ phát video này ở cuối danh sách; đổi thứ tự tại trang Video theo sản phẩm."/>
          <MultiSelect label="Người xuất hiện trong video" placeholder="Chưa gắn người" maxVisible={4} value={presenterIds} options={presenterOptions} onChange={setPresenterIds}
            hint="Chọn tất cả những người có mặt trong video."/>
          <TagsInput label="Tags" hint="Nhấn Enter hoặc dấu phẩy để thêm tag" value={tags} onChange={setTags} placeholder="áo trễ vai, review, quay studio…"/>
          {formError && <Alert tone="danger">{formError}</Alert>}
        </form>
      </div>}
    </Sheet>
  </div>
}

function TagList({tags,limit}:{tags:string[];limit?:number}){
  if (!tags.length) return null
  const shown = limit ? tags.slice(0, limit) : tags
  return <div className="media-tags">{shown.map(tag=><span key={tag}>#{tag}</span>)}{shown.length < tags.length && <span className="media-tags-more">+{tags.length - shown.length}</span>}</div>
}

function MissingBadge(){
  return <Badge tone="danger"><AlertTriangle size={12}/>Không còn trên máy</Badge>
}

function Fact({label,children}:{label:string;children:ReactNode}){
  return <div className="media-fact"><dt>{label}</dt><dd>{children}</dd></div>
}
