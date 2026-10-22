/** "Mẫu chung": reusable answer templates with {placeholders}, used for every product that has the data they need. */
import {useEffect,useMemo,useRef,useState} from 'react'
import {Badge,Button,ConfirmModal,EmptyState,Input,SearchInput,Select,Sheet,Switch,Textarea,useToast} from './ui/core'
import {Check,Copy,Eye,LayoutTemplate,Pencil,Plus,Save,Trash2} from 'lucide-react'
import {apiRequest,errorText} from './mediaApi'
import './knowledge.css'

type Source='builtin'|'manual'|'ai'|'import'
type Template={id:string;intent:string;category:string|null;when:string|null;patterns:string[];say:string[];text:string[];source:Source;approved:boolean;enabled:boolean;uses:number;editable:boolean}
type Placeholders={product:string[];ask:string[];calc:string[];shop:string[]}
type ListView={templates:Template[];placeholders:Placeholders;intents:string[]}
type Preview={say:string[];text:string[];missing:string[];matched:boolean|null}
type Product={id:number;name:string}
type Draft={id:string|null;intent:string;category:string;when:string;patterns:string;say:string;text:string}
type Field='patterns'|'say'|'text'

const INTENTS:Record<string,string>={ASK_PRICE:'Giá',ASK_SIZE:'Size',ASK_COLOR:'Màu',ASK_MATERIAL:'Chất liệu',ASK_STOCK:'Còn hàng',ASK_SHIPPING:'Vận chuyển',ASK_RETURN:'Đổi trả',ASK_PAYMENT:'Thanh toán',ASK_PROMO:'Khuyến mãi',ASK_CARE:'Bảo quản/giặt',ASK_STYLE:'Phối đồ/form',ASK_QUALITY:'Chất lượng',SHOW_PRODUCT:'Xem sản phẩm',ORDER:'Chốt đơn',GENERAL:'Khác'}
const intentLabel=(i:string)=>INTENTS[i]??i
const SOURCES:Record<Source,string>={builtin:'Có sẵn',manual:'Shop viết',ai:'AI học',import:'Nhập file'}
const WHEN:Record<string,string>={asked_color_available:'Khách hỏi màu shop có',asked_color_unavailable:'Khách hỏi màu shop không có',asked_size_available:'Khách hỏi size shop có',asked_size_unavailable:'Khách hỏi size shop không có',in_stock:'Còn hàng',out_of_stock:'Hết hàng',has_discount:'Đang giảm giá'}
const NAMESPACES:[keyof Placeholders,string][]=[['product','Sản phẩm'],['ask','Khách hỏi'],['calc','Tự tính'],['shop','Chính sách shop']]
const MAX_PATTERNS=30, MAX_SAY=8, MAX_TEXT=8, TEXT_LEN=90, SAY_WORDS=18
const lines=(s:string)=>s.split('\n').map(l=>l.trim()).filter(Boolean)
const json=(method:string,body:unknown):RequestInit=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
/** Placeholder names may come as "price", "product.price" or "{product.price}"; always returns "{ns.key}". */
const token=(ns:string,name:string)=>{const n=name.replace(/^\{|\}$/g,'');return '{'+(n.includes('.')?n:ns+'.'+n)+'}'}
const emptyDraft=():Draft=>({id:null,intent:'GENERAL',category:'',when:'',patterns:'',say:'',text:''})
const toDraft=(t:Template,copy=false):Draft=>({id:copy?null:t.id,intent:t.intent,category:t.category??'',when:t.when??'',patterns:t.patterns.join('\n'),say:t.say.join('\n'),text:t.text.join('\n')})

export default function KnowledgeTemplates(){
  const toast=useToast()
  const [view,setView]=useState<ListView|null>(null)
  const [query,setQuery]=useState('')
  const [intent,setIntent]=useState('all')
  const [source,setSource]=useState('all')
  const [version,setVersion]=useState(0)
  const [draft,setDraft]=useState<Draft|null>(null)
  const [formKey,setFormKey]=useState(0)
  const [removing,setRemoving]=useState<Template|null>(null)
  const [busy,setBusy]=useState('')

  useEffect(()=>{
    let live=true
    const params=new URLSearchParams({q:query.trim(),intent:intent==='all'?'':intent,source:source==='all'?'':source})
    const timer=window.setTimeout(()=>{
      apiRequest<ListView>('/ai/knowledge/templates?'+params.toString())
        .then(v=>{if(live)setView(v)})
        .catch(e=>{if(live){setView(v=>v??{templates:[],placeholders:{product:[],ask:[],calc:[],shop:[]},intents:[]});toast.push({tone:'danger',title:'Không tải được mẫu chung',description:errorText(e,'')})}})
    },query?300:0)
    return ()=>{live=false;window.clearTimeout(timer)}
  },[query,intent,source,version,toast])

  const intents=view?.intents.length?view.intents:Object.keys(INTENTS)
  const groups=useMemo(()=>{
    const map=new Map<string,Template[]>()
    for(const t of view?.templates??[])map.set(t.intent,[...(map.get(t.intent)??[]),t])
    const order=Object.keys(INTENTS), rank=(i:string)=>{const n=order.indexOf(i);return n<0?order.length:n}
    return [...map.entries()].sort((a,b)=>rank(a[0])-rank(b[0]))
  },[view])
  const replace=(t:Template)=>setView(v=>v&&{...v,templates:v.templates.map(x=>x.id===t.id?t:x)})
  const act=async(t:Template,key:string,run:()=>Promise<Template|void>,ok:string)=>{
    setBusy(key+t.id)
    try{const saved=await run();if(saved)replace(saved);toast.push({tone:'success',title:ok})}
    catch(e){toast.push({tone:'danger',title:'Chưa làm được',description:errorText(e,'')})}
    finally{setBusy('')}
  }
  const toggle=(t:Template,enabled:boolean)=>void act(t,'toggle',async()=>{
    const r=await apiRequest<Template|undefined>('/ai/knowledge/templates/'+encodeURIComponent(t.id)+'/toggle',json('POST',{enabled}))
    return r&&typeof r==='object'&&'id' in r?r:{...t,enabled}
  },enabled?'Đã bật mẫu':'Đã tắt mẫu')
  const approve=(t:Template)=>void act(t,'approve',async()=>{
    const r=await apiRequest<Template|undefined>('/ai/knowledge/templates/'+encodeURIComponent(t.id)+'/approve',{method:'POST'})
    return r&&typeof r==='object'&&'id' in r?r:{...t,approved:true}
  },'Đã duyệt mẫu')
  const remove=async()=>{
    if(!removing)return
    setBusy('remove')
    try{
      await apiRequest('/ai/knowledge/templates/'+encodeURIComponent(removing.id),{method:'DELETE'})
      setView(v=>v&&{...v,templates:v.templates.filter(x=>x.id!==removing.id)})
      setRemoving(null)
      toast.push({tone:'success',title:'Đã xoá mẫu'})
    }catch(e){toast.push({tone:'danger',title:'Không xoá được',description:errorText(e,'')})}
    finally{setBusy('')}
  }
  const open=(d:Draft)=>{setFormKey(k=>k+1);setDraft(d)}

  return <section className="ab-panel kn-panel">
    <div className="kn-toolbar">
      <div className="kn-search"><SearchInput value={query} onChange={e=>setQuery(e.target.value)} placeholder="Tìm theo câu hỏi hoặc câu trả lời…" aria-label="Tìm mẫu"/></div>
      <div className="kn-select"><Select value={intent} onChange={setIntent} options={[{value:'all',label:'Mọi loại câu hỏi'},...intents.map(i=>({value:i,label:intentLabel(i)}))]}/></div>
      <div className="kn-select"><Select value={source} onChange={setSource} options={[{value:'all',label:'Mọi nguồn'},...Object.entries(SOURCES).map(([value,label])=>({value,label}))]}/></div>
      <Button leftIcon={<Plus size={15}/>} onClick={()=>open(emptyDraft())}>Thêm mẫu</Button>
    </div>
    <p className="ab-muted kn-pad">Chữ trong ngoặc nhọn như <code>{'{product.price}'}</code> sẽ được thay bằng dữ liệu của từng sản phẩm. Mẫu chỉ được dùng khi sản phẩm có đủ dữ liệu.</p>
    <div className="ab-body">
      {view===null?<p className="ab-muted">Đang tải…</p>:!groups.length
        ?<div className="ab-empty"><EmptyState icon={<LayoutTemplate size={28}/>} title="Chưa có mẫu phù hợp" description="Thêm mẫu cho câu khách hay hỏi. Một mẫu dùng được cho mọi sản phẩm." actionLabel="Thêm mẫu" onAction={()=>open(emptyDraft())}/></div>
        :groups.map(([key,items])=><div key={key} className="ab-group">
          <div className="ab-group-head"><strong>{intentLabel(key)} ({items.length})</strong></div>
          {items.map(t=><article key={t.id} className={'ab-card'+(t.enabled?'':' kn-off')}>
            <div className="ab-card-top">
              <div className="ab-chips">
                <Badge tone={t.source==='builtin'?'info':t.source==='ai'?'primary':'default'}>{SOURCES[t.source]??t.source}</Badge>
                {!t.enabled&&<Badge tone="danger">Đang tắt</Badge>}
                {t.source==='ai'&&!t.approved&&<Badge tone="warning">AI học – chưa duyệt</Badge>}
                {t.category&&<Badge>Chỉ cho: {t.category}</Badge>}
                {t.when&&<Badge>Khi: {WHEN[t.when]??t.when}</Badge>}
                <span className="ab-muted">Dùng {t.uses} lần</span>
              </div>
              <Switch label={t.enabled?'Đang bật':'Đang tắt'} checked={t.enabled} disabled={busy==='toggle'+t.id} onChange={e=>toggle(t,e.target.checked)}/>
            </div>
            <div className="kn-patterns">{t.patterns.map((p,i)=><span key={i} className="kn-chip">{p}</span>)}</div>
            {t.say.map((s,i)=><div key={'s'+i} className="ab-line"><span aria-label="Câu AI sẽ nói">🔊</span><span>{s}</span></div>)}
            {t.text.map((s,i)=><div key={'t'+i} className="ab-line is-text"><span aria-label="Câu comment">💬</span><span>{s}</span></div>)}
            <div className="ab-actions">
              {t.source==='ai'&&!t.approved&&<Button size="sm" leftIcon={<Check size={13}/>} loading={busy==='approve'+t.id} onClick={()=>approve(t)}>Duyệt</Button>}
              {t.editable
                ?<Button size="sm" variant="outline" leftIcon={<Pencil size={13}/>} onClick={()=>open(toDraft(t))}>Sửa</Button>
                :<Button size="sm" variant="outline" leftIcon={<Copy size={13}/>} onClick={()=>open(toDraft(t,true))}>Tạo bản sao để sửa</Button>}
              {t.source!=='builtin'&&<Button size="sm" variant="ghost" leftIcon={<Trash2 size={13}/>} onClick={()=>setRemoving(t)}>Xoá</Button>}
            </div>
          </article>)}
        </div>)}
    </div>
    {draft&&<TemplateForm key={formKey} draft={draft} intents={intents} placeholders={view?.placeholders??{product:[],ask:[],calc:[],shop:[]}}
      onClose={()=>setDraft(null)} onSaved={()=>{setDraft(null);setVersion(v=>v+1)}}/>}
    <ConfirmModal open={removing!==null} onClose={()=>busy!=='remove'&&setRemoving(null)} onConfirm={()=>void remove()} loading={busy==='remove'} tone="danger"
      title="Xoá mẫu này?" description="AI sẽ không dùng mẫu này cho sản phẩm nào nữa. Muốn tạm ngưng thì nên Tắt thay vì Xoá." confirmLabel="Xoá" cancelLabel="Huỷ"/>
  </section>
}

function TemplateForm({draft,intents,placeholders,onClose,onSaved}:{draft:Draft;intents:string[];placeholders:Placeholders;onClose:()=>void;onSaved:()=>void}){
  const toast=useToast()
  const [form,setForm]=useState<Draft>(draft)
  const [busy,setBusy]=useState(false)
  const [focused,setFocused]=useState<Field>('say')
  const refs={patterns:useRef<HTMLTextAreaElement>(null),say:useRef<HTMLTextAreaElement>(null),text:useRef<HTMLTextAreaElement>(null)}
  const [products,setProducts]=useState<Product[]>([])
  const [productId,setProductId]=useState('')
  const [comment,setComment]=useState('')
  const [preview,setPreview]=useState<Preview|null>(null)
  const [previewing,setPreviewing]=useState(false)

  useEffect(()=>{
    let live=true
    apiRequest<Product[]>('/products?limit=500').then(items=>{if(!live)return;setProducts(items);setProductId(id=>id||String(items[0]?.id??''))}).catch(()=>{})
    return ()=>{live=false}
  },[])

  const patterns=lines(form.patterns), say=lines(form.say), text=lines(form.text)
  const patternError=!patterns.length?'Cần ít nhất 1 cách hỏi':patterns.length>MAX_PATTERNS?'Tối đa '+MAX_PATTERNS+' cách hỏi':''
  const sayError=!say.length?'Cần ít nhất 1 câu':say.length>MAX_SAY?'Tối đa '+MAX_SAY+' câu':''
  const longSay=say.filter(l=>l.split(/\s+/).length>SAY_WORDS).length
  const textError=text.length>MAX_TEXT?'Tối đa '+MAX_TEXT+' câu':text.some(l=>l.length>TEXT_LEN)?'Mỗi câu tối đa '+TEXT_LEN+' ký tự':''
  const invalid=!!patternError||!!sayError||!!textError
  const set=(k:keyof Draft,v:string)=>setForm(f=>({...f,[k]:v}))
  const body=()=>({intent:form.intent,category:form.category.trim()||null,when:form.when||null,patterns,say,text})

  const insert=(value:string)=>{
    const el=refs[focused].current
    const current=form[focused]
    const start=el?.selectionStart??current.length, end=el?.selectionEnd??current.length
    setForm(f=>({...f,[focused]:current.slice(0,start)+value+current.slice(end)}))
    requestAnimationFrame(()=>{if(!el)return;el.focus();el.setSelectionRange(start+value.length,start+value.length)})
  }

  const save=async()=>{
    if(invalid)return
    setBusy(true)
    try{
      await apiRequest<Template>(form.id==null?'/ai/knowledge/templates':'/ai/knowledge/templates/'+encodeURIComponent(form.id),json(form.id==null?'POST':'PUT',body()))
      toast.push({tone:'success',title:form.id==null?'Đã thêm mẫu':'Đã lưu mẫu'})
      onSaved()
    }catch(e){toast.push({tone:'danger',title:'Chưa lưu được mẫu',description:errorText(e,'')})}
    finally{setBusy(false)}
  }
  const runPreview=async()=>{
    if(!productId)return
    setPreviewing(true)
    try{setPreview(await apiRequest<Preview>('/ai/knowledge/templates/preview',json('POST',{...body(),product_id:Number(productId),comment:comment.trim()})))}
    catch(e){toast.push({tone:'danger',title:'Không xem thử được',description:errorText(e,'')})}
    finally{setPreviewing(false)}
  }
  const area=(field:Field,label:string,rows:number,placeholder:string,hint:string,error:string)=>
    <Textarea ref={refs[field]} label={label} rows={rows} value={form[field]} placeholder={placeholder} hint={hint} error={form[field]&&error?error:undefined}
      className={focused===field?'kn-focused':undefined} onFocus={()=>setFocused(field)} onChange={e=>set(field,e.target.value)}/>

  return <Sheet open onClose={()=>!busy&&onClose()} width={640} title={form.id==null?'Thêm mẫu chung':'Sửa mẫu chung'} footer={<div className="pp-sheet-footer">
    <Button variant="ghost" disabled={busy} onClick={onClose}>Huỷ</Button>
    <Button loading={busy} disabled={invalid} leftIcon={<Save size={15}/>} onClick={()=>void save()}>Lưu mẫu</Button>
  </div>}>
    <div className="pp-form">
      <div className="pp-form-row">
        <Select label="Loại câu hỏi" value={form.intent} onChange={v=>set('intent',v)} options={intents.map(i=>({value:i,label:intentLabel(i)}))}/>
        <Select label="Chỉ dùng khi (không bắt buộc)" value={form.when} onChange={v=>set('when',v)} options={[{value:'',label:'Luôn dùng'},...Object.entries(WHEN).map(([value,label])=>({value,label}))]}/>
      </div>
      <Input label="Chỉ cho nhóm sản phẩm (không bắt buộc)" maxLength={40} value={form.category} placeholder="Để trống = mọi sản phẩm. Ví dụ: áo, váy" onChange={e=>set('category',e.target.value)}/>
      <div className="kn-palette">
        <strong>Bấm để chèn dữ liệu vào ô đang chọn</strong>
        {NAMESPACES.map(([ns,caption])=>placeholders[ns]?.length?<div key={ns} className="kn-palette-row">
          <span className="ab-muted">{caption}</span>
          {placeholders[ns].map(p=>{const t=token(ns,p);return <button key={t} type="button" className="kn-chip is-button" onMouseDown={e=>e.preventDefault()} onClick={()=>insert(t)}>{t}</button>})}
        </div>:null)}
      </div>
      {area('patterns','Khách thường hỏi thế nào',4,'giá bn\n{ask.weight} mặc size gì\nsz nào vừa {ask.weight}','Mỗi dòng một cách hỏi, viết ngắn như khách gõ (1–'+MAX_PATTERNS+' dòng) · '+patterns.length+' dòng',patternError)}
      {area('say','Câu AI sẽ nói',4,'Mẫu này {product.price} thôi nha.\n{ask.weight} mặc {calc.size_for_weight} là vừa nha.',
        'Mỗi dòng một cách nói (1–'+MAX_SAY+' dòng, tối đa '+SAY_WORDS+' chữ). Không chào, không gọi tên khách, viết số bằng số.'+(longSay?' · '+longSay+' câu hơi dài':''),sayError)}
      {area('text','Câu comment (không bắt buộc)',3,'{ask.weight} lấy {calc.size_for_weight} vừa nha','0–'+MAX_TEXT+' dòng, mỗi dòng ≤ '+TEXT_LEN+' ký tự',textError)}
      <div className="kn-preview">
        <strong>Xem thử với một sản phẩm</strong>
        <div className="pp-form-row">
          <Select label="Sản phẩm" value={productId} onChange={setProductId} placeholder="Chọn sản phẩm" options={products.map(p=>({value:String(p.id),label:p.name}))}/>
          <Input label="Bình luận thử (không bắt buộc)" maxLength={200} value={comment} placeholder="55kg mặc size gì" onChange={e=>setComment(e.target.value)}/>
        </div>
        <div><Button variant="outline" size="sm" leftIcon={<Eye size={14}/>} loading={previewing} disabled={!productId||!say.length} onClick={()=>void runPreview()}>Xem thử</Button></div>
        {preview&&<div className="kn-preview-out">
          {preview.matched===true&&<p className="kn-ok">Bình luận thử khớp với mẫu này.</p>}
          {preview.matched===false&&<p className="kn-warn">Bình luận thử chưa khớp — thêm cách hỏi giống vậy vào ô “Khách thường hỏi thế nào”.</p>}
          {preview.missing.length>0&&<p className="kn-warn">Sản phẩm này thiếu dữ liệu: {preview.missing.join(', ')}. Mẫu sẽ không dùng cho sản phẩm này cho đến khi nhập đủ.</p>}
          {preview.say.map((s,i)=><div key={'s'+i} className="ab-line"><span aria-label="AI sẽ nói">🔊</span><span>{s}</span></div>)}
          {preview.text.map((s,i)=><div key={'t'+i} className="ab-line is-text"><span aria-label="Câu comment">💬</span><span>{s}</span></div>)}
        </div>}
      </div>
    </div>
  </Sheet>
}
