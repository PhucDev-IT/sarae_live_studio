import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Badge, Button, ConfirmModal, EmptyState, Input, SearchInput, SegmentedControl, Select, Sheet, Switch, Table, Textarea, useToast, type TableColumn } from './ui/core'
import { CircleAlert, Edit3, Film, Plus, Trash2, UserCheck, Users } from 'lucide-react'
import { apiRequest, errorText } from './mediaApi'
import { IconButton, ImageField, Stat, Thumb } from './CatalogParts'

type Presenter = { id:number; name:string; description:string; avatar_url:string; status:'ACTIVE'|'INACTIVE'; created_at:string }
/** Row shape for the core Table, which needs an index signature. */
type Row = Presenter & Record<string, unknown>
type Form = { name:string; description:string; avatar_url:string; active:boolean }
type StatusFilter = 'ALL' | 'ACTIVE' | 'INACTIVE'
type VideoFilter = 'ALL' | 'TAGGED' | 'UNTAGGED'

const EMPTY:Form = {name:'', description:'', avatar_url:'', active:true}
const json = (data:unknown, method:string):RequestInit => ({method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)})
const toForm = (p:Presenter):Form => ({name:p.name, description:p.description, avatar_url:p.avatar_url, active:p.status === 'ACTIVE'})
const toBody = (f:Form) => ({name:f.name.trim(), description:f.description, avatar_url:f.avatar_url.trim(), status:f.active ? 'ACTIVE' : 'INACTIVE'})

/** People who appear in the videos (models / hosts), with photo and how many videos they are tagged in. */
export default function PresentersPage(){
  const toast = useToast()
  const [people,setPeople] = useState<Presenter[]>([])
  const [videoCounts,setVideoCounts] = useState<Record<number,number>>({})
  const [loaded,setLoaded] = useState(false)
  const [query,setQuery] = useState('')
  const [statusFilter,setStatusFilter] = useState<StatusFilter>('ALL')
  const [videoFilter,setVideoFilter] = useState<VideoFilter>('ALL')
  const [editing,setEditing] = useState<number|'new'|null>(null)
  const [form,setForm] = useState<Form>(EMPTY)
  const [nameError,setNameError] = useState('')
  const [busy,setBusy] = useState(false)
  const [toDelete,setToDelete] = useState<Presenter|null>(null)

  const load = useCallback(async ()=>{
    const [p, counts] = await Promise.all([
      apiRequest<Presenter[]>('/presenters?limit=500'),
      apiRequest<{presenter_id:number;video_count:number}[]>('/presenter-videos'),
    ])
    setPeople(p);setVideoCounts(Object.fromEntries(counts.map(c=>[c.presenter_id,c.video_count])));setLoaded(true)
  },[])
  useEffect(()=>{void load().catch(e=>{setLoaded(true);toast.push({tone:'danger',title:'Không tải được người giới thiệu',description:errorText(e, '')})})},[load,toast])

  const q = query.trim().toLocaleLowerCase('vi')
  const visible = people.filter(p=>
    (statusFilter === 'ALL' || p.status === statusFilter)
    && (videoFilter === 'ALL' || (videoFilter === 'TAGGED') === !!videoCounts[p.id])
    && (!q || `${p.name} ${p.description}`.toLocaleLowerCase('vi').includes(q)))
  const stats = useMemo(()=>({
    total:people.length,
    active:people.filter(p=>p.status === 'ACTIVE').length,
    tagged:people.filter(p=>videoCounts[p.id]).length,
    videos:Object.values(videoCounts).reduce((sum,n)=>sum + n, 0),
  }),[people,videoCounts])

  const openNew = () => {setForm(EMPTY);setNameError('');setEditing('new')}
  const openEdit = (p:Presenter) => {setForm(toForm(p));setNameError('');setEditing(p.id)}
  const close = () => {if (!busy) setEditing(null)}

  const save = async (andNew:boolean) => {
    if (!form.name.trim()) {setNameError('Nhập tên người giới thiệu');return}
    setBusy(true);setNameError('')
    try{
      const isNew = editing === 'new'
      const saved = await apiRequest<Presenter>(isNew ? '/presenters' : `/presenters/${editing}`, json(toBody(form), isNew ? 'POST' : 'PUT'))
      setPeople(list=>[saved, ...list.filter(p=>p.id !== saved.id)].sort((a,b)=>b.id - a.id))
      toast.push({tone:'success', title:isNew ? `Đã thêm “${saved.name}”` : 'Đã lưu thay đổi'})
      if (andNew) setForm(f=>({...EMPTY, active:f.active}))
      else setEditing(null)
    }catch(e){toast.push({tone:'danger', title:'Không lưu được', description:errorText(e, '')})}
    finally{setBusy(false)}
  }
  const toggleStatus = async (p:Presenter) => {
    const next:Presenter = {...p, status:p.status === 'ACTIVE' ? 'INACTIVE' : 'ACTIVE'}
    setPeople(list=>list.map(x=>x.id === p.id ? next : x))
    try{await apiRequest(`/presenters/${p.id}`, json(toBody(toForm(next)), 'PUT'))}
    catch(e){setPeople(list=>list.map(x=>x.id === p.id ? p : x));toast.push({tone:'danger', title:'Không đổi được trạng thái', description:errorText(e, '')})}
  }
  const remove = async () => {
    const p = toDelete
    if (!p) return
    setBusy(true)
    try{await apiRequest(`/presenters/${p.id}`, {method:'DELETE'});setToDelete(null);toast.push({tone:'success', title:`Đã xóa “${p.name}”`});await load()}
    catch(e){toast.push({tone:'danger', title:'Không xóa được', description:errorText(e, '')})}
    finally{setBusy(false)}
  }

  const columns:TableColumn<Row>[] = [
    {key:'name', header:'Người giới thiệu', render:p=><button type="button" className={'pp-row-main' + (p.status === 'INACTIVE' ? ' is-hidden' : '')} onClick={()=>openEdit(p)}>
      <span className="pp-row-thumb pp-avatar"><Thumb src={p.avatar_url} name={p.name} fallback={<Users size={18}/>}/></span>
      <span className="pp-row-text"><strong>{p.name}</strong><small>{p.description || 'Chưa có mô tả'}</small></span>
    </button>},
    {key:'videos', header:'Xuất hiện trong', render:p=>videoCounts[p.id]
      ? <a className="pp-badge-link" href="#/presenter-videos" title="Xem / gắn video"><Badge tone="success"><Film size={12}/>{videoCounts[p.id]} video</Badge></a>
      : <a className="pp-novideo" href="#/presenter-videos" title="Gắn video cho người này"><CircleAlert size={13}/>Chưa gắn video</a>},
    {key:'status', header:'Đang hoạt động', render:p=><Switch aria-label={`Đang hoạt động ${p.name}`} checked={p.status === 'ACTIVE'} onChange={()=>void toggleStatus(p)}/>},
    {key:'actions', header:'Thao tác', align:'right', render:p=><div className="pp-row-actions">
      <IconButton title="Sửa" onClick={()=>openEdit(p)}><Edit3 size={15}/></IconButton>
      <IconButton title="Xóa" danger onClick={()=>setToDelete(p)}><Trash2 size={15}/></IconButton>
    </div>},
  ]

  const editingPerson = typeof editing === 'number' ? people.find(p=>p.id === editing) : undefined
  const filtered = !!q || statusFilter !== 'ALL' || videoFilter !== 'ALL'

  return <div className="pp-page">
    <section className="pp-stats" aria-label="Tổng quan người giới thiệu">
      <Stat icon={<Users size={18}/>} label="Người giới thiệu" value={stats.total} tone="violet"/>
      <Stat icon={<UserCheck size={18}/>} label="Đang hoạt động" value={stats.active} hint={`${stats.total - stats.active} tạm ẩn`} tone="blue"/>
      <Stat icon={<Film size={18}/>} label="Đã gắn video" value={stats.tagged} hint={`${stats.videos} lượt xuất hiện`} tone="mint"/>
      <Stat icon={<CircleAlert size={18}/>} label="Chưa gắn video" value={stats.total - stats.tagged} hint={stats.total - stats.tagged ? 'bấm để lọc' : 'không có'}
        tone={stats.total - stats.tagged ? 'peach' : 'muted'} onClick={stats.total - stats.tagged ? ()=>setVideoFilter('UNTAGGED') : undefined} actionTitle="Lọc người chưa gắn video"/>
    </section>

    <section className="pp-panel">
      <div className="pp-toolbar">
        <div className="pp-search"><SearchInput value={query} onChange={e=>setQuery(e.target.value)} placeholder="Tìm theo tên hoặc mô tả…" aria-label="Tìm người giới thiệu"/></div>
        <SegmentedControl<StatusFilter> value={statusFilter} onChange={setStatusFilter} items={[{value:'ALL', label:'Tất cả'}, {value:'ACTIVE', label:'Đang hoạt động'}, {value:'INACTIVE', label:'Tạm ẩn'}]}/>
        <div className="pp-select"><Select value={videoFilter} onChange={v=>setVideoFilter(v as VideoFilter)} options={[{value:'ALL', label:'Mọi trạng thái video'}, {value:'TAGGED', label:'Đã gắn video'}, {value:'UNTAGGED', label:'Chưa gắn video'}]}/></div>
        <Button leftIcon={<Plus size={17}/>} onClick={openNew}>Thêm người giới thiệu</Button>
      </div>
      <div className="pp-count">{loaded ? `${visible.length} / ${people.length} người` : 'Đang tải…'}
        {filtered && <button type="button" className="pp-clear" onClick={()=>{setQuery('');setStatusFilter('ALL');setVideoFilter('ALL')}}>Xóa bộ lọc</button>}
      </div>
      {loaded && !visible.length ? <div className="pp-empty"><EmptyState icon={<Users size={30}/>}
        title={people.length ? 'Không có ai phù hợp' : 'Chưa có người giới thiệu'}
        description={people.length ? 'Thử từ khóa hoặc bộ lọc khác.' : 'Thêm người mẫu / người dẫn xuất hiện trong video để gắn họ vào từng video.'}
        actionLabel={people.length ? undefined : 'Thêm người đầu tiên'} onAction={openNew}/></div>
      : <div className="pp-table"><Table<Row> data={visible as Row[]} columns={columns}/></div>}
    </section>

    <Sheet open={editing !== null} onClose={close} width={520} title={editing === 'new' ? 'Thêm người giới thiệu' : 'Chỉnh sửa người giới thiệu'} footer={<div className="pp-sheet-footer">
      <Button variant="ghost" disabled={busy} onClick={close}>Hủy</Button>
      {editing === 'new' && <Button variant="outline" disabled={busy} onClick={()=>void save(true)}>Lưu & thêm tiếp</Button>}
      <Button type="submit" form="pr-form" loading={busy}>{editing === 'new' ? 'Thêm người giới thiệu' : 'Lưu thay đổi'}</Button>
    </div>}>
      {editing !== null && <form id="pr-form" className="pp-form" onSubmit={(e:FormEvent)=>{e.preventDefault();void save(false)}}>
        <ImageField round value={form.avatar_url} name={form.name} uploadPath="/presenters/images" label="Chọn ảnh đại diện" fallback={<Users size={28}/>}
          onChange={url=>setForm(f=>({...f, avatar_url:url}))}/>
        <Input label="Tên *" autoFocus maxLength={200} value={form.name} error={nameError || undefined} placeholder="VD: Linh (người mẫu)"
          onChange={e=>setForm(f=>({...f, name:e.target.value}))}/>
        <Textarea label="Mô tả" rows={5} maxLength={10000} value={form.description}
          placeholder="Vai trò, số đo / size mặc, phong cách… giúp chọn video phù hợp khi LIVE."
          hint={`${form.description.length.toLocaleString('vi-VN')}/10.000 ký tự`} onChange={e=>setForm(f=>({...f, description:e.target.value}))}/>
        <div className="pp-switch-row">
          <Switch label="Đang hoạt động" checked={form.active} onChange={e=>setForm(f=>({...f, active:e.target.checked}))}/>
          <small>{form.active ? 'Hiện khi gắn người vào video.' : 'Tạm ẩn: giữ lịch sử video nhưng không dùng cho video mới.'}</small>
        </div>
        {editingPerson && <div className="pp-meta">
          {videoCounts[editingPerson.id] ? <Badge tone="success"><Film size={12}/>{videoCounts[editingPerson.id]} video</Badge> : <span>Chưa gắn video</span>}
          <a href="#/presenter-videos">Gắn video cho người này →</a>
          <span>Tạo {new Date(editingPerson.created_at).toLocaleDateString('vi-VN')}</span>
        </div>}
      </form>}
    </Sheet>

    <ConfirmModal open={!!toDelete} loading={busy} onClose={()=>setToDelete(null)} onConfirm={()=>void remove()} confirmLabel="Xóa"
      title={toDelete ? `Xóa “${toDelete.name}”?` : 'Xóa người giới thiệu?'}
      description={`Không thể hoàn tác.${toDelete && videoCounts[toDelete.id] ? ` Người này sẽ bị gỡ khỏi ${videoCounts[toDelete.id]} video (video vẫn giữ nguyên).` : ''} Nếu chỉ tạm ngưng, hãy tắt “Đang hoạt động”.`}/>
  </div>
}
