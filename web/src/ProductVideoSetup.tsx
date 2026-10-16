import {useEffect,useState} from 'react'
import {Button,Select} from './ui/core'
import {ArrowDown,ArrowUp,Trash2} from 'lucide-react'
import './live.css'

type Product={id:number;name:string;sku:string}
type Video={id:number;filename:string;duration_seconds:number|null;local_path?:string|null}
const root=((import.meta.env.VITE_API_URL as string|undefined)||'http://127.0.0.1:8000').replace(/\/$/,'')
async function req<T>(path:string,init?:RequestInit):Promise<T>{
  const res=await fetch(root+'/api'+path,init)
  if(!res.ok){const b=await res.json().catch(()=>({}));throw Error(typeof b.detail==='string'?b.detail:'HTTP '+res.status)}
  return res.json() as Promise<T>
}
export default function ProductVideoSetup(){
  const [products,setProducts]=useState<Product[]>([])
  const [videos,setVideos]=useState<Video[]>([])
  const [selected,setSelected]=useState('')
  const [chosen,setChosen]=useState<number[]>([])
  const [addId,setAddId]=useState('')
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [notice,setNotice]=useState('')
  const [dirty,setDirty]=useState(false)
  useEffect(()=>{void Promise.all([req<Product[]>('/products?limit=500'),req<Video[]>('/media?limit=500')]).then(([p,v])=>{setProducts(p);setVideos(v)}).catch(e=>setError(String(e)))},[])
  useEffect(()=>{
    if(!selected){setChosen([]);setDirty(false);return}
    let active=true
    setError('');setNotice('')
    void req<{media_ids:number[]}>('/product-videos/'+selected).then(d=>{if(active){setChosen(d.media_ids);setDirty(false)}}).catch(e=>{if(active)setError(String(e))})
    return()=>{active=false}
  },[selected])
  const change=(ids:number[])=>{setChosen(ids);setDirty(true)}
  const move=(i:number,delta:number)=>{const next=[...chosen];const j=i+delta;if(j<0||j>=next.length)return;[next[i],next[j]]=[next[j],next[i]];change(next)}
  const save=async()=>{if(!selected)return;setBusy(true);setError('');setNotice('')
    try{await req('/product-videos/'+selected,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({media_ids:chosen})});setDirty(false);setNotice('Đã lưu danh sách video LIVE cho sản phẩm.')}
    catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}
  }
  return <section className="live-workspace product-video-setup panel">
    <div className="live-head"><div><h3>Cấu hình video LIVE theo sản phẩm</h3><p>Chọn video từ thư viện và lưu thứ tự phát cho từng sản phẩm. Không sao chép hay xóa video gốc.</p></div></div>
    {error&&<p className="live-message error" role="alert">{error}</p>}{notice&&<p className="live-message" role="status">{notice}</p>}
    <div className="live-section">
      <Select label="Sản phẩm cần chuẩn bị" value={selected} options={[{value:'',label:'Chọn sản phẩm…'},...products.map(p=>({value:String(p.id),label:p.name+' · '+p.sku}))]} onChange={v=>{if(dirty&&!window.confirm('Danh sách chưa lưu. Bỏ thay đổi?'))return;setSelected(v)}}/>
      {selected&&<>
        <div className="live-pick"><Select label="Thêm video từ Media Library" value={addId} options={[{value:'',label:'Chọn video…'},...videos.filter(v=>!chosen.includes(v.id)).map(v=>({value:String(v.id),label:v.filename}))]} onChange={v=>{if(v){change([...chosen,Number(v)]);setAddId('')}}}/></div>
        <h4>Danh sách phát theo thứ tự ({chosen.length} video)</h4>
        {!chosen.length&&<p>Chưa chọn video. Sản phẩm sẽ chưa thể bắt đầu LIVE.</p>}
        <ol className="live-order">{chosen.map((id,i)=><li key={id}><span>{i+1}. {videos.find(v=>v.id===id)?.filename||'Video đã bị xóa'}</span><div>
          <button aria-label="Đưa lên" disabled={i===0} onClick={()=>move(i,-1)}><ArrowUp size={16}/></button>
          <button aria-label="Đưa xuống" disabled={i===chosen.length-1} onClick={()=>move(i,1)}><ArrowDown size={16}/></button>
          <button aria-label="Gỡ khỏi sản phẩm" onClick={()=>change(chosen.filter(v=>v!==id))}><Trash2 size={16}/></button>
        </div></li>)}</ol>
        <Button disabled={busy||!dirty} onClick={()=>void save()}>{busy?'Đang lưu…':'Lưu cấu hình video'}</Button>
        <p>LIVE Control sẽ phát vòng lặp theo danh sách này. Một video có thể được chọn cho nhiều sản phẩm.</p>
      </>}
    </div>
  </section>
}
