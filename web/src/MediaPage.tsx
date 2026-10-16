import { useEffect, useState, type FormEvent } from 'react'
import { Button, Input, Modal, Select, Textarea } from './ui/core'
import { Film, Play, Search, Trash2, Upload, Pencil, RefreshCw } from 'lucide-react'
import './media.css'

type Media = { id:number; filename:string; size_bytes:number; duration_seconds:number|null; width:number|null; height:number|null; fps:number|null; video_codec:string; product_id:number|null; presenter_id:number|null; tags:string; created_at:string;local_path:string|null }
type CatalogItem = { id:number; name:string }
const api = ((import.meta.env.VITE_API_URL as string|undefined) || 'http://127.0.0.1:8000').replace(/\/$/, '')
const sizeLabel = (bytes:number) => (bytes / (1024*1024)).toFixed(1) + ' MB'
const durationLabel = (seconds:number|null) => seconds === null ? '—' : `${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2,'0')}`
async function apiRequest<T>(url:string, options?:RequestInit):Promise<T>{
  const response = await fetch(api + '/api' + url, options)
  if (!response.ok) {
    const result = await response.json().catch(()=>({}))
    throw new Error(typeof result.detail === 'string' ? result.detail : 'HTTP ' + response.status)
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>
}
export default function MediaPage(){
  const [items,setItems] = useState<Media[]>([])
  const [products,setProducts] = useState<CatalogItem[]>([])
  const [presenters,setPresenters] = useState<CatalogItem[]>([])
  const [query,setQuery] = useState('')
  const [loading,setLoading] = useState(true)
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const [message,setMessage] = useState('')
  const [file,setFile] = useState<File|null>(null)
  const [folderOpen,setFolderOpen] = useState(false)
  const [folder,setFolder] = useState('')
  const [recursive,setRecursive] = useState(false)
  const [editing,setEditing] = useState<'new'|number|null>(null)
  const [productId,setProductId] = useState('')
  const [presenterId,setPresenterId] = useState('')
  const [tags,setTags] = useState('')
  const [preview,setPreview] = useState<Media|null>(null)
  const load = async () => {
    setError('')
    try{
      const [videos, p, a] = await Promise.all([
        apiRequest<Media[]>('/media?limit=500'),
        apiRequest<CatalogItem[]>('/products?limit=500'),
        apiRequest<CatalogItem[]>('/presenters?limit=500')
      ])
      setItems(videos);setProducts(p);setPresenters(a)
    }catch(e){setError(e instanceof Error ? e.message : 'Không tải được dữ liệu')}
    finally{setLoading(false)}
  }
  useEffect(()=>{void load()},[])
  const showForm = (item?:Media) => {
    setEditing(item ? item.id : 'new')
    setFile(null);setProductId(item?.product_id == null ? '' : String(item.product_id))
    setPresenterId(item?.presenter_id == null ? '' : String(item.presenter_id))
    setTags(item?.tags ?? '');setError('');setMessage('')
  }
  const save = async (event:FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (editing === 'new' && !file) {setError('Chọn file MP4 để upload');return}
    setBusy(true);setError('')
    try{
      if(editing === 'new'){
        const body = new FormData()
        body.append('file',file!)
        if(productId) body.append('product_id',productId)
        if(presenterId) body.append('presenter_id',presenterId)
        body.append('tags',tags)
        await apiRequest<Media>('/media/upload',{method:'POST',body})
      }else{
        await apiRequest<Media>('/media/'+editing,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({product_id:productId?Number(productId):null,presenter_id:presenterId?Number(presenterId):null,tags})})
      }
      setEditing(null);setMessage('Đã lưu video thành công.');await load()
    }catch(e){setError(e instanceof Error ? e.message : 'Có lỗi khi lưu video')}
    finally{setBusy(false)}
  }
  const scan = async (event:FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setBusy(true);setError('');setMessage('')
    try {
      const result=await apiRequest<{found:number;created:number;existing:number}>('/media/local/scan',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folder,recursive})
      })
      setFolderOpen(false)
      setMessage('Đã thêm '+result.created+' video; đã có '+result.existing+' video.')
      await load()
    } catch(e) {setError(e instanceof Error?e.message:String(e))}
    finally {setBusy(false)}
  }
  const remove = async(item:Media) => {
    if(!window.confirm(item.local_path ? `Bỏ video "${item.filename}" khỏi thư viện? File gốc được giữ nguyên.` : `Xóa video "${item.filename}" và file upload?`))return
    setBusy(true);setError('')
    try{await apiRequest<void>('/media/'+item.id,{method:'DELETE'});setMessage('Đã xóa video.');await load()}
    catch(e){setError(e instanceof Error ? e.message : 'Không xóa được video')}
    finally{setBusy(false)}
  }
  const filtered = items.filter(item=>(item.filename+' '+item.tags).toLocaleLowerCase('vi').includes(query.toLocaleLowerCase('vi')))
  return <section className="media-panel panel">
    <div className="media-heading"><div><h3>Thư viện MP4</h3><p>Upload vào máy chạy Backend · {items.length} video</p></div><div className="media-buttons"><Button variant="outline" leftIcon={<RefreshCw size={16}/>} onClick={()=>void load()}>Tải lại</Button><Button variant="outline" onClick={()=>{window.location.hash="/product-videos"}}>Gắn video cho sản phẩm</Button><Button variant="outline" onClick={()=>setFolderOpen(true)}>Link Local Folder</Button><Button leftIcon={<Upload size={16}/>} onClick={()=>showForm()}>Upload MP4</Button></div></div>
    <div className="media-toolbar"><Search size={17}/><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Tìm video theo tên hoặc tags…" aria-label="Tìm video"/><span>{filtered.length} kết quả</span></div>
    {error && <div className="media-feedback media-error" role="alert">{error}</div>}
    {message && <div className="media-feedback" role="status">{message}</div>}
    {loading ? <div className="media-empty">Đang tải video…</div> : filtered.length===0 ? <div className="media-empty"><Film size={36}/><h3>{query?'Không tìm thấy video':'Thư viện chưa có video'}</h3><p>{query?'Thử từ khóa khác.':'Chọn Import MP4 để tải video đầu tiên.'}</p></div> : <div className="media-grid">{filtered.map(item=><article className="media-card" key={item.id}><button className="media-preview" onClick={()=>setPreview(item)} aria-label={'Xem '+item.filename}><Film size={30}/><span className="media-play"><Play size={19}/></span></button><div className="media-details"><strong title={item.filename}>{item.filename}</strong><small>{item.local_path ? "Local · Không copy" : "Uploaded · Backend storage"}</small><small>{durationLabel(item.duration_seconds)} · {sizeLabel(item.size_bytes)} · {item.width&&item.height?`${item.width}×${item.height}`:'Chưa có metadata'}</small><small>Sản phẩm: {products.find(x=>x.id===item.product_id)?.name||'Chưa gắn'}</small><small>Người giới thiệu: {presenters.find(x=>x.id===item.presenter_id)?.name||'Chưa gắn'}</small>{item.tags&&<span className="media-tags">{item.tags}</span>}<div className="media-actions"><Button size="sm" variant="outline" leftIcon={<Pencil size={14}/>} onClick={()=>showForm(item)}>Chỉnh sửa</Button><button disabled={busy} className="media-delete" title="Xóa video" onClick={()=>void remove(item)}><Trash2 size={17}/></button></div></div></article>)}</div>}
    <Modal open={folderOpen} title="Liên kết thư mục video" onClose={()=>{if(!busy)setFolderOpen(false)}} size="md">
      <form className="media-form" onSubmit={event=>void scan(event)}>
        <p>Nhập đường dẫn trên máy chạy Backend/OBS. Chỉ quét MP4, không copy file.</p>
        <Input label="Thư mục Windows" required value={folder} onChange={e=>setFolder(e.target.value)} placeholder="D:\\Sarae\\Videos"/>
        <label><input type="checkbox" checked={recursive} onChange={e=>setRecursive(e.target.checked)}/> Quét cả thư mục con</label>
        {error&&<div className="media-feedback media-error" role="alert">{error}</div>}
        <div className="media-form-actions"><Button type="button" variant="outline" disabled={busy} onClick={()=>setFolderOpen(false)}>Hủy</Button><Button type="submit" loading={busy}>Quét và liên kết</Button></div>
      </form>
    </Modal>
    <Modal open={editing!==null} title={editing==='new'?'Import video MP4':'Thông tin video'} onClose={()=>{if(!busy)setEditing(null)}} size="md">
      <form className="media-form" onSubmit={event=>void save(event)}>
        {editing==='new'&&<div className="media-upload"><input type="file" accept=".mp4,video/mp4" onChange={e=>setFile(e.target.files?.[0]??null)}/><p>Chọn MP4 từ máy tính (tối đa 500 MB/file).</p>{file&&<strong>{file.name} · {sizeLabel(file.size)}</strong>}</div>}
        <Select label="Sản phẩm" placeholder="Chưa liên kết" value={productId} options={[{value:'',label:'Chưa liên kết'},...products.map(p=>({value:String(p.id),label:p.name}))]} onChange={setProductId}/>
        <Select label="Người giới thiệu" value={presenterId} options={[{value:'',label:'Chưa liên kết'},...presenters.map(p=>({value:String(p.id),label:p.name}))]} onChange={setPresenterId}/>
        <Textarea label="Tags / ghi chú phân loại" value={tags} maxLength={1000} rows={3} onChange={e=>setTags(e.target.value)} placeholder="Ví dụ: áo trễ vai, review, quay studio"/>
        {error&&<div className="media-feedback media-error">{error}</div>}
        <div className="media-form-actions"><Button variant="outline" type="button" disabled={busy} onClick={()=>setEditing(null)}>Hủy</Button><Button type="submit" loading={busy}>{editing==='new'?'Upload video':'Lưu thay đổi'}</Button></div>
      </form>
    </Modal>
    <Modal open={preview!==null} title={preview?.filename||'Xem video'} onClose={()=>setPreview(null)} size="lg">
      {preview&&<video className="media-player" src={api+'/api/media/'+preview.id+'/file'} controls autoPlay playsInline/>}
    </Modal>
  </section>
}
