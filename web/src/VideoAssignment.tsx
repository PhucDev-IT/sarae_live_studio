import { useEffect, useMemo, useState, type DragEvent } from 'react'
import { Alert, Badge, Button, Card, ConfirmModal, EmptyState, LoadingState, Modal, SearchInput, SegmentedControl, SkeletonCard, useToast } from './ui/core'
import { ArrowDown, ArrowUp, Check, Film, GripVertical, Plus, Save, Trash2, Undo2, type LucideIcon } from 'lucide-react'
import { assetUrl, durationLabel, errorText, listVideos, sizeLabel, type Media, type OwnerAgent } from './mediaApi'
import { WithAgent } from './agentContext'
import VideoThumb from './VideoThumb'
import './media.css'
import './product-videos.css'

type PickerFilter = 'all'|'unlinked'
export type AssignmentEntity = { id:number; name:string; subtitle?:string; image?:string }

/** Describes one side of a many-to-many video link (products, presenters, …) for {@link VideoAssignment}. */
export type AssignmentConfig = {
  /** Lower-case noun for one entity, e.g. "sản phẩm", "người". */
  noun:string
  listTitle:string
  icon:LucideIcon
  /** Ordered links get drag-and-drop and up/down controls; the saved array order is meaningful. */
  ordered:boolean
  /** Field of Media listing the linked entity ids. */
  linkKey:'product_ids'|'presenter_ids'
  loadEntities:()=>Promise<AssignmentEntity[]>
  /** Links are per Windows Agent: the same product has its own videos on each PC. */
  loadVideos:(agentId:string, entityId:number)=>Promise<string[]>
  saveVideos:(agentId:string, entityId:number, mediaIds:string[])=>Promise<string[]>
  catalogHash:string
  text:{
    summary:(count:number, duration:string|null)=>string
    emptyList:string
    emptyListHint:string
    shared:string
    footnote:string
    save:string
    remove:string
  }
}

const sameSet = (a:string[], b:string[]) => a.length === b.length && a.every(id=>b.includes(id))

/** Editor for the videos linked to each entity of one kind, on the Windows Agent this tab works with. */
export default function VideoAssignment({config}:{config:AssignmentConfig}){
  return <WithAgent purpose={`Gắn video cho ${config.noun}`}>{agent=><Assignment key={agent.id} config={config} agent={agent}/>}</WithAgent>
}

/** Entity list on the left, its videos on the selected Agent on the right. */
function Assignment({config, agent}:{config:AssignmentConfig; agent:OwnerAgent}){
  const {noun, ordered, linkKey, text} = config
  const Icon = config.icon
  const toast = useToast()
  const [entities,setEntities] = useState<AssignmentEntity[]>([])
  const [videos,setVideos] = useState<Media[]>([])
  const [loading,setLoading] = useState(true)
  const [loadError,setLoadError] = useState('')

  const [entityQuery,setEntityQuery] = useState('')
  const [selected,setSelected] = useState<number|null>(null)
  const [saved,setSaved] = useState<string[]>([])
  const [chosen,setChosen] = useState<string[]>([])
  const [listLoading,setListLoading] = useState(false)
  const [busy,setBusy] = useState(false)
  const [pendingEntity,setPendingEntity] = useState<number|null>(null)
  const [dragIndex,setDragIndex] = useState<number|null>(null)

  const [pickerOpen,setPickerOpen] = useState(false)
  const [pickerQuery,setPickerQuery] = useState('')
  const [pickerFilter,setPickerFilter] = useState<PickerFilter>('all')
  const [picked,setPicked] = useState<string[]>([])

  const loadLibrary = async () => {
    setLoadError('')
    try{
      const [e, v] = await Promise.all([config.loadEntities(), listVideos(agent.id)])
      setEntities(e);setVideos(v)
      setSelected(current=>current ?? e[0]?.id ?? null)
    }catch(err){setLoadError(errorText(err, 'Không tải được dữ liệu'))}
    finally{setLoading(false)}
  }
  useEffect(()=>{void loadLibrary()},[])
  useEffect(()=>{
    if (selected == null) return
    let active = true
    setListLoading(true)
    config.loadVideos(agent.id, selected)
      .then(ids=>{if(active){setSaved(ids);setChosen(ids)}})
      .catch(e=>{if(active)toast.push({tone:'danger',title:'Không tải được danh sách video',description:errorText(e, '')})})
      .finally(()=>{if(active)setListLoading(false)})
    return ()=>{active = false}
  },[selected,toast,config,agent.id])

  const videoById = useMemo(()=>new Map(videos.map(v=>[v.id,v])),[videos])
  const entityName = useMemo(()=>new Map(entities.map(e=>[e.id,e.name])),[entities])
  /** Number of videos per entity, derived from each video's link ids. */
  const counts = useMemo(()=>{
    const result = new Map<number,number>()
    for (const video of videos) for (const id of video[linkKey]) result.set(id, (result.get(id) ?? 0) + 1)
    return result
  },[videos,linkKey])
  const entity = entities.find(e=>e.id === selected)
  const dirty = ordered ? chosen.length !== saved.length || chosen.some((id,i)=>id !== saved[i]) : !sameSet(chosen, saved)
  const durationOf = (id:string) => videoById.get(id)?.duration_seconds
  const unknownDurations = chosen.filter(id=>durationOf(id) == null).length
  const totalDuration = unknownDurations === chosen.length ? null : durationLabel(chosen.reduce((sum,id)=>sum + (durationOf(id) ?? 0), 0))
  const names = (ids:number[]) => ids.map(id=>entityName.get(id) ?? '#' + id).join(', ')

  const needle = (value:string) => value.trim().toLocaleLowerCase('vi')
  const visibleEntities = entities.filter(e=>(e.name + ' ' + (e.subtitle ?? '')).toLocaleLowerCase('vi').includes(needle(entityQuery)))
  const pickable = videos.filter(v=>{
    if (chosen.includes(v.id)) return false
    if (pickerFilter === 'unlinked' && v[linkKey].length > 0) return false
    return (v.filename + ' ' + v.tags).toLocaleLowerCase('vi').includes(needle(pickerQuery))
  })

  const selectEntity = (id:number) => {
    if (id === selected) return
    if (dirty) {setPendingEntity(id);return}
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
  const togglePicked = (id:string) => setPicked(prev=>prev.includes(id) ? prev.filter(x=>x !== id) : [...prev, id])
  const addPicked = () => {setChosen(prev=>[...prev, ...picked]);setPickerOpen(false)}

  const save = async () => {
    if (selected == null) return
    setBusy(true)
    try{
      const ids = await config.saveVideos(agent.id, selected, chosen)
      setSaved(ids);setChosen(ids)
      toast.push({tone:'success',title:'Đã lưu',description:`${entity?.name ?? ''} · ${ids.length} video`})
      await loadLibrary()
    }catch(e){toast.push({tone:'danger',title:'Không lưu được',description:errorText(e, '')})}
    finally{setBusy(false)}
  }

  if (loading) return <div className="pv-layout"><SkeletonCard media={false} lines={6}/><SkeletonCard lines={4}/></div>
  if (loadError) return <Card><LoadingState error={loadError} errorTitle="Không tải được dữ liệu" onRetry={()=>{setLoading(true);void loadLibrary()}}>{null}</LoadingState></Card>
  if (!entities.length) return <Card><EmptyState icon={<Icon size={32}/>} title={`Chưa có ${noun}`} description={`Tạo ${noun} trước, sau đó quay lại để gắn video.`} actionLabel={`Đến trang ${config.listTitle}`} onAction={()=>{window.location.hash=config.catalogHash}}/></Card>

  return <div className="pv-layout">
    <Card className="pv-products" padding={false} title={config.listTitle} description={`${entities.length} ${noun} · ${entities.filter(e=>!counts.get(e.id)).length} chưa có video trên ${agent.hostname}`}>
      <div className="pv-products-search"><SearchInput value={entityQuery} onChange={e=>setEntityQuery(e.target.value)} placeholder="Tìm theo tên…" aria-label={`Tìm ${noun}`}/></div>
      <ul className="pv-product-list">{visibleEntities.map(e=>{
        const count = counts.get(e.id) ?? 0
        return <li key={e.id}><button type="button" className={e.id === selected ? 'is-active' : ''} aria-current={e.id === selected} onClick={()=>selectEntity(e.id)}>
          <span className="pv-product-icon">{e.image ? <img src={assetUrl(e.image) ?? undefined} alt=""/> : <Icon size={16}/>}</span>
          <span className="pv-product-text"><strong>{e.name}</strong>{e.subtitle && <small>{e.subtitle}</small>}</span>
          {count ? <Badge tone="primary">{count} video</Badge> : <Badge tone="warning">Chưa có</Badge>}
        </button></li>
      })}{!visibleEntities.length && <li className="pv-muted">Không có {noun} khớp.</li>}</ul>
    </Card>

    <Card
      className="pv-playlist"
      padding={false}
      title={<span className="pv-title"><Icon size={18}/>{entity?.name ?? `Chọn ${noun}`}</span>}
      description={chosen.length ? text.summary(chosen.length, totalDuration) : text.emptyList}
      actions={<div className="pv-actions">
        {dirty && <Button variant="ghost" leftIcon={<Undo2 size={16}/>} disabled={busy} onClick={()=>setChosen(saved)}>Hoàn tác</Button>}
        <Button variant="outline" leftIcon={<Plus size={16}/>} disabled={selected == null} onClick={openPicker}>Thêm video</Button>
        <Button leftIcon={<Save size={16}/>} loading={busy} disabled={!dirty} onClick={()=>void save()}>{text.save}</Button>
      </div>}
    >
      {dirty && <div className="pv-dirty"><Alert tone="warning">Danh sách đã thay đổi nhưng chưa lưu.</Alert></div>}
      {listLoading ? <div className="pv-body"><SkeletonCard media={false} lines={4}/></div>
      : chosen.length === 0 ? <EmptyState icon={<Film size={32}/>} title={`Chưa có video cho ${noun} này`} description={text.emptyListHint} actionLabel="Thêm video" onAction={openPicker}/>
      : <ol className={'pv-order' + (ordered ? '' : ' is-unordered')}>{chosen.map((id,index)=>{
        const video = videoById.get(id)
        const others = (video?.[linkKey] ?? []).filter(eid=>eid !== selected)
        return <li key={id} draggable={ordered} className={dragIndex === index ? 'is-dragging' : ''}
          onDragStart={ordered ? ()=>setDragIndex(index) : undefined} onDragEnd={()=>setDragIndex(null)}
          onDragOver={ordered ? e=>e.preventDefault() : undefined} onDrop={ordered ? e=>onDrop(e,index) : undefined}>
          {ordered && <span className="pv-grip" aria-hidden><GripVertical size={16}/></span>}
          {ordered && <span className="pv-index">{index + 1}</span>}
          <span className="pv-thumb">{video ? <VideoThumb src={video.thumb_url}/> : <Film size={18}/>}</span>
          <span className="pv-info">
            <strong title={video?.filename}>{video?.filename ?? 'Video không còn trên máy này'}</strong>
            <small>{durationLabel(durationOf(id)) ?? '—'}{video && ` · ${sizeLabel(video.size_bytes)}`}{video && !video.available ? ' · không còn file' : ''}</small>
            {others.length > 0 && <small className="pv-shared" title={names(others)}>{text.shared}: {names(others)}</small>}
          </span>
          <span className="pv-row-actions">
            {ordered && <button type="button" className="media-icon-btn" aria-label="Đưa lên" title="Đưa lên" disabled={index === 0} onClick={()=>move(index, index - 1)}><ArrowUp size={16}/></button>}
            {ordered && <button type="button" className="media-icon-btn" aria-label="Đưa xuống" title="Đưa xuống" disabled={index === chosen.length - 1} onClick={()=>move(index, index + 1)}><ArrowDown size={16}/></button>}
            <button type="button" className="media-icon-btn pv-remove" aria-label={text.remove} title={text.remove} onClick={()=>setChosen(chosen.filter(x=>x !== id))}><Trash2 size={16}/></button>
          </span>
        </li>
      })}</ol>}
      <p className="pv-footnote">{text.footnote}</p>
    </Card>

    <Modal open={pickerOpen} size="lg" title={`Thêm video cho “${entity?.name ?? ''}”`} onClose={()=>setPickerOpen(false)} footer={<>
      <span className="pv-picked-count">{picked.length ? `Đã chọn ${picked.length} video${ordered ? ' · thêm vào cuối danh sách' : ''}` : 'Chọn một hoặc nhiều video'}</span>
      <Button variant="ghost" onClick={()=>setPickerOpen(false)}>Hủy</Button>
      <Button leftIcon={<Plus size={16}/>} disabled={!picked.length} onClick={addPicked}>Thêm {picked.length || ''} video</Button>
    </>}>
      <div className="pv-picker-tools">
        <SearchInput value={pickerQuery} onChange={e=>setPickerQuery(e.target.value)} placeholder="Tìm theo tên file hoặc tags…" aria-label="Tìm video"/>
        <SegmentedControl<PickerFilter> value={pickerFilter} onChange={setPickerFilter} items={[{value:'all',label:'Tất cả'},{value:'unlinked',label:`Chưa gắn ${noun}`}]}/>
      </div>
      {pickable.length === 0
        ? <EmptyState icon={<Film size={28}/>} title="Không còn video phù hợp" description={`Các video khớp bộ lọc đều đã có trong danh sách của ${noun} này.`}/>
        : <div className="pv-picker-grid">{pickable.map(video=>{
          const isPicked = picked.includes(video.id)
          const duration = durationLabel(durationOf(video.id))
          return <button type="button" key={video.id} className={'pv-pick' + (isPicked ? ' is-picked' : '')} aria-pressed={isPicked} onClick={()=>togglePicked(video.id)}>
            <span className="pv-pick-thumb"><VideoThumb src={video.thumb_url}/>
              {duration && <span className="media-chip media-chip--duration">{duration}</span>}
              <span className="pv-pick-check">{isPicked && <Check size={14}/>}</span>
            </span>
            <strong title={video.filename}>{video.filename}</strong>
            <small>{video[linkKey].length ? `${text.shared}: ${names(video[linkKey])}` : `Chưa gắn ${noun}`}</small>
          </button>
        })}</div>}
    </Modal>

    <ConfirmModal
      open={pendingEntity != null}
      title="Bỏ thay đổi chưa lưu?"
      description={`Danh sách video của “${entity?.name ?? ''}” có thay đổi chưa lưu. Chuyển sang ${noun} khác sẽ bỏ các thay đổi này.`}
      confirmLabel="Bỏ thay đổi"
      onClose={()=>setPendingEntity(null)}
      onConfirm={()=>{setSelected(pendingEntity);setPendingEntity(null)}}
    />
  </div>
}
