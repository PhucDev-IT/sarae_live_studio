/** "Sao lưu & nhập": download all knowledge as a JSONL file and import (merge) one back, with a dry-run check first. */
import {useRef,useState} from 'react'
import {Button,ConfirmModal,Select,Textarea,useToast} from './ui/core'
import {Download,FileUp,ShieldCheck,Upload} from 'lucide-react'
import {api,apiRequest,errorText} from './mediaApi'
import {authHeaders} from './authToken'
import './knowledge.css'

type ImportResult={dry_run:boolean;added:Record<string,number>;updated:Record<string,number>;skipped:number;errors:{line:number;error:string}[]}
const TYPES:[string,string][]=[['template','Mẫu'],['policy','Chính sách'],['product_facts','Dữ liệu sản phẩm'],['product_qa','Hỏi đáp sản phẩm']]
const MAX_SHOWN=2_000_000

export default function KnowledgeBackup(){
  const toast=useToast()
  const fileInput=useRef<HTMLInputElement>(null)
  const [downloading,setDownloading]=useState(false)
  const [raw,setRaw]=useState('')
  const [fileName,setFileName]=useState('')
  const [mode,setMode]=useState('merge')
  const [checked,setChecked]=useState<{text:string;result:ImportResult}|null>(null)
  const [done,setDone]=useState<ImportResult|null>(null)
  const [busy,setBusy]=useState<''|'check'|'import'>('')
  const [confirming,setConfirming]=useState(false)

  const download=async()=>{
    setDownloading(true)
    try{
      const response=await fetch(api+'/api/ai/knowledge/export',{headers:authHeaders()})
      if(!response.ok){const r=await response.json().catch(()=>({}));throw new Error(typeof r.detail==='string'?r.detail:'HTTP '+response.status)}
      const url=URL.createObjectURL(new Blob([await response.text()],{type:'application/x-ndjson;charset=utf-8'}))
      const a=document.createElement('a')
      a.href=url;a.download='sarae-knowledge-'+new Date().toLocaleDateString('sv-SE')+'.jsonl'
      document.body.appendChild(a);a.click();a.remove()
      window.setTimeout(()=>URL.revokeObjectURL(url),1000)
    }catch(e){toast.push({tone:'danger',title:'Không tải được bản sao lưu',description:errorText(e,'')})}
    finally{setDownloading(false)}
  }
  const pickFile=async(file:File|undefined)=>{
    if(!file)return
    try{
      const text=await file.text()
      if(text.length>MAX_SHOWN)throw new Error('File quá lớn (tối đa khoảng 2 MB)')
      setRaw(text);setFileName(file.name);setChecked(null);setDone(null)
    }catch(e){toast.push({tone:'danger',title:'Không đọc được file',description:errorText(e,'')})}
    finally{if(fileInput.current)fileInput.current.value=''}
  }
  const send=async(dryRun:boolean)=>{
    const text=raw
    setBusy(dryRun?'check':'import')
    try{
      const result=await apiRequest<ImportResult>('/ai/knowledge/import?dry_run='+dryRun,{method:'POST',headers:{'Content-Type':'text/plain'},body:text})
      if(dryRun){setChecked({text,result});setDone(null)}
      else{setDone(result);setChecked(null);toast.push({tone:'success',title:'Đã nhập dữ liệu',description:total(result.added)+' thêm mới · '+total(result.updated)+' cập nhật'})}
    }catch(e){toast.push({tone:'danger',title:dryRun?'Không kiểm tra được':'Không nhập được',description:errorText(e,'')})}
    finally{setBusy('');setConfirming(false)}
  }
  const ready=checked!==null&&checked.text===raw
  const hasErrors=!!checked?.result.errors.length
  const importNow=()=>hasErrors?setConfirming(true):void send(false)
  const result=done??(ready&&checked?checked.result:null)

  return <section className="ab-panel kn-panel">
    <div className="ab-body">
      <div className="kn-box">
        <strong>Sao lưu</strong>
        <p className="ab-muted">Tải toàn bộ mẫu chung, chính sách shop, dữ liệu và hỏi đáp sản phẩm về máy (1 file .jsonl). Nên tải trước khi nhập hoặc sửa nhiều.</p>
        <div><Button variant="outline" leftIcon={<Download size={15}/>} loading={downloading} onClick={()=>void download()}>Tải bản sao lưu</Button></div>
      </div>
      <div className="kn-box">
        <strong>Nhập dữ liệu</strong>
        <p className="ab-muted">Chọn file .jsonl / .txt hoặc dán nội dung. Luôn bấm “Kiểm tra trước” để xem sẽ thêm/cập nhật gì; dữ liệu cũ không bị xoá.</p>
        <div className="kn-import-head">
          <input ref={fileInput} type="file" accept=".jsonl,.txt,.json,text/plain" hidden onChange={e=>void pickFile(e.target.files?.[0])}/>
          <Button variant="outline" leftIcon={<FileUp size={15}/>} onClick={()=>fileInput.current?.click()}>Chọn file</Button>
          {fileName&&<span className="ab-muted">{fileName}</span>}
          <div className="kn-select"><Select value={mode} onChange={setMode} options={[{value:'merge',label:'Gộp: thêm mới, cập nhật theo id/tên'}]}/></div>
        </div>
        <Textarea label="Nội dung" rows={8} value={raw} placeholder={'{"type":"policy","key":"ship_time","value":"2-4 ngày"}'}
          hint={raw?raw.split('\n').filter(l=>l.trim()).length+' dòng':'Mỗi dòng là một mục (JSON).'} onChange={e=>{setRaw(e.target.value);setFileName('')}}/>
        <div className="ab-actions">
          <Button variant="outline" leftIcon={<ShieldCheck size={15}/>} loading={busy==='check'} disabled={!raw.trim()||busy!==''} onClick={()=>void send(true)}>Kiểm tra trước</Button>
          <Button leftIcon={<Upload size={15}/>} loading={busy==='import'} disabled={!ready||busy!==''} onClick={importNow}>Nhập thật</Button>
          {checked&&!ready&&<span className="kn-warn">Nội dung đã thay đổi, hãy kiểm tra lại.</span>}
        </div>
        {result&&<div className="kn-result">
          <strong>{done?'Kết quả nhập':'Kết quả kiểm tra (chưa lưu gì)'}</strong>
          <table className="kn-summary">
            <thead><tr><th>Loại</th><th>Thêm</th><th>Cập nhật</th></tr></thead>
            <tbody>{TYPES.map(([k,label])=><tr key={k}><td>{label}</td><td>{result.added[k]??0}</td><td>{result.updated[k]??0}</td></tr>)}</tbody>
          </table>
          {result.skipped>0&&<p className="ab-muted">Bỏ qua: {result.skipped} dòng</p>}
          {result.errors.length>0&&<div className="kn-errors">
            <strong>Dòng lỗi ({result.errors.length})</strong>
            <ul>{result.errors.slice(0,200).map((x,i)=><li key={i}><b>Dòng {x.line}:</b> {x.error}</li>)}</ul>
          </div>}
          {!done&&!result.errors.length&&<p className="kn-ok">Không có lỗi. Bấm “Nhập thật” để lưu.</p>}
        </div>}
      </div>
      <div className="kn-box kn-help">
        <strong>Hướng dẫn: <code>docs/AI_KNOWLEDGE_GUIDE.md</code></strong>
        <p className="ab-muted">Dành cho người kỹ thuật, chạy trong thư mục backend:</p>
        <code>python -m app.knowledge_cli validate FILE</code>
        <code>python -m app.knowledge_cli import --owner EMAIL FILE --dry-run</code>
      </div>
    </div>
    <ConfirmModal open={confirming} onClose={()=>busy===''&&setConfirming(false)} onConfirm={()=>void send(false)} loading={busy==='import'} tone="primary"
      title="Nhập dù có dòng lỗi?" description="Các dòng lỗi sẽ bị bỏ qua, các dòng đúng vẫn được nhập." confirmLabel="Nhập thật" cancelLabel="Huỷ"/>
  </section>
}

function total(r:Record<string,number>){return Object.values(r).reduce((n,v)=>n+v,0)}
