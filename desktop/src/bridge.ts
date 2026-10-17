import {Command, type Child} from '@tauri-apps/plugin-shell';
type Packet={id?:number;ok?:boolean;data?:unknown;error?:string;event?:string;message?:string};
type Listener=(packet:Packet)=>void;
let child:Child|undefined;
let starting:Promise<void>|undefined;
let seq=0;
const pending=new Map<number,{resolve:(value:any)=>void;reject:(error:Error)=>void;timer:ReturnType<typeof setTimeout>}>();
const subscribers=new Set<Listener>();
function publish(packet:Packet){for(const listener of subscribers)listener(packet)}
export function subscribe(listener:Listener){subscribers.add(listener);return()=>{subscribers.delete(listener)}}
export async function connect(){
 if(child)return;
 if(starting)return starting;
 starting=(async()=>{
  const cmd=Command.sidecar('binaries/sarae-agent-core');
  let buffer='';
  cmd.stdout.on('data',chunk=>{
   buffer+=chunk;
   const lines=buffer.split(/\r?\n/);buffer=lines.pop()||'';
   for(const line of lines){
    if(!line.startsWith('SARAE_IPC:')){if(line.trim())publish({event:'log',message:line});continue}
    try{const packet=JSON.parse(line.slice('SARAE_IPC:'.length)) as Packet;
      if(typeof packet.id==='number'&&pending.has(packet.id)){
       const p=pending.get(packet.id)!;clearTimeout(p.timer);pending.delete(packet.id);
       packet.ok?p.resolve(packet.data):p.reject(new Error(packet.error||'Lỗi Agent'));
      }else publish(packet)
    }catch{publish({event:'log',message:line})}
   }
  });
  cmd.stderr.on('data',chunk=>publish({event:'log',message:String(chunk)}));
  cmd.on('close',()=>{
   child=undefined;starting=undefined;
   for(const p of pending.values()){clearTimeout(p.timer);p.reject(new Error('Agent Core đã dừng'))}
   pending.clear();publish({event:'stopped'});
  });
  cmd.on('error',error=>publish({event:'error',message:String(error)}));
  child=await cmd.spawn();
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
export async function shutdown(){try{if(child)await call('shutdown',{},22000)}catch{/* close below */}try{await child?.kill()}catch{/* already stopped */}child=undefined}
