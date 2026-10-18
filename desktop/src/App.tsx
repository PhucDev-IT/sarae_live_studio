import{useEffect,useState}from'react';
import{open}from'@tauri-apps/plugin-dialog';
import{getCurrentWindow}from'@tauri-apps/api/window';
import{call,connect,subscribe,shutdown}from'./bridge';
import{Activity,Cloud,FolderOpen,KeyRound,MonitorPlay,Play,Power,RefreshCw,Settings2,Square,Video}from'lucide-react';
type State={agent_id:string|null;running:boolean;cloud_url:string;folders:string[]};
const initial:State={agent_id:null,running:false,cloud_url:'',folders:[]};
export default function App(){
 const[state,setState]=useState<State>(initial),[url,setUrl]=useState(''),[key,setKey]=useState(''),[busy,setBusy]=useState(false),[logs,setLogs]=useState<string[]>([]),[count,setCount]=useState<number|null>(null),[status,setStatus]=useState('Đang kết nối Agent Core...');
 const log=(message:string)=>setLogs(old=>[...old.slice(-200),new Date().toLocaleTimeString('vi-VN')+' · '+message]);
 const sync=async()=>{const info=await call<State>('status');setState(info);setUrl(info.cloud_url||'');return info};
 useEffect(()=>{
  let disposed=false;
  let unlisten:(()=>void)|undefined;
  void getCurrentWindow().onCloseRequested(async event=>{
   if(disposed)return;
   event.preventDefault();
   await shutdown();
   disposed=true;
   await getCurrentWindow().destroy();
  }).then(fn=>{unlisten=fn}).catch(e=>log('Không đăng ký được xử lý thoát: '+String(e)));
  const unsubscribe=subscribe(packet=>{
  if(packet.event==='ready'){log('Python Agent Core đã sẵn sàng');void sync().catch(e=>log(String(e)))}
  else if(packet.event==='started'){setStatus('Đang chạy');setState(old=>({...old,running:true}));log('Agent đã khởi động')}
  else if(packet.event==='stopped'){setStatus('Đã dừng');setState(old=>({...old,running:false}));log('Agent đã dừng')}
  else if(packet.event==='log'||packet.event==='error')log(packet.message||'Lỗi không xác định');
 });void connect().then(()=>sync()).then(info=>setStatus(info.running?'Đang chạy':'Sẵn sàng')).catch(e=>{setStatus('Không thể khởi động Agent Core');log(String(e))});return()=>{unsubscribe();unlisten?.()}},[]);
 const action=async<T,>(label:string,fn:()=>Promise<T>)=>{setBusy(true);try{return await fn()}catch(e){log('Lỗi '+label+': '+String(e));return undefined}finally{setBusy(false)}};
 const configure=()=>action('Cloud',async()=>{const result=await call<State>('configure_cloud',{url});setState(result);log('Đã lưu Cloud URL')});
 const activate=()=>action('kích hoạt',async()=>{if(state.running)throw new Error('Hãy dừng Agent trước khi kích hoạt');await call('configure_cloud',{url});const result=await call<State>('activate',{key});setState(result);setKey('');log('Kích hoạt thành công · '+result.agent_id)});
 const choose=()=>action('thêm thư mục',async()=>{const selected=await open({directory:true,multiple:false,title:'Chọn thư mục chứa video MP4'});if(typeof selected==='string'){const result=await call<State>('add_folder',{path:selected});setState(result);log('Đã thêm '+selected)}});
 const remove=(path:string)=>action('xóa thư mục',async()=>{setState(await call<State>('remove_folder',{path}))});
 const scan=()=>action('quét video',async()=>{const result=await call<{count:number}>('scan',{},120000);setCount(result.count);log('Đã quét '+result.count+' video MP4')});
 // The worker's 'stopped' event can arrive before this reply, so derive the label from the reply itself.
 const toggle=()=>action('Agent',async()=>{const wasRunning=state.running;const next=await call<State>(wasRunning?'stop':'start');setState(next);setStatus(next.running?(wasRunning?'Đang dừng...':'Đang chạy'):'Đã dừng');log(wasRunning?'Đã yêu cầu dừng':'Đã yêu cầu chạy')});
 return <div className="app">
  <aside className="sidebar"><div className="logo"><div className="logo-mark">S</div><div><strong>SARAE</strong><small>WINDOWS AGENT</small></div></div>
   <div className="sidebar-label">THIẾT BỊ</div><div className="nav active"><MonitorPlay size={18}/> Tổng quan</div>
   <div className="nav"><FolderOpen size={18}/> Media Local</div><div className="nav"><Activity size={18}/> Trạng thái & Log</div>
   <div className="sidebar-foot"><span className={'dot '+(state.running?'online':'')}/>{state.running?'Core đang chạy':'Core đã dừng'}</div>
  </aside>
  <main className="content">
   <header><div><div className="eyebrow">SARAE / LOCAL AGENT</div><h1>Windows Agent</h1><p>Kết nối Cloud, theo dõi máy LIVE và quản lý video lưu tại Windows.</p></div><div className={'pill '+(state.running?'good':'')}>{state.running?'Đang hoạt động':'Chưa chạy'}</div></header>
   <section className="cards">
    <article className="metric"><Cloud/><span>Cloud Backend</span><strong>{state.cloud_url?'Đã cấu hình':'Chưa cấu hình'}</strong></article>
    <article className="metric"><KeyRound/><span>Kích hoạt</span><strong>{state.agent_id?'Đã kích hoạt':'Chưa kích hoạt'}</strong></article>
    <article className="metric"><FolderOpen/><span>Thư mục video</span><strong>{state.folders.length} thư mục</strong></article>
    <article className="metric"><Video/><span>Video local</span><strong>{count===null?'Chưa quét':count+' video'}</strong></article>
   </section>
   <section className="split">
    <div className="panel"><div className="panel-title"><Cloud size={19}/><h2>Kết nối và kích hoạt</h2></div>
     <label>Cloud API URL</label><div className="input-row"><input value={url} onChange={e=>setUrl(e.target.value)} placeholder="https://api.example.com/api"/><button onClick={()=>void configure()} disabled={busy||state.running}><Settings2 size={16}/> Lưu</button></div>
     <label>Mã kích hoạt lần đầu</label><div className="input-row"><input type="password" value={key} disabled={!!state.agent_id} onChange={e=>setKey(e.target.value)} placeholder={state.agent_id?'Thiết bị đã kích hoạt':'SARAE-ACT-...'}/><button disabled={busy||!!state.agent_id||!key.trim()} onClick={()=>void activate()}><KeyRound size={16}/> Kích hoạt</button></div>
     <div className="device-id">Agent ID <code>{state.agent_id||'Chưa có'}</code></div>
     <div className="actions"><button className="primary" disabled={busy||!state.agent_id} onClick={()=>void toggle()}>{state.running?<><Square size={16}/> Dừng Agent</>:<><Play size={16}/> Bắt đầu Agent</>}</button><span>{status}</span></div>
    </div>
    <div className="panel"><div className="panel-title"><FolderOpen size={19}/><h2>Thư viện video local</h2></div>
     <p className="hint">Agent quét video trên ổ cứng; Cloud chỉ nhận metadata, không tải lên MP4.</p>
     <div className="folder-list">{state.folders.length?state.folders.map(folder=><div key={folder} className="folder"><FolderOpen size={15}/><span title={folder}>{folder}</span><button onClick={()=>void remove(folder)} disabled={busy} title="Bỏ thư mục">×</button></div>):<div className="empty">Chưa thêm thư mục video</div>}</div>
     <div className="actions"><button onClick={()=>void choose()} disabled={busy}><FolderOpen size={16}/> Thêm thư mục</button><button onClick={()=>void scan()} disabled={busy}><RefreshCw size={16}/> Quét MP4</button></div>
    </div>
   </section>
   <section className="panel logs"><div className="panel-title"><Activity size={19}/><h2>Nhật ký Agent</h2><div className="spacer"/><span>{logs.length} sự kiện</span><button onClick={()=>setLogs([])}>Xóa màn hình</button></div>
    <div className="log-list">{logs.length?logs.map((entry,i)=><div key={i}>{entry}</div>):<p>Chưa có sự kiện</p>}</div>
   </section>
   <footer><span><Power size={14}/> Agent Core: Python · GUI: Tauri + React</span><span>v0.1.0 · Developer Preview</span></footer>
  </main>
 </div>
}
