/** "Chính sách shop": facts true for the whole shop (ship time, return policy…), entered once and used by every template as {shop.key}. */
import {useEffect,useState} from 'react'
import {Button,EmptyState,useToast} from './ui/core'
import {Plus,Save,ScrollText,Trash2} from 'lucide-react'
import {apiRequest,errorText} from './mediaApi'
import './knowledge.css'

type Policy={key:string;value:string;note:string}
type Suggested={key:string;label:string;example:string}
type View={policies:Policy[];suggested:Suggested[]}
type Row=Policy&{uid:number}

const KEY=/^[a-z][a-z0-9_]{1,30}$/
const VALUE_LEN=300, NOTE_LEN=200
let uid=0
const toRows=(items:Policy[]):Row[]=>items.map(p=>({key:p.key,value:p.value,note:p.note??'',uid:++uid}))

export default function KnowledgePolicies(){
  const toast=useToast()
  const [rows,setRows]=useState<Row[]|null>(null)
  const [suggested,setSuggested]=useState<Suggested[]>([])
  const [dirty,setDirty]=useState(false)
  const [busy,setBusy]=useState(false)

  useEffect(()=>{
    let live=true
    apiRequest<View>('/ai/knowledge/policies')
      .then(v=>{if(!live)return;setRows(toRows(v.policies));setSuggested(v.suggested??[])})
      .catch(e=>{if(live){setRows([]);toast.push({tone:'danger',title:'Không tải được chính sách shop',description:errorText(e,'')})}})
    return ()=>{live=false}
  },[toast])

  const list=rows??[]
  const labelOf=(key:string)=>suggested.find(s=>s.key===key)
  const keyError=(r:Row)=>{
    const k=r.key.trim()
    if(!k)return r.value.trim()?'Cần đặt tên':''
    if(!KEY.test(k))return 'Chỉ chữ thường không dấu, số, dấu _ (ví dụ ship_time)'
    if(list.filter(x=>x.key.trim()===k).length>1)return 'Tên bị trùng'
    return ''
  }
  const valueError=(r:Row)=>r.key.trim()&&!r.value.trim()?'Cần nhập nội dung':r.value.length>VALUE_LEN?'Tối đa '+VALUE_LEN+' ký tự':''
  const invalid=list.some(r=>keyError(r)||valueError(r))
  const update=(id:number,patch:Partial<Policy>)=>{setRows(rs=>rs&&rs.map(r=>r.uid===id?{...r,...patch}:r));setDirty(true)}
  const add=(key='',value='')=>{setRows(rs=>[...(rs??[]),{key,value,note:'',uid:++uid}]);setDirty(true)}
  const removeRow=(id:number)=>{setRows(rs=>rs&&rs.filter(r=>r.uid!==id));setDirty(true)}
  const unused=suggested.filter(s=>!list.some(r=>r.key.trim()===s.key))

  const save=async()=>{
    if(invalid)return
    setBusy(true)
    try{
      const policies=list.filter(r=>r.key.trim()).map(r=>({key:r.key.trim(),value:r.value.trim(),note:r.note.trim()}))
      const v=await apiRequest<View>('/ai/knowledge/policies',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({policies})})
      setRows(toRows(v.policies));if(v.suggested)setSuggested(v.suggested);setDirty(false)
      toast.push({tone:'success',title:'Đã lưu chính sách shop'})
    }catch(e){toast.push({tone:'danger',title:'Chưa lưu được',description:errorText(e,'')})}
    finally{setBusy(false)}
  }

  return <section className="ab-panel kn-panel">
    <div className="ab-body">
      <div className="ab-toolbar">
        <p className="ab-muted">Nhập 1 lần cho cả shop. Mẫu chung dùng bằng cách viết <code>{'{shop.tên}'}</code>, ví dụ <code>{'{shop.ship_time}'}</code>.</p>
        <Button loading={busy} disabled={!dirty||invalid} leftIcon={<Save size={15}/>} onClick={()=>void save()}>Lưu tất cả</Button>
      </div>
      {unused.length>0&&<div className="kn-suggest">
        <span className="ab-muted">Thêm nhanh:</span>
        {unused.map(s=><button key={s.key} type="button" className="ab-sample" title={'Ví dụ: '+s.example} onClick={()=>add(s.key)}>+ {s.label} <small>({s.key})</small></button>)}
      </div>}
      {rows===null?<p className="ab-muted">Đang tải…</p>:!list.length
        ?<div className="ab-empty"><EmptyState icon={<ScrollText size={28}/>} title="Chưa có chính sách nào" description="Ví dụ: thời gian giao hàng, đổi trả, thanh toán. Bấm Thêm nhanh ở trên hoặc thêm dòng." actionLabel="Thêm dòng" onAction={()=>add()}/></div>
        :<div className="kn-table">
          <div className="kn-row is-head"><span>Tên (mã)</span><span>Nội dung</span><span>Ghi chú</span><span/></div>
          {list.map(r=>{const ke=keyError(r),ve=valueError(r),s=labelOf(r.key.trim())
            return <div key={r.uid} className="kn-row">
              <span className="kn-cell">
                <input aria-label="Tên chính sách" maxLength={31} placeholder="ship_time" value={r.key} className={ke?'is-error':undefined} onChange={e=>update(r.uid,{key:e.target.value})}/>
                {ke?<small className="kn-err">{ke}</small>:s?<small>{s.label}</small>:<small>chữ thường không dấu, ví dụ ship_time</small>}
              </span>
              <span className="kn-cell">
                <input aria-label="Nội dung" maxLength={VALUE_LEN} placeholder={s?.example||'2-4 ngày'} value={r.value} className={ve?'is-error':undefined} onChange={e=>update(r.uid,{value:e.target.value})}/>
                {ve&&<small className="kn-err">{ve}</small>}
              </span>
              <span className="kn-cell"><input aria-label="Ghi chú" maxLength={NOTE_LEN} placeholder="tuỳ chọn" value={r.note} onChange={e=>update(r.uid,{note:e.target.value})}/></span>
              <button type="button" className="pp-icon is-danger" aria-label="Xoá dòng" onClick={()=>removeRow(r.uid)}><Trash2 size={15}/></button>
            </div>})}
          <div><Button variant="outline" size="sm" leftIcon={<Plus size={14}/>} onClick={()=>add()}>Thêm dòng</Button></div>
        </div>}
      {dirty&&<p className="kn-warn">Có thay đổi chưa lưu.</p>}
    </div>
  </section>
}
