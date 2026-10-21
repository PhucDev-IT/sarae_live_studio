/**
 * App-wide upload queue for the Media Library. It lives outside React, so uploads keep running and keep their
 * progress when the user switches page; any page (and the floating upload dock) can subscribe to it.
 *
 * Unfinished uploads are remembered (file name/size/date → client_upload_id) in localStorage: after a reload the
 * browser no longer has the File, but choosing the same file again resumes on the Backend from the stored offset.
 */
import { useSyncExternalStore } from 'react'
import { cancelUpload, putCover, uploadVideo } from './cloudMediaApi'
import { errorText } from './mediaApi'
import { captureFrame } from './videoFrame'

export type UploadState = 'queued'|'uploading'|'finishing'|'done'|'error'|'cancelled'
export type UploadTask = { key:string; name:string; size:number; sent:number; state:UploadState; error?:string; mediaId?:string; resumed:boolean }
type Internal = UploadTask & { file:File; clientId:string; uploadId?:string; controller?:AbortController }
type Pending = { clientId:string; name:string; size:number; lastModified:number }

const PENDING_KEY = 'sarae-pending-uploads'
const CONCURRENCY = 2
let tasks:Internal[] = []
let snapshot:UploadTask[] = []
const listeners = new Set<()=>void>()
const mediaListeners = new Set<(mediaId:string)=>void>()

const newId = () => (globalThis.crypto?.randomUUID?.() ?? `${Date.now()}${Math.random()}`).replace(/[^A-Za-z0-9]/g, '').slice(0, 32)
const fileKey = (f:{name:string; size:number; lastModified:number}) => `${f.name}|${f.size}|${f.lastModified}`

function readPending():Record<string, Pending>{
  try { return JSON.parse(localStorage.getItem(PENDING_KEY) || '{}') as Record<string, Pending> } catch { return {} }
}
function writePending(map:Record<string, Pending>){
  try { localStorage.setItem(PENDING_KEY, JSON.stringify(map)) } catch { /* resume after reload is a convenience */ }
}

function emit(){
  snapshot = tasks.map(({key, name, size, sent, state, error, mediaId, resumed})=>({key, name, size, sent, state, error, mediaId, resumed}))
  listeners.forEach(fn=>fn())
}
function patch(key:string, values:Partial<Internal>){
  tasks = tasks.map(t=>t.key === key ? {...t, ...values} : t)
  emit()
}

async function run(task:Internal){
  const controller = new AbortController()
  patch(task.key, {state:'uploading', error:undefined, controller})
  // Read the cover frame locally first: it is instant and does not depend on the storage processing the video.
  const cover = await captureFrame(task.file).catch(()=>null)
  try {
    const result = await uploadVideo(task.file, task.clientId, sent=>patch(task.key, {sent}), controller.signal, uploadId=>patch(task.key, {uploadId}))
    patch(task.key, {state:'finishing', sent:task.size})
    if (result.media_id && cover?.blob) await putCover(result.media_id, cover.blob, cover).catch(()=>undefined)
    const pending = readPending(); delete pending[fileKey(task.file)]; writePending(pending)
    patch(task.key, {state:'done', mediaId:result.media_id ?? undefined, controller:undefined})
    if (result.media_id) mediaListeners.forEach(fn=>fn(result.media_id as string))
  } catch (error) {
    if (controller.signal.aborted) return
    patch(task.key, {state:'error', error:errorText(error, 'Upload thất bại'), controller:undefined})
  } finally {
    pump()
  }
}

function pump(){
  const active = tasks.filter(t=>t.state === 'uploading' || t.state === 'finishing').length
  tasks.filter(t=>t.state === 'queued').slice(0, Math.max(0, CONCURRENCY - active)).forEach(t=>void run(t))
}

export const uploads = {
  add(files:FileList|File[]){
    const pending = readPending()
    for (const file of Array.from(files)) {
      const key = fileKey(file)
      if (tasks.some(t=>fileKey(t.file) === key && t.state !== 'cancelled' && t.state !== 'done')) continue
      const remembered = pending[key]
      const clientId = remembered?.clientId ?? newId()
      pending[key] = {clientId, name:file.name, size:file.size, lastModified:file.lastModified}
      tasks = [...tasks, {key:newId(), name:file.name, size:file.size, sent:0, state:'queued', file, clientId, resumed:!!remembered}]
    }
    writePending(pending)
    emit()
    pump()
  },
  cancel(key:string){
    const task = tasks.find(t=>t.key === key)
    if (!task) return
    task.controller?.abort()
    if (task.uploadId) void cancelUpload(task.uploadId).catch(()=>undefined)
    const pending = readPending(); delete pending[fileKey(task.file)]; writePending(pending)
    patch(key, {state:'cancelled', controller:undefined})
    pump()
  },
  retry(key:string){ patch(key, {state:'queued', error:undefined}); pump() },
  dismiss(key:string){ tasks = tasks.filter(t=>t.key !== key); emit() },
  clearFinished(){ tasks = tasks.filter(t=>t.state !== 'done' && t.state !== 'cancelled'); emit() },
  /** Uploads interrupted by a reload/closed tab that the user can resume by choosing the same file again. */
  interrupted():Pending[]{
    const open = new Set(tasks.map(t=>fileKey(t.file)))
    return Object.entries(readPending()).filter(([key])=>!open.has(key)).map(([, p])=>p)
  },
  forget(p:Pending){ const pending = readPending(); delete pending[fileKey(p)]; writePending(pending); emit() },
  busy:()=>tasks.some(t=>t.state === 'queued' || t.state === 'uploading' || t.state === 'finishing'),
  subscribe(fn:()=>void){ listeners.add(fn); return ()=>{listeners.delete(fn)} },
  /** Called with the new media id each time an upload completes (pages refresh their list). */
  onMediaAdded(fn:(mediaId:string)=>void){ mediaListeners.add(fn); return ()=>{mediaListeners.delete(fn)} },
}

export const useUploads = () => useSyncExternalStore(uploads.subscribe, ()=>snapshot)

window.addEventListener('beforeunload', event=>{
  if (uploads.busy()) event.preventDefault()
})
