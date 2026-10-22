import {useCallback,useEffect,useMemo,useState} from 'react'
import {Button,EmptyState,Input,SearchInput,Select,Sheet,Table,Textarea,useToast,type TableColumn} from './ui/core'
import {CircleAlert,Eye,Package,Plus,Radio,RefreshCw,Trash2} from 'lucide-react'
import {apiRequest,errorText,onAgent} from './mediaApi'
import {useAgent} from './agentContext'
import {Stat,Thumb} from './CatalogParts'

type Product={id:number;shop_id:number|null;tiktok_seller_id:string|null;tiktok_product_id:string|null;name:string;sku:string;description:string;price:number|null;origin_price:number|null;stock_num:number|null;image_url:string;status:'ACTIVE'|'INACTIVE';created_at:string;ai_facts?:AiFacts}
type SizeRow={size:string;weight_min:number|null;weight_max:number|null;height_min:number|null;height_max:number|null;note:string}
type AiFacts={sizes:SizeRow[];colors:string[];material:string;shipping:string;notes:string;category?:string;extras?:Record<string,string>}
type ExtraRow={key:string;value:string}
const EXTRA_KEY=/^[a-z][a-z0-9_]{1,30}$/
const MAX_EXTRAS=30, EXTRA_LEN=200
const CATEGORIES=['áo','váy','quần','đầm','set','phụ kiện']
const COMMON_EXTRAS:[string,string][]=[['form','Form dáng'],['care','Cách giặt'],['fit_tip','Gợi ý mặc'],['origin','Xuất xứ'],['thickness','Độ dày'],['stretch','Co giãn']]
const extraLabel=(key:string)=>COMMON_EXTRAS.find(([k])=>k===key)?.[1]
/** Form copy of a size row: numbers stay as typed text until saved. */
type SizeForm={size:string;weight_min:string;weight_max:string;height_min:string;height_max:string;note:string}
const EMPTY_SIZE:SizeForm={size:'',weight_min:'',weight_max:'',height_min:'',height_max:'',note:''}
const numText=(v:number|null|undefined)=>v==null?'':String(v)
const toNum=(v:string)=>{const n=Number(v.trim().replace(',','.'));return v.trim()===''||!Number.isFinite(n)?null:n}
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
  // Products are identified by their source Seller (seller_id), not by the LIVE account that sells them.
  const sellerOf=(p:Product)=>p.tiktok_seller_id ?? shops.find(sh=>sh.id===p.shop_id)?.seller_id ?? ''
  const sellerName=(id:string)=>!id?'Chưa rõ Seller':shops.find(sh=>sh.seller_id===id)?.display_name||'Seller '+id
  const [videoCounts,setVideoCounts]=useState<Record<number,number>>({})
  const [snapshot,setSnapshot]=useState<TikTokSnapshot|null>(null)
  const [loaded,setLoaded]=useState(false)
  const [query,setQuery]=useState('')
  const [editing,setEditing]=useState<Product|null>(null)
  const [description,setDescription]=useState('')
  const [busy,setBusy]=useState(false)
  const [sizes,setSizes]=useState<SizeForm[]>([])
  const [colors,setColors]=useState('')
  const [material,setMaterial]=useState('')
  const [shipping,setShipping]=useState('')
  const [notes,setNotes]=useState('')
  const [category,setCategory]=useState('')
  const [extras,setExtras]=useState<ExtraRow[]>([])

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
  const sellers=useMemo(()=>[...new Set(products.map(sellerOf).filter(Boolean))].sort(),[products,shops])
  const current=useMemo(()=>products.filter(p=>
    (shopFilter==='all'||sellerOf(p)===shopFilter)
    &&(!query.trim()||(p.name+' '+p.sku).toLocaleLowerCase('vi').includes(query.trim().toLocaleLowerCase('vi')))),[products,shops,shopFilter,query])
  const ready=products.filter(p=>p.status==='ACTIVE'&&!!videoCounts[p.id]).length
  const missing=products.filter(p=>p.status==='ACTIVE'&&!videoCounts[p.id]).length
  const open=(p:Product)=>{const f=p.ai_facts;setEditing(p);setDescription(p.description||'')
    setSizes((f?.sizes??[]).map(r=>({size:r.size,weight_min:numText(r.weight_min),weight_max:numText(r.weight_max),height_min:numText(r.height_min),height_max:numText(r.height_max),note:r.note||''})))
    setColors((f?.colors??[]).join(', '));setMaterial(f?.material??'');setShipping(f?.shipping??'');setNotes(f?.notes??'')
    setCategory(f?.category??'');setExtras(Object.entries(f?.extras??{}).map(([key,value])=>({key,value})))}
  const setExtra=(i:number,patch:Partial<ExtraRow>)=>setExtras(rows=>rows.map((r,j)=>j===i?{...r,...patch}:r))
  const extraError=(r:ExtraRow)=>{const k=r.key.trim()
    if(!k)return r.value.trim()?'Cần đặt tên':''
    if(!EXTRA_KEY.test(k))return 'Chữ thường không dấu, ví dụ fit_tip'
    if(extras.filter(x=>x.key.trim()===k).length>1)return 'Tên bị trùng'
    return r.value.length>EXTRA_LEN?'Tối đa '+EXTRA_LEN+' ký tự':''}
  const extrasInvalid=extras.some(r=>extraError(r))
  const setSize=(i:number,key:keyof SizeForm,value:string)=>setSizes(rows=>rows.map((r,j)=>j===i?{...r,[key]:value}:r))
  const save=async()=>{
    if(!editing)return
    if(extrasInvalid){toast.push({tone:'danger',title:'Kiểm tra lại phần Thông tin thêm'});return}
    setBusy(true)
    try{
      const facts:AiFacts={
        sizes:sizes.filter(r=>r.size.trim()).map(r=>({size:r.size.trim(),weight_min:toNum(r.weight_min),weight_max:toNum(r.weight_max),height_min:toNum(r.height_min),height_max:toNum(r.height_max),note:r.note.trim()})),
        colors:colors.split(',').map(c=>c.trim()).filter(Boolean).slice(0,20),material:material.trim(),shipping:shipping.trim(),notes:notes.trim(),
        category:category.trim().slice(0,40),
        extras:Object.fromEntries(extras.map(r=>[r.key.trim(),r.value.trim()] as const).filter(([k,v])=>k&&v).slice(0,MAX_EXTRAS))}
      await apiRequest('/tiktok-catalog/products/'+editing.id+'/description',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({description})})
      const saved=await apiRequest<{id:number;ai_facts:AiFacts}>('/tiktok-catalog/products/'+editing.id+'/ai-facts',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(facts)})
      setProducts(rows=>rows.map(p=>p.id===editing.id?{...p,description,ai_facts:saved.ai_facts}:p))
      setEditing(null)
      toast.push({tone:'success',title:'Đã lưu thông tin cho AI'})
    }catch(e){toast.push({tone:'danger',title:'Không lưu được thông tin AI',description:errorText(e,'')})}
    finally{setBusy(false)}
  }
  const columns:TableColumn<Row>[]=[
    {key:'name',header:'Sản phẩm TikTok',render:p=><button type="button" className="pp-row-main" onClick={()=>open(p)}>
      <span className="pp-row-thumb"><Thumb src={p.image_url} name={p.name} fallback={<Package size={18}/>} /></span>
      <span className="pp-row-text"><strong>{p.name}</strong><small>{p.tiktok_product_id||p.sku}{p.description?' · Đã có mô tả AI':''}{p.ai_facts?.sizes?.length?' · Có bảng size':''}</small></span>
    </button>},
    {key:'price',header:'Giá bán',align:'right',render:p=><span className="pp-num">{p.price==null?'—':vnd.format(p.price)}</span>},
    {key:'stock',header:'Tồn kho',render:p=><span>{p.stock_num??'—'}</span>},
    {key:'video',header:'Video LIVE',render:p=><span>{videoCounts[p.id]||0} video</span>},
    {key:'shop',header:'Seller nguồn',render:p=><span title="Seller sở hữu sản phẩm (một giỏ Affiliate có thể gồm nhiều Seller)">{sellerName(sellerOf(p))}</span>},
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
        <div className="pp-select"><Select value={shopFilter} onChange={setShopFilter} options={[{value:'all',label:'Tất cả Seller nguồn'},...sellers.map(id=>({value:id,label:sellerName(id)}))]}/></div>
      </div>
      <div className="pp-count">{current.length} / {products.length} sản phẩm</div>
      {!loaded||!current.length?<div className="pp-empty"><EmptyState icon={<Package size={30}/>} title={loaded?'Chưa có sản phẩm phù hợp':'Đang tải…'} description="Sản phẩm được lấy từ TikTok qua Windows Agent, không nhập thủ công trên Sarae."/></div>
      :<div className="pp-table"><Table<Row> data={current as Row[]} columns={columns}/></div>}
    </section>
    <Sheet open={editing!==null} onClose={()=>!busy&&setEditing(null)} width={560} title="Thông tin sản phẩm" footer={<div className="pp-sheet-footer">
      <Button variant="ghost" disabled={busy} onClick={()=>setEditing(null)}>Đóng</Button>
      <Button loading={busy} onClick={()=>void save()}>Lưu thông tin AI</Button>
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
        <div className="pp-facts">
          <strong>Thông tin cho AI trả lời (không tốn token)</strong>
          <p className="pp-facts-hint">AI chỉ nói những gì có ở đây. Ví dụ size M 45–55kg thì khách hỏi '50kg mặc gì' được trả lời ngay, không cần gọi AI.</p>
          <div className="pp-size-table">
            <div className="pp-size-row is-head"><span>Size</span><span>Cân nặng (kg)</span><span>Chiều cao (cm)</span><span>Ghi chú</span><span/></div>
            {sizes.map((r,i)=><div key={i} className="pp-size-row">
              <input aria-label="Tên size" maxLength={20} placeholder="M" value={r.size} onChange={e=>setSize(i,'size',e.target.value)}/>
              <span className="pp-range"><input aria-label="Cân nặng từ" inputMode="decimal" placeholder="từ" value={r.weight_min} onChange={e=>setSize(i,'weight_min',e.target.value)}/>–<input aria-label="Cân nặng đến" inputMode="decimal" placeholder="đến" value={r.weight_max} onChange={e=>setSize(i,'weight_max',e.target.value)}/></span>
              <span className="pp-range"><input aria-label="Chiều cao từ" inputMode="decimal" placeholder="từ" value={r.height_min} onChange={e=>setSize(i,'height_min',e.target.value)}/>–<input aria-label="Chiều cao đến" inputMode="decimal" placeholder="đến" value={r.height_max} onChange={e=>setSize(i,'height_max',e.target.value)}/></span>
              <input aria-label="Ghi chú size" maxLength={100} placeholder="tuỳ chọn" value={r.note} onChange={e=>setSize(i,'note',e.target.value)}/>
              <button type="button" className="pp-icon is-danger" aria-label="Xoá dòng size" onClick={()=>setSizes(rows=>rows.filter((_,j)=>j!==i))}><Trash2 size={15}/></button>
            </div>)}
            <Button variant="outline" leftIcon={<Plus size={14}/>} disabled={sizes.length>=12} onClick={()=>setSizes(rows=>[...rows,{...EMPTY_SIZE}])}>Thêm size{sizes.length>=12?' (tối đa 12)':''}</Button>
          </div>
          <Input label="Màu sắc" maxLength={400} value={colors} placeholder="Đen, Trắng, Be" hint="Cách nhau bằng dấu phẩy (tối đa 20 màu)" onChange={e=>setColors(e.target.value)}/>
          <Input label="Chất liệu" maxLength={200} value={material} placeholder="Cotton 100%, co giãn 4 chiều" onChange={e=>setMaterial(e.target.value)}/>
          <Textarea label="Vận chuyển" rows={2} maxLength={300} value={shipping} placeholder="Giao 2–4 ngày, được kiểm hàng" onChange={e=>setShipping(e.target.value)}/>
          <Input label="Nhóm sản phẩm" maxLength={40} list="pp-categories" value={category} placeholder="áo, váy, quần…" hint="Mẫu chung có thể chỉ dành cho một nhóm (ví dụ chỉ cho váy)" onChange={e=>setCategory(e.target.value)}/>
          <datalist id="pp-categories">{CATEGORIES.map(c=><option key={c} value={c}/>)}</datalist>
          <div className="pp-extras">
            <strong>Thông tin thêm</strong>
            <p className="pp-facts-hint">Mỗi dòng một thông tin. Mẫu chung dùng bằng cách viết {'{product.tên}'}, ví dụ {'{product.form}'}.</p>
            {extras.map((r,i)=>{const err=extraError(r),label=extraLabel(r.key.trim())
              return <div key={i} className="pp-extra-row">
                <span className="pp-extra-cell"><input aria-label="Tên thông tin" maxLength={31} placeholder="form" value={r.key} className={err?'is-error':undefined} onChange={e=>setExtra(i,{key:e.target.value})}/>
                  <small className={err?'is-error':undefined}>{err||label||'chữ thường không dấu'}</small></span>
                <input aria-label="Nội dung" maxLength={EXTRA_LEN} placeholder="rộng, dễ mặc" value={r.value} onChange={e=>setExtra(i,{value:e.target.value})}/>
                <button type="button" className="pp-icon is-danger" aria-label="Xoá thông tin" onClick={()=>setExtras(rows=>rows.filter((_,j)=>j!==i))}><Trash2 size={15}/></button>
              </div>})}
            <div className="pp-extra-chips">
              {COMMON_EXTRAS.filter(([k])=>!extras.some(r=>r.key.trim()===k)).map(([k,label])=><button key={k} type="button" className="pp-chip" disabled={extras.length>=MAX_EXTRAS} onClick={()=>setExtras(rows=>[...rows,{key:k,value:''}])}>+ {label}</button>)}
              <Button variant="outline" size="sm" leftIcon={<Plus size={14}/>} disabled={extras.length>=MAX_EXTRAS} onClick={()=>setExtras(rows=>[...rows,{key:'',value:''}])}>Thêm dòng{extras.length>=MAX_EXTRAS?' (tối đa '+MAX_EXTRAS+')':''}</Button>
            </div>
          </div>
          <Textarea label="Ghi chú khác" rows={3} maxLength={500} value={notes} placeholder="Đổi size trong 7 ngày…" hint={notes.length+'/500 ký tự'} onChange={e=>setNotes(e.target.value)}/>
        </div>
        <a href="#/cloud-media">Gắn video cho sản phẩm trong Thư viện video →</a>
      </div>}
    </Sheet>
  </div>
}
