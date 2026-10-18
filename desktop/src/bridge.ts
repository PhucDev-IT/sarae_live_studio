import {Child, Command} from '@tauri-apps/plugin-shell';
type Packet={id?:number;ok?:boolean;data?:unknown;error?:string;event?:string;message?:string};
type Listener=(packet:Packet)=>void;
let child:Child|undefined;
let starting:Promise<void>|undefined;
let seq=0;
const pending=new Map<number,{resolve:(value:any)=>void;reject:(error:Error)=>void;timer:ReturnType<typeof setTimeout>}>();
const subscribers=new Set<Listener>();
function publish(packet:Packet){for(const listener of subscribers)listener(packet)}
export function subscribe(listener:Listener){subscribers.add(listener);return()=>{subscribers.delete(listener)}}
// The Rust side keeps the sidecar alive across WebView reloads (dev HMR, F5); remember its pid so a
// reloaded page stops it instead of leaving a second Agent Core running.
const PID_KEY='sarae-agent-core-pid';
function rememberPid(pid?:number){try{pid?sessionStorage.setItem(PID_KEY,String(pid)):sessionStorage.removeItem(PID_KEY)}catch{/* storage unavailable */}}
async function killOrphan(){
 let pid:number|undefined;
 try{pid=Number(sessionStorage.getItem(PID_KEY))||undefined}catch{/* storage unavailable */}
 if(pid){try{await new Child(pid).kill()}catch{/* already exited */}rememberPid()}
}
export async function connect(){
 if(child)return;
 if(starting)return starting;
 starting=(async()=>{
  await killOrphan();
  const cmd=Command.sidecar('binaries/sarae-agent-core');
  // Shell plugin emits stdout line by line (with or without the line ending, depending on version).
  cmd.stdout.on('data',chunk=>{
   for(const line of String(chunk).split(/\r?\n/)){
    if(!line)continue;
    if(!line.startsWith('SARAE_IPC:')){if(line.trim())publish({event:'log',message:line});continue}
    try{const packet=JSON.parse(line.slice('SARAE_IPC:'.length)) as Packet;
      if(typeof packet.id==='number'&&pending.has(packet.id)){
       const p=pending.get(packet.id)!;clearTimeout(p.timer);pending.delete(packet.id);
       packet.ok?p.resolve(packet.data):p.reject(new Error(packet.error||'Lỗi Agent'));
      }else publish(packet)
    }catch{publish({event:'log',message:line})}
   }
  });
  cmd.stderr.on('data',chunk=>{const message=String(chunk).trimEnd();if(message)publish({event:'log',message})});
  cmd.on('close',()=>{
   child=undefined;starting=undefined;rememberPid();
   for(const p of pending.values()){clearTimeout(p.timer);p.reject(new Error('Agent Core đã dừng'))}
   pending.clear();publish({event:'stopped'});
  });
  cmd.on('error',error=>publish({event:'error',message:String(error)}));
  child=await cmd.spawn();
  rememberPid(child.pid);
 })();
 try{await starting}finally{starting=undefined}
}
export async function call<T=unknown>(method:string,params:Record<string,unknown>={},timeout=25000):Promise<T>{
 await connect();
 return new Promise((resolve,reject)=>{
  const id=++seq;
  const timer=setTimeout(()=>{pending.delete(id);reject(new Error('Agent không phản hồi'))},timeout);
  pending.set(id,{resolve,reject,timer});
  child!.write(JSON.stringify({id,method,params})+'\n').catch(error=>{clearTimeout(timer);pending.delete(id);reject(error)})
 })
}
export async function shutdown(){try{if(child)await call('shutdown',{},22000)}catch{/* close below */}try{await child?.kill()}catch{/* already stopped */}child=undefined;rememberPid()}
