/** "Hỏi đáp AI": per-product answer bank (what the AI says for common questions) and a playground to try the AI without a LIVE. */
import {useCallback,useEffect,useMemo,useRef,useState} from 'react'
import {Badge,Button,ConfirmModal,EmptyState,Input,SearchInput,SegmentedControl,Select,Sheet,Textarea,useToast} from './ui/core'
import {BookOpen,Copy,MessageSquareText,Package,Pencil,Play,Plus,Save,Sparkles,Trash2} from 'lucide-react'
import {api,apiRequest,errorText} from './mediaApi'
import {authHeaders} from './authToken'
import {Thumb} from './CatalogParts'
import './answer-bank.css'

type Product={id:number;name:string;price:number|null;image_url:string;description?:string;ai_facts?:unknown}
type Intent='ASK_PRICE'|'ASK_SIZE'|'ASK_COLOR'|'ASK_MATERIAL'|'ASK_STOCK'|'ASK_SHIPPING'|'SHOW_PRODUCT'|'GENERAL'|'ORDER'
type Answer={id:number;question:string;intent:string;say:string[];text:string[];source:'manual'|'operator'|'ai'|'pregen';approved:boolean;uses:number;last_used_at:string|null;scope:'shop'|'auto'}
type BankView={product:{id:number;name:string;price:number|null};answers:Answer[]}
type Viewer={username:string;nickname:string;comment:string}
type Reply={id:number;question:string;comments:Viewer[];intent:string;slots:Record<string,unknown>;source:'rule'|'bank'|'ai';say:string;text:string;intro:string|null;body:string;spoken_text:string;comment_text:string;review_reasons:string[];route:'review'|'voice'|'comment'|'both';confidence:number}
type Playground={product:{id:number;name:string;price:number|null};replies:Reply[];skipped:{username:string;comment:string;reason:string}[];usage:{input_tokens:number;output_tokens:number;latency_ms:number}[];error:string|null}
type VoiceConfig={voice?:string;rate?:unknown}
type BadgeTone='default'|'primary'|'success'|'warning'|'danger'|'info'
type Draft={id:number|null;question:string;intent:string;say:string;text:string}

const INTENTS:Record<Intent,string>={ASK_PRICE:'Giá',ASK_SIZE:'Size',ASK_COLOR:'Màu',ASK_MATERIAL:'Chất liệu',ASK_STOCK:'Còn hàng',ASK_SHIPPING:'Vận chuyển',SHOW_PRODUCT:'Xem sản phẩm',GENERAL:'Khác',ORDER:'Chốt đơn'}
const intentLabel=(i:string)=>INTENTS[i as Intent]??i
const SOURCES:Record<Answer['source'],string>={manual:'Shop thêm',operator:'Bạn sửa khi LIVE',ai:'AI',pregen:'AI chuẩn bị sẵn'}
const REPLY_SOURCES:Record<Reply['source'],{label:string;tone:BadgeTone}>={rule:{label:'Luật · 0 token',tone:'success'},bank:{label:'Kho câu · 0 token',tone:'info'},ai:{label:'AI',tone:'primary'}}
const ROUTES:Record<Reply['route'],{label:string;tone:BadgeTone}>={review:{label:'Cần duyệt',tone:'warning'},voice:{label:'Đọc giọng',tone:'success'},comment:{label:'Comment',tone:'info'},both:{label:'Đọc + comment',tone:'primary'}}
const SAMPLES=['giá bn shop','55kg mặc size gì','có màu đen không','vải gì vậy','giá bn, có size L k','hàng lỗi đổi sao','hi']
const MAX_SAY=5, MAX_TEXT=5, SAY_LEN=160, TEXT_LEN=120, MAX_LINES=20
const HINT='Viết như bạn nói khi LIVE. Viết số bằng số (200k, 55kg) – hệ thống tự đọc thành chữ. Có thể viết nhiều cách nói để AI xoay vòng cho tự nhiên.'
const vnd=new Intl.NumberFormat('vi-VN',{style:'currency',currency:'VND',maximumFractionDigits:0})
const lines=(s:string)=>s.split('\n').map(l=>l.trim()).filter(Boolean)
const json=(method:string,body:unknown):RequestInit=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
const slug=(name:string)=>name.normalize('NFD').replace(/[̀-ͯ]/g,'').replace(/đ/g,'d').replace(/Đ/g,'D').toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_|_$/g,'')||'khach'

/** One viewer comment per line; `Tên: nội dung` sets the viewer name, otherwise "Khách 1", "Khách 2"… */
function parseComments(raw:string):Viewer[]{
  let guest=0
  return lines(raw).slice(0,MAX_LINES).map(line=>{
    const m=/^([^:]{1,30}):\s*(.+)$/.exec(line)
    const nickname=m?m[1].trim():'Khách '+(++guest)
    return {username:slug(nickname),nickname,comment:m?m[2].trim():line}
  })
}

/** Plays `text` with the shop's LIVE voice (binary response, so a raw fetch rather than apiRequest). */
function useSpeaker(){
  const toast=useToast()
  const config=useRef<VoiceConfig|null>(null)
  const audio=useRef<HTMLAudioElement|null>(null)
  const [playing,setPlaying]=useState('')
  useEffect(()=>()=>{audio.current?.pause()},[])
  const speak=useCallback(async(key:string,text:string)=>{
    if(!text.trim())return
    audio.current?.pause()
    setPlaying(key)
    try{
      if(!config.current)config.current=await apiRequest<VoiceConfig>('/ai/live/reply-config').catch(()=>({}))
      const response=await fetch(api+'/api/ai/voice/preview',{method:'POST',headers:{'Content-Type':'application/json',...authHeaders()},body:JSON.stringify({text,voice:config.current.voice,rate:config.current.rate})})
      if(!response.ok){const r=await response.json().catch(()=>({}));throw new Error(typeof r.detail==='string'?r.detail:'HTTP '+response.status)}
      const url=URL.createObjectURL(await response.blob())
      const player=new Audio(url)
      audio.current=player
      const done=()=>{URL.revokeObjectURL(url);setPlaying(p=>p===key?'':p)}
      player.onended=done;player.onerror=done
      await player.play()
    }catch(e){setPlaying('');toast.push({tone:'danger',title:'Không phát được giọng đọc',description:errorText(e,'')})}
  },[toast])
  return {speak,playing}
}

export default function AnswerBankPage(){
  const toast=useToast()
  const [products,setProducts]=useState<Product[]>([])
  const [loaded,setLoaded]=useState(false)
  const [query,setQuery]=useState('')
  const [selectedId,setSelectedId]=useState<number|null>(null)
  const [tab,setTab]=useState<'bank'|'play'>('bank')
  const [draft,setDraft]=useState<Draft|null>(null)
  const [bankVersion,setBankVersion]=useState(0)
  const [formKey,setFormKey]=useState(0)
  const openDraft=useCallback((d:Draft)=>{setFormKey(k=>k+1);setDraft(d)},[])
  const speaker=useSpeaker()

  useEffect(()=>{
    let live=true
    apiRequest<Product[]>('/products?limit=500')
      .then(items=>{if(!live)return;setProducts(items);setSelectedId(id=>id??items[0]?.id??null)})
      .catch(e=>toast.push({tone:'danger',title:'Không tải được sản phẩm',description:errorText(e,'')}))
      .finally(()=>{if(live)setLoaded(true)})
    return ()=>{live=false}
  },[toast])
  const shown=useMemo(()=>{const q=query.trim().toLocaleLowerCase('vi');return q?products.filter(p=>p.name.toLocaleLowerCase('vi').includes(q)):products},[products,query])
  const selected=products.find(p=>p.id===selectedId)??null

  return <div className="ab-page">
    <aside className="ab-panel ab-products">
      <div className="ab-search"><SearchInput value={query} onChange={e=>setQuery(e.target.value)} placeholder="Tìm sản phẩm…" aria-label="Tìm sản phẩm"/></div>
      <div className="ab-count">{shown.length} / {products.length} sản phẩm</div>
      <div className="ab-list">
        {!shown.length?<div className="ab-empty"><EmptyState icon={<Package size={26}/>} title={loaded?'Chưa có sản phẩm':'Đang tải…'} description={loaded?'Sản phẩm được đồng bộ từ TikTok Shop ở trang Sản phẩm.':undefined}/></div>
        :shown.map(p=><button key={p.id} type="button" className={'ab-product'+(p.id===selectedId?' is-active':'')} onClick={()=>setSelectedId(p.id)}>
          <span className="pp-row-thumb"><Thumb src={p.image_url} name={p.name} fallback={<Package size={16}/>}/></span>
          <span className="ab-product-text"><strong>{p.name}</strong><small>{p.price==null?'Chưa có giá':vnd.format(p.price)}</small></span>
        </button>)}
      </div>
    </aside>
    <section className="ab-panel ab-main">
      {!selected?<div className="ab-empty"><EmptyState icon={<BookOpen size={28}/>} title="Chọn một sản phẩm" description="Chọn sản phẩm bên trái để xem câu hỏi khách hay hỏi và thử AI trả lời."/></div>:<>
        <header className="ab-head">
          <span className="pp-row-thumb"><Thumb src={selected.image_url} name={selected.name} fallback={<Package size={18}/>}/></span>
          <div className="ab-head-text"><strong>{selected.name}</strong><small>{selected.price==null?'Chưa có giá':vnd.format(selected.price)}</small></div>
          <SegmentedControl value={tab} onChange={setTab} items={[{value:'bank',label:'Kho hỏi đáp'},{value:'play',label:'Thử AI'}]}/>
        </header>
        {tab==='bank'
          ?<BankTab key={selected.id+':'+bankVersion} productId={selected.id} speaker={speaker} onEdit={openDraft}/>
          :<PlaygroundTab key={selected.id} productId={selected.id} speaker={speaker} onSave={openDraft}/>}
      </>}
    </section>
    {selected&&draft&&<AnswerForm key={formKey} draft={draft} productId={selected.id} onClose={()=>setDraft(null)} onSaved={()=>{setDraft(null);setBankVersion(v=>v+1);setTab('bank')}}/>}
  </div>
}

type Speaker=ReturnType<typeof useSpeaker>
const emptyDraft=():Draft=>({id:null,question:'',intent:'GENERAL',say:'',text:''})

function BankTab({productId,speaker,onEdit}:{productId:number;speaker:Speaker;onEdit:(d:Draft)=>void}){
  const toast=useToast()
  const [answers,setAnswers]=useState<Answer[]|null>(null)
  const [removing,setRemoving]=useState<Answer|null>(null)
  const [busy,setBusy]=useState(false)
  useEffect(()=>{
    let live=true
    apiRequest<BankView>('/ai/answers?product_id='+productId)
      .then(v=>{if(live)setAnswers(v.answers)})
      .catch(e=>{if(live){setAnswers([]);toast.push({tone:'danger',title:'Không tải được kho hỏi đáp',description:errorText(e,'')})}})
    return ()=>{live=false}
  },[productId,toast])
  const remove=async()=>{
    if(!removing)return
    setBusy(true)
    try{await apiRequest('/ai/answers/'+removing.id,{method:'DELETE'});setAnswers(a=>a?.filter(x=>x.id!==removing.id)??null);setRemoving(null);toast.push({tone:'success',title:'Đã xoá câu hỏi'})}
    catch(e){toast.push({tone:'danger',title:'Không xoá được',description:errorText(e,'')})}
    finally{setBusy(false)}
  }
  const groups:[string,string,Answer[]][]=[
    ['Shop tự thêm','Câu trả lời bạn viết — AI ưu tiên dùng trước.',answers?.filter(a=>a.scope==='shop')??[]],
    ['AI tự học','AI tự lưu lại từ các buổi LIVE và chuẩn bị sẵn.',answers?.filter(a=>a.scope!=='shop')??[]],
  ]
  return <div className="ab-body">
    <div className="ab-toolbar">
      <p className="ab-muted">Khi khách hỏi giống câu trong kho, AI đọc ngay câu trả lời có sẵn — nhanh và không tốn token.</p>
      <Button leftIcon={<Plus size={15}/>} onClick={()=>onEdit(emptyDraft())}>Thêm câu hỏi</Button>
    </div>
    {answers===null?<p className="ab-muted">Đang tải…</p>:!answers.length
      ?<div className="ab-empty"><EmptyState icon={<MessageSquareText size={28}/>} title="Chưa có câu hỏi nào" description="Thêm những câu khách hay hỏi (giá, size, màu…) để AI trả lời đúng ý shop." actionLabel="Thêm câu hỏi" onAction={()=>onEdit(emptyDraft())}/></div>
      :groups.filter(g=>g[2].length).map(([title,hint,items])=><div key={title} className="ab-group">
        <div className="ab-group-head"><strong>{title} ({items.length})</strong><span className="ab-muted">{hint}</span></div>
        {items.map(a=><article key={a.id} className="ab-card">
          <div className="ab-card-top">
            <strong className="ab-question">“{a.question}”</strong>
            <div className="ab-chips">
              <Badge tone="primary">{intentLabel(a.intent)}</Badge>
              <Badge>{SOURCES[a.source]??a.source}</Badge>
              {!a.approved&&<Badge tone="warning">Chưa duyệt</Badge>}
              <span className="ab-muted">Dùng {a.uses} lần</span>
            </div>
          </div>
          {a.say.map((s,i)=><div key={'s'+i} className="ab-line"><span aria-label="Câu AI sẽ nói">🔊</span><span>{s}</span></div>)}
          {a.text.map((s,i)=><div key={'t'+i} className="ab-line is-text"><span aria-label="Câu comment">💬</span><span>{s}</span></div>)}
          <div className="ab-actions">
            <Button size="sm" variant="outline" leftIcon={<Play size={13}/>} loading={speaker.playing==='a'+a.id} disabled={!a.say.length} onClick={()=>void speaker.speak('a'+a.id,a.say[0]??'')}>Nghe</Button>
            <Button size="sm" variant="ghost" leftIcon={<Pencil size={13}/>} onClick={()=>onEdit({id:a.id,question:a.question,intent:a.intent,say:a.say.join('\n'),text:a.text.join('\n')})}>Sửa</Button>
            <Button size="sm" variant="ghost" leftIcon={<Trash2 size={13}/>} onClick={()=>setRemoving(a)}>Xoá</Button>
          </div>
        </article>)}
      </div>)}
    <ConfirmModal open={removing!==null} onClose={()=>!busy&&setRemoving(null)} onConfirm={()=>void remove()} loading={busy} tone="danger"
      title="Xoá câu hỏi này?" description={removing?'“'+removing.question+'” sẽ bị xoá khỏi kho. AI sẽ không dùng câu trả lời này nữa.':''} confirmLabel="Xoá" cancelLabel="Huỷ"/>
  </div>
}

function AnswerForm({draft,productId,onClose,onSaved}:{draft:Draft;productId:number;onClose:()=>void;onSaved:()=>void}){
  const toast=useToast()
  const [form,setForm]=useState<Draft>(draft)
  const [busy,setBusy]=useState(false)
  const say=lines(form.say), text=lines(form.text)
  const sayError=!say.length?'Cần ít nhất 1 câu':say.length>MAX_SAY?'Tối đa '+MAX_SAY+' câu':say.some(l=>l.length>SAY_LEN)?'Mỗi câu tối đa '+SAY_LEN+' ký tự':''
  const textError=text.length>MAX_TEXT?'Tối đa '+MAX_TEXT+' câu':text.some(l=>l.length>TEXT_LEN)?'Mỗi câu tối đa '+TEXT_LEN+' ký tự':''
  const invalid=!form.question.trim()||!!sayError||!!textError
  const save=async()=>{
    if(invalid)return
    setBusy(true)
    const body={question:form.question.trim(),intent:form.intent,say,text}
    try{
      await (form.id==null?apiRequest('/ai/answers',json('POST',{product_id:productId,...body})):apiRequest('/ai/answers/'+form.id,json('PUT',body)))
      toast.push({tone:'success',title:form.id==null?'Đã thêm vào kho hỏi đáp':'Đã lưu thay đổi'})
      onSaved()
    }catch(e){toast.push({tone:'danger',title:'Chưa lưu được',description:errorText(e,'')})}
    finally{setBusy(false)}
  }
  const set=(k:keyof Draft,v:string)=>setForm(f=>({...f,[k]:v}))
  return <Sheet open onClose={()=>!busy&&onClose()} width={560} title={form.id==null?'Thêm câu hỏi':'Sửa câu hỏi'} footer={<div className="pp-sheet-footer">
    <Button variant="ghost" disabled={busy} onClick={onClose}>Huỷ</Button>
    <Button loading={busy} disabled={invalid} leftIcon={<Save size={15}/>} onClick={()=>void save()}>Lưu</Button>
  </div>}>
    <div className="pp-form">
      <Input label="Câu hỏi khách hay gõ" maxLength={200} value={form.question} placeholder="giá bao nhiêu vậy shop" onChange={e=>set('question',e.target.value)}/>
      <Select label="Loại câu hỏi" value={form.intent} onChange={v=>set('intent',v)} options={Object.entries(INTENTS).map(([value,label])=>({value,label}))}/>
      <p className="ab-hint">{HINT}</p>
      <Textarea label="Câu AI sẽ nói" rows={4} value={form.say} placeholder={'Dạ mẫu này giá 200k nha chị\nGiá chỉ 200k thôi ạ'}
        error={form.say&&sayError?sayError:undefined} hint={'Mỗi dòng là một cách nói (1–'+MAX_SAY+' dòng, mỗi dòng ≤ '+SAY_LEN+' ký tự) · '+say.length+' dòng'} onChange={e=>set('say',e.target.value)}/>
      <Textarea label="Câu comment (không bắt buộc)" rows={3} value={form.text} placeholder="Giá 200k ạ, chị bấm giỏ hàng số 1 nha"
        error={textError||undefined} hint={'Câu trả lời bằng chữ trong khung chat (0–'+MAX_TEXT+' dòng, mỗi dòng ≤ '+TEXT_LEN+' ký tự)'} onChange={e=>set('text',e.target.value)}/>
    </div>
  </Sheet>
}

function PlaygroundTab({productId,speaker,onSave}:{productId:number;speaker:Speaker;onSave:(d:Draft)=>void}){
  const toast=useToast()
  const [raw,setRaw]=useState('')
  const [busy,setBusy]=useState(false)
  const [result,setResult]=useState<Playground|null>(null)
  const viewers=parseComments(raw)
  const tooMany=lines(raw).length>MAX_LINES
  const addSample=(s:string)=>setRaw(r=>(r.trim()?r.replace(/\s*$/,'')+'\n':'')+s)
  const run=async()=>{
    if(!viewers.length)return
    setBusy(true)
    try{setResult(await apiRequest<Playground>('/ai/playground',json('POST',{product_id:productId,comments:viewers})))}
    catch(e){toast.push({tone:'danger',title:'Không thử được AI',description:errorText(e,'')})}
    finally{setBusy(false)}
  }
  const copy=(s:string)=>void navigator.clipboard.writeText(s).then(()=>toast.push({tone:'success',title:'Đã copy'}),()=>toast.push({tone:'danger',title:'Không copy được'}))
  const tokens=result?.usage.reduce((n,u)=>n+u.input_tokens+u.output_tokens,0)??0
  return <div className="ab-body">
    <p className="ab-muted">Gõ thử bình luận như khách trên LIVE để xem AI sẽ nói gì — không cần mở LIVE, không đọc ra OBS.</p>
    <Textarea label="Bình luận thử" rows={5} value={raw} placeholder={'giá bn shop\nLan: 55kg mặc size gì'}
      hint={'Mỗi dòng một bình luận (tối đa '+MAX_LINES+'). Muốn đặt tên khách thì viết “Tên: nội dung”.'} error={tooMany?'Chỉ gửi '+MAX_LINES+' dòng đầu':undefined} onChange={e=>setRaw(e.target.value)}/>
    <div className="ab-samples">
      <span className="ab-muted">Thêm nhanh:</span>
      {SAMPLES.map(s=><button key={s} type="button" className="ab-sample" onClick={()=>addSample(s)}>{s}</button>)}
    </div>
    <div className="ab-toolbar">
      <Button loading={busy} disabled={!viewers.length} leftIcon={<Sparkles size={15}/>} onClick={()=>void run()}>Xem AI trả lời</Button>
      {raw&&<Button variant="ghost" disabled={busy} onClick={()=>{setRaw('');setResult(null)}}>Xoá hết</Button>}
    </div>
    {result&&<div className="ab-results">
      {result.error&&<div className="ab-error"><strong>Câu cần AI chưa trả lời được: hãy bật AI trong AI Assistant.</strong><small>{result.error}</small></div>}
      {!result.replies.length&&!result.error&&<p className="ab-muted">Không có bình luận nào cần trả lời.</p>}
      {result.replies.map(r=>{const src=REPLY_SOURCES[r.source]??{label:r.source,tone:'default' as BadgeTone},route=ROUTES[r.route]??{label:r.route,tone:'default' as BadgeTone}
        return <article key={r.id} className="ab-card">
          <div className="ab-viewers">{r.comments.map((c,i)=><div key={i}><b>{c.nickname||c.username}:</b> {c.comment}</div>)}</div>
          <div className="ab-chips"><Badge tone={src.tone}>{src.label}</Badge><Badge tone="primary">{intentLabel(r.intent)}</Badge><Badge tone={route.tone}>{route.label}</Badge></div>
          {r.route==='review'&&r.review_reasons.length>0&&<ul className="ab-reasons">{r.review_reasons.map((x,i)=><li key={i}>{x}</li>)}</ul>}
          {r.spoken_text&&<div className="ab-line"><span aria-label="AI sẽ nói">🔊</span><span>{r.spoken_text}</span>
            <Button size="sm" variant="outline" leftIcon={<Play size={13}/>} loading={speaker.playing==='r'+r.id} onClick={()=>void speaker.speak('r'+r.id,r.spoken_text)}>Nghe</Button></div>}
          {r.comment_text&&<div className="ab-line is-text"><span aria-label="Câu comment">💬</span><span>{r.comment_text}</span>
            <Button size="sm" variant="ghost" leftIcon={<Copy size={13}/>} onClick={()=>copy(r.comment_text)}>Copy</Button></div>}
          <div className="ab-actions"><Button size="sm" variant="outline" leftIcon={<Save size={13}/>} onClick={()=>onSave({id:null,question:r.question,intent:r.intent in INTENTS?r.intent:'GENERAL',say:r.say,text:r.text})}>Lưu vào kho</Button></div>
        </article>})}
      {result.skipped.length>0&&<div className="ab-skipped"><strong>Bỏ qua ({result.skipped.length})</strong>
        {result.skipped.map((s,i)=><div key={i}>{s.username}: “{s.comment}” — {s.reason}</div>)}</div>}
      <p className="ab-muted">Token đã dùng: {tokens.toLocaleString('vi-VN')}{result.usage.length?' · '+result.usage.length+' lần gọi AI':' · không gọi AI'}</p>
    </div>}
  </div>
}
