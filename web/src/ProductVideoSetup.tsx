import { useEffect, useMemo, useState, type DragEvent } from 'react'
import { Alert, Badge, Button, Card, ConfirmModal, EmptyState, LoadingState, Modal, SearchInput, SegmentedControl, SkeletonCard, useToast } from './ui/core'
import { ArrowDown, ArrowUp, Check, Film, GripVertical, ListVideo, Package, Plus, Save, Trash2, Undo2 } from 'lucide-react'
import { apiRequest, durationLabel, errorText, getProductVideos, putProductVideos, sizeLabel, type Media, type Product } from './mediaApi'
import VideoThumb, { type ClientMeta } from './VideoThumb'
import './media.css'
import './product-videos.css'

type PickerFilter = 'all'|'unlinked'

/**
 * Per-product LIVE playlist editor. Product ↔ video is many-to-many: a video can belong to several
 * products' playlists, and each product keeps its own playback order (`media_ids` order).
 */
export default function ProductVideoSetup(){
  const toast = useToast()
  const [products,setProducts] = useState<Product[]>([])
  const [videos,setVideos] = useState<Media[]>([])
  const [loading,setLoading] = useState(true)
  const [loadError,setLoadError] = useState('')
  const [meta,setMeta] = useState<Record<number,ClientMeta>>({})

  const [productQuery,setProductQuery] = useState('')
  const [selected,setSelected] = useState<number|null>(null)
  const [saved,setSaved] = useState<number[]>([])
  const [chosen,setChosen] = useState<number[]>([])
  const [listLoading,setListLoading] = useState(false)
  const [busy,setBusy] = useState(false)
  const [pendingProduct,setPendingProduct] = useState<number|null>(null)
  const [dragIndex,setDragIndex] = useState<number|null>(null)

  const [pickerOpen,setPickerOpen] = useState(false)
  const [pickerQuery,setPickerQuery] = useState('')
  const [pickerFilter,setPickerFilter] = useState<PickerFilter>('all')
  const [picked,setPicked] = useState<number[]>([])

  const loadLibrary = async () => {
    setLoadError('')
    try{
      const [p, v] = await Promise.all([apiRequest<Product[]>('/products?limit=500'), apiRequest<Media[]>('/media?limit=500')])
      setProducts(p);setVideos(v)
      setSelected(current=>current ?? p[0]?.id ?? null)
    }catch(e){setLoadError(errorText(e, 'Không tải được dữ liệu'))}
    finally{setLoading(false)}
  }
  useEffect(()=>{void loadLibrary()},[])
  useEffect(()=>{
    if (selected == null) return
    let active = true
    setListLoading(true)
    getProductVideos(selected)
      .then(ids=>{if(active){setSaved(ids);setChosen(ids)}})
      .catch(e=>{if(active)toast.push({tone:'danger',title:'Không tải được danh sách phát',description:errorText(e, '')})})
      .finally(()=>{if(active)setListLoading(false)})
    return ()=>{active = false}
  },[selected,toast])

  const videoById = useMemo(()=>new Map(videos.map(v=>[v.id,v])),[videos])
  const productName = useMemo(()=>new Map(products.map(p=>[p.id,p.name])),[products])
  /** Number of videos per product, derived from each video's product_ids. */
  const counts = useMemo(()=>{
    const result = new Map<number,number>()
    for (const video of videos) for (const id of video.product_ids) result.set(id, (result.get(id) ?? 0) + 1)
    return result
  },[videos])
  const product = products.find(p=>p.id === selected)
  const dirty = chosen.length !== saved.length || chosen.some((id,i)=>id !== saved[i])
  const durationOf = (id:number) => videoById.get(id)?.duration_seconds ?? meta[id]?.duration
  const totalSeconds = chosen.reduce((sum,id)=>sum + (durationOf(id) ?? 0), 0)
  const unknownDurations = chosen.filter(id=>durationOf(id) == null).length

  const visibleProducts = products.filter(p=>(p.name + ' ' + (p.sku ?? '')).toLocaleLowerCase('vi').includes(productQuery.trim().toLocaleLowerCase('vi')))
  const pickable = videos.filter(v=>{
    if (chosen.includes(v.id)) return false
    if (pickerFilter === 'unlinked' && v.product_ids.length > 0) return false
    return (v.filename + ' ' + v.tags).toLocaleLowerCase('vi').includes(pickerQuery.trim().toLocaleLowerCase('vi'))
  })

  const reportMeta = (id:number, next:ClientMeta) => setMeta(prev=>prev[id]?.duration === next.duration && prev[id]?.missing === next.missing ? prev : {...prev, [id]:{...prev[id], ...next}})
  const selectProduct = (id:number) => {
    if (id === selected) return
    if (dirty) {setPendingProduct(id);return}
    setSelected(id)
  }
  const move = (from:number, to:number) => {
    if (to < 0 || to >= chosen.length || from === to) return
    const next = [...chosen]
    const [item] = next.splice(from, 1)
    next.splice(to, 0, item)
    setChosen(next)
  }
  const onDrop = (event:DragEvent, index:number) => {
    event.preventDefault()
    if (dragIndex != null) move(dragIndex, index)
    setDragIndex(null)
  }
  const openPicker = () => {setPicked([]);setPickerQuery('');setPickerOpen(true)}
  const togglePicked = (id:number) => setPicked(prev=>prev.includes(id) ? prev.filter(x=>x !== id) : [...prev, id])
  const addPicked = () => {setChosen(prev=>[...prev, ...picked]);setPickerOpen(false)}

  const save = async () => {
    if (selected == null) return
    setBusy(true)
    try{
      const result = await putProductVideos(selected, chosen)
      setSaved(result.media_ids);setChosen(result.media_ids)
      toast.push({tone:'success',title:'Đã lưu danh sách phát',description:`${product?.name ?? ''} · ${result.media_ids.length} video`})
      await loadLibrary()
    }catch(e){toast.push({tone:'danger',title:'Không lưu được',description:errorText(e, '')})}
    finally{setBusy(false)}
  }

  if (loading) return <div className="pv-layout"><SkeletonCard media={false} lines={6}/><SkeletonCard lines={4}/></div>
  if (loadError) return <Card><LoadingState error={loadError} errorTitle="Không tải được dữ liệu" onRetry={()=>{setLoading(true);void loadLibrary()}}>{null}</LoadingState></Card>
  if (!products.length) return <Card><EmptyState icon={<Package size={32}/>} title="Chưa có sản phẩm" description="Tạo sản phẩm trước, sau đó quay lại để chọn video phát LIVE." actionLabel="Đến trang Sản phẩm" onAction={()=>{window.location.hash='/products'}}/></Card>

  return <div className="pv-layout">
    <Card className="pv-products" padding={false} title="Sản phẩm" description={`${products.length} sản phẩm · ${products.filter(p=>!counts.get(p.id)).length} chưa có video`}>
      <div className="pv-products-search"><SearchInput value={productQuery} onChange={e=>setProductQuery(e.target.value)} placeholder="Tìm tên hoặc SKU…" aria-label="Tìm sản phẩm"/></div>
      <ul className="pv-product-list">{visibleProducts.map(p=>{
        const count = counts.get(p.id) ?? 0
        return <li key={p.id}><button type="button" className={p.id === selected ? 'is-active' : ''} aria-current={p.id === selected} onClick={()=>selectProduct(p.id)}>
          <span className="pv-product-icon">{p.image_url ? <img src={p.image_url} alt=""/> : <Package size={16}/>}</span>
          <span className="pv-product-text"><strong>{p.name}</strong>{p.sku && <small>{p.sku}</small>}</span>
          {count ? <Badge tone="primary">{count} video</Badge> : <Badge tone="warning">Chưa có</Badge>}
        </button></li>
      })}{!visibleProducts.length && <li className="pv-muted">Không có sản phẩm khớp.</li>}</ul>
    </Card>

    <Card
      className="pv-playlist"
      padding={false}
      title={<span className="pv-title"><ListVideo size={18}/>{product?.name ?? 'Chọn sản phẩm'}</span>}
      description={chosen.length
        ? `${chosen.length} video · ${unknownDurations === chosen.length ? 'chưa rõ thời lượng' : `${durationLabel(totalSeconds)} mỗi vòng${unknownDurations ? ` (chưa rõ ${unknownDurations} video)` : ''}`} · phát lặp theo thứ tự`
        : 'Chưa có video — sản phẩm chưa thể bắt đầu LIVE'}
      actions={<div className="pv-actions">
        {dirty && <Button variant="ghost" leftIcon={<Undo2 size={16}/>} disabled={busy} onClick={()=>setChosen(saved)}>Hoàn tác</Button>}
        <Button variant="outline" leftIcon={<Plus size={16}/>} disabled={selected == null} onClick={openPicker}>Thêm video</Button>
        <Button leftIcon={<Save size={16}/>} loading={busy} disabled={!dirty} onClick={()=>void save()}>Lưu thứ tự</Button>
      </div>}
    >
      {dirty && <div className="pv-dirty"><Alert tone="warning">Danh sách đã thay đổi nhưng chưa lưu.</Alert></div>}
      {listLoading ? <div className="pv-body"><SkeletonCard media={false} lines={4}/></div>
      : chosen.length === 0 ? <EmptyState icon={<Film size={32}/>} title="Chưa chọn video cho sản phẩm này" description="Chọn một hoặc nhiều video từ thư viện. Một video có thể dùng cho nhiều sản phẩm." actionLabel="Thêm video" onAction={openPicker}/>
      : <ol className="pv-order">{chosen.map((id,index)=>{
        const video = videoById.get(id)
        const others = (video?.product_ids ?? []).filter(pid=>pid !== selected)
        return <li key={id} draggable className={dragIndex === index ? 'is-dragging' : ''}
          onDragStart={()=>setDragIndex(index)} onDragEnd={()=>setDragIndex(null)} onDragOver={e=>e.preventDefault()} onDrop={e=>onDrop(e,index)}>
          <span className="pv-grip" aria-hidden><GripVertical size={16}/></span>
          <span className="pv-index">{index + 1}</span>
          <span className="pv-thumb">{video ? <VideoThumb id={id} onMeta={next=>reportMeta(id,next)}/> : <Film size={18}/>}</span>
          <span className="pv-info">
            <strong title={video?.filename}>{video?.filename ?? `Video #${id} không còn trong thư viện`}</strong>
            <small>{durationLabel(durationOf(id)) ?? '—'}{video && ` · ${sizeLabel(video.size_bytes)}`}{video?.local_path ? ' · Local' : video ? ' · Upload' : ''}</small>
            {others.length > 0 && <small className="pv-shared" title={others.map(pid=>productName.get(pid) ?? '#' + pid).join(', ')}>Cũng dùng cho: {others.map(pid=>productName.get(pid) ?? '#' + pid).join(', ')}</small>}
          </span>
          <span className="pv-row-actions">
            <button type="button" className="media-icon-btn" aria-label="Đưa lên" title="Đưa lên" disabled={index === 0} onClick={()=>move(index, index - 1)}><ArrowUp size={16}/></button>
            <button type="button" className="media-icon-btn" aria-label="Đưa xuống" title="Đưa xuống" disabled={index === chosen.length - 1} onClick={()=>move(index, index + 1)}><ArrowDown size={16}/></button>
            <button type="button" className="media-icon-btn pv-remove" aria-label="Gỡ khỏi sản phẩm" title="Gỡ khỏi sản phẩm (không xóa video)" onClick={()=>setChosen(chosen.filter(x=>x !== id))}><Trash2 size={16}/></button>
          </span>
        </li>
      })}</ol>}
      <p className="pv-footnote">Kéo thả hoặc dùng mũi tên để đổi thứ tự. Gỡ video chỉ bỏ liên kết với sản phẩm này, không xóa file và không ảnh hưởng sản phẩm khác.</p>
    </Card>

    <Modal open={pickerOpen} size="lg" title={`Thêm video cho “${product?.name ?? ''}”`} onClose={()=>setPickerOpen(false)} footer={<>
      <span className="pv-picked-count">{picked.length ? `Đã chọn ${picked.length} video · thêm vào cuối danh sách` : 'Chọn một hoặc nhiều video'}</span>
      <Button variant="ghost" onClick={()=>setPickerOpen(false)}>Hủy</Button>
      <Button leftIcon={<Plus size={16}/>} disabled={!picked.length} onClick={addPicked}>Thêm {picked.length || ''} video</Button>
    </>}>
      <div className="pv-picker-tools">
        <SearchInput value={pickerQuery} onChange={e=>setPickerQuery(e.target.value)} placeholder="Tìm theo tên file hoặc tags…" aria-label="Tìm video"/>
        <SegmentedControl<PickerFilter> value={pickerFilter} onChange={setPickerFilter} items={[{value:'all',label:'Tất cả'},{value:'unlinked',label:'Chưa gắn sản phẩm'}]}/>
      </div>
      {pickable.length === 0
        ? <EmptyState icon={<Film size={28}/>} title="Không còn video phù hợp" description="Các video khớp bộ lọc đều đã có trong danh sách của sản phẩm này."/>
        : <div className="pv-picker-grid">{pickable.map(video=>{
          const isPicked = picked.includes(video.id)
          return <button type="button" key={video.id} className={'pv-pick' + (isPicked ? ' is-picked' : '')} aria-pressed={isPicked} onClick={()=>togglePicked(video.id)}>
            <span className="pv-pick-thumb"><VideoThumb id={video.id} onMeta={next=>reportMeta(video.id,next)}/>
              {durationLabel(durationOf(video.id)) && <span className="media-chip media-chip--duration">{durationLabel(durationOf(video.id))}</span>}
              <span className="pv-pick-check">{isPicked && <Check size={14}/>}</span>
            </span>
            <strong title={video.filename}>{video.filename}</strong>
            <small>{video.product_ids.length ? `Đang dùng: ${video.product_ids.map(pid=>productName.get(pid) ?? '#' + pid).join(', ')}` : 'Chưa gắn sản phẩm'}</small>
          </button>
        })}</div>}
    </Modal>

    <ConfirmModal
      open={pendingProduct != null}
      title="Bỏ thay đổi chưa lưu?"
      description={`Danh sách phát của “${product?.name ?? ''}” có thay đổi chưa lưu. Chuyển sang sản phẩm khác sẽ bỏ các thay đổi này.`}
      confirmLabel="Bỏ thay đổi"
      onClose={()=>setPendingProduct(null)}
      onConfirm={()=>{setSelected(pendingProduct);setPendingProduct(null)}}
    />
  </div>
}
