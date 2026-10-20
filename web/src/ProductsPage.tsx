import {useCallback,useEffect,useMemo,useState} from 'react'
import {Button,EmptyState,SearchInput,Select,Sheet,Table,Textarea,useToast,type TableColumn} from './ui/core'
import {CircleAlert,Eye,Package,Radio,RefreshCw} from 'lucide-react'
import {apiRequest,errorText,onAgent} from './mediaApi'
import {useAgent} from './agentContext'
import {Stat,Thumb} from './CatalogParts'

type Product={id:number;shop_id:number|null;tiktok_product_id:string|null;name:string;sku:string;description:string;price:number|null;origin_price:number|null;stock_num:number|null;image_url:string;status:'ACTIVE'|'INACTIVE';created_at:string}
type Shop={id:number;seller_id:string;display_name:string}
type TikTokSnapshot={scanned_at:string|null;summary:{tiktok:number;matched:number;unmatched_tiktok:number}}
type Row=Product&Record<string,unknown>
const vnd=new Intl.NumberFormat('vi-VN',{style:'currency',currency:'VND',maximumFractionDigits:0})

/** TikTok owns product identity and commercial fields. Sarae only edits AI descriptions and video links. */
export default function ProductsPage(){
  const toast=useToast()
  const {agent}=useAgent()
  const agentId=agent?.platform === 'ANDROID' ? undefined : agent?.id
  const [products,setProducts]=useState<Product[]>([])
  const [shops,setShops]=useState<Shop[]>([])
  const [shopFilter,setShopFilter]=useState('all')
  const [videoCounts,setVideoCounts]=useState<Record<number,number>>({})
  const [snapshot,setSnapshot]=useState<TikTokSnapshot|null>(null)
  const [loaded,setLoaded]=useState(false)
  const [query,setQuery]=useState('')
  const [editing,setEditing]=useState<Product|null>(null)
  const [description,setDescription]=useState('')
  const [busy,setBusy]=useState(false)

  const load=useCallback(async()=>{
    const [items,available,shopList,currentSnapshot]=await Promise.all([
      apiRequest<Product[]>('/products?limit=500'),
      agentId?apiRequest<Array<{id:number;video_count:number}>>(onAgent('/live/products',agentId)):Promise.resolve([]),
      apiRequest<Shop[]>('/tiktok-catalog/shops'),
      agentId?apiRequest<TikTokSnapshot>('/tiktok-products/'+encodeURIComponent(agentId)).catch(()=>null):Promise.resolve(null),
    ])
    setProducts(items)
    setVideoCounts(Object.fromEntries(available.map(v=>[v.id,v.video_count])))
    setShops(shopList)
    setSnapshot(currentSnapshot)
    setLoaded(true)
  },[agentId])
  useEffect(()=>{void load().catch(e=>{setLoaded(true);toast.push({tone:'danger',title:'Không tải được danh mục',description:errorText(e,'')})})},[load,toast])
  const current=useMemo(()=>products.filter(p=>
    (shopFilter==='all'||String(p.shop_id)===shopFilter)
    &&(!query.trim()||(p.name+' '+p.sku).toLocaleLowerCase('vi').includes(query.trim().toLocaleLowerCase('vi')))),[products,shopFilter,query])
  const ready=products.filter(p=>p.status==='ACTIVE'&&!!videoCounts[p.id]).length
  const missing=products.filter(p=>p.status==='ACTIVE'&&!videoCounts[p.id]).length
  const open=(p:Product)=>{setEditing(p);setDescription(p.description||'')}
  const save=async()=>{
    if(!editing)return
    setBusy(true)
    try{
      await apiRequest('/tiktok-catalog/products/'+editing.id+'/description',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({description})})
      setProducts(rows=>rows.map(p=>p.id===editing.id?{...p,description}:p))
      setEditing(null)
      toast.push({tone:'success',title:'Đã lưu mô tả cho AI'})
    }catch(e){toast.push({tone:'danger',title:'Không lưu được mô tả',description:errorText(e,'')})}
    finally{setBusy(false)}
  }
  const columns:TableColumn<Row>[]=[
    {key:'name',header:'Sản phẩm TikTok',render:p=><button type="button" className="pp-row-main" onClick={()=>open(p)}>
      <span className="pp-row-thumb"><Thumb src={p.image_url} name={p.name} fallback={<Package size={18}/>} /></span>
      <span className="pp-row-text"><strong>{p.name}</strong><small>{p.tiktok_product_id||p.sku}{p.description?' · Đã có mô tả AI':''}</small></span>
    </button>},
    {key:'price',header:'Giá bán',align:'right',render:p=><span className="pp-num">{p.price==null?'—':vnd.format(p.price)}</span>},
    {key:'stock',header:'Tồn kho',render:p=><span>{p.stock_num??'—'}</span>},
    {key:'video',header:'Video LIVE',render:p=><span>{videoCounts[p.id]||0} video</span>},
    {key:'shop',header:'TikTok Shop',render:p=><span>{shops.find(sh=>sh.id===p.shop_id)?.display_name||shops.find(sh=>sh.id===p.shop_id)?.seller_id||'Chưa liên kết'}</span>},
  ]
  return <div className="pp-page">
    <section className="pp-stats" aria-label="Tổng quan sản phẩm">
      <Stat icon={<Package size={18}/>} label="Tổng sản phẩm" value={products.length} tone="violet"/>
      <Stat icon={<Eye size={18}/>} label="Đang bán" value={products.filter(p=>p.status==='ACTIVE').length} tone="blue"/>
      <Stat icon={<Radio size={18}/>} label="Sẵn sàng LIVE" value={ready} hint="Đang bán và có video trên Agent" tone="mint"/>
      <Stat icon={<CircleAlert size={18}/>} label="Thiếu video" value={missing} tone={missing?'peach':'muted'}/>
    </section>
    <section className="pp-panel" style={{padding:'14px 18px',marginBottom:14}}>
      <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',flexWrap:'wrap',gap:12}}>
        <div><strong>Đồng bộ sản phẩm từ TikTok Shop</strong>
          <div className="pp-count">{snapshot?.scanned_at?'Agent đã đọc '+snapshot.summary.tiktok+' sản phẩm · lần cuối '+new Date(snapshot.scanned_at).toLocaleString('vi-VN'):'Mở Windows Agent và chọn Đồng bộ sản phẩm lên Sarae'}</div>
        </div>
        <Button variant="outline" leftIcon={<RefreshCw size={15}/>} onClick={()=>void load().catch(e=>toast.push({tone:'danger',title:'Không tải được sản phẩm',description:errorText(e,'')}))}>Làm mới</Button>
      </div>
    </section>
    <section className="pp-panel">
      <div className="pp-toolbar">
        <div className="pp-search"><SearchInput value={query} onChange={e=>setQuery(e.target.value)} placeholder="Tìm sản phẩm theo tên hoặc ID…" aria-label="Tìm sản phẩm"/></div>
        <div className="pp-select"><Select value={shopFilter} onChange={setShopFilter} options={[{value:'all',label:'Tất cả TikTok Shop'},...shops.map(sh=>({value:String(sh.id),label:sh.display_name||'Shop '+sh.seller_id}))]}/></div>
      </div>
      <div className="pp-count">{current.length} / {products.length} sản phẩm</div>
      {!loaded||!current.length?<div className="pp-empty"><EmptyState icon={<Package size={30}/>} title={loaded?'Chưa có sản phẩm phù hợp':'Đang tải…'} description="Sản phẩm được lấy từ TikTok qua Windows Agent, không nhập thủ công trên Sarae."/></div>
      :<div className="pp-table"><Table<Row> data={current as Row[]} columns={columns}/></div>}
    </section>
    <Sheet open={editing!==null} onClose={()=>!busy&&setEditing(null)} width={560} title="Thông tin sản phẩm" footer={<div className="pp-sheet-footer">
      <Button variant="ghost" disabled={busy} onClick={()=>setEditing(null)}>Đóng</Button>
      <Button loading={busy} onClick={()=>void save()}>Lưu mô tả AI</Button>
    </div>}>
      {editing&&<div className="pp-form">
        <div className="pp-row-main"><span className="pp-row-thumb"><Thumb src={editing.image_url} name={editing.name} fallback={<Package size={18}/>} /></span>
          <span className="pp-row-text"><strong>{editing.name}</strong><small>TikTok Product ID: {editing.tiktok_product_id}</small></span></div>
        <div className="pp-form-row"><div>Giá: {editing.price==null?'—':vnd.format(editing.price)}</div><div>Tồn kho: {editing.stock_num??'—'}</div></div>
        <div className="pp-form-row"><div>Giá gốc: {editing.origin_price==null?'—':vnd.format(editing.origin_price)}</div></div>
        <p className="pp-count">Tên, ảnh, giá và tồn kho được quản lý trên TikTok Shop (chỉ đọc).</p>
        <Textarea label="Mô tả cho AI" rows={8} maxLength={10000} value={description}
          placeholder="Chất liệu, màu sắc, size, ưu điểm, cách phối đồ…"
          hint={description.length.toLocaleString('vi-VN')+'/10.000 ký tự'}
          onChange={e=>setDescription(e.target.value)}/>
        <a href="#/product-videos">Thiết lập video LIVE cho sản phẩm →</a>
      </div>}
    </Sheet>
  </div>
}
