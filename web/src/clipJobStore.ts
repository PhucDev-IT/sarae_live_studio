/**
 * App-wide clip jobs (cutting a library video into new videos on the server). Polled outside React, like the upload
 * queue, so the progress keeps going and stays visible when the cutter is closed or the user changes page.
 */
import { useSyncExternalStore } from 'react'
import { getClipJob, type ClipJob } from './cloudMediaApi'
import { errorText } from './mediaApi'

/** `lost`: polling gave up (network/server restart); the job may still finish on the server. */
export type TrackedClipJob = { job:ClipJob; name:string; startedAt:number; lost?:string }

const POLL_MS = 1200
const MAX_FAILURES = 5
let jobs:TrackedClipJob[] = []
const listeners = new Set<()=>void>()
const finishListeners = new Set<(job:ClipJob)=>void>()

export const isFinal = (job:ClipJob) => job.status === 'DONE' || job.status === 'FAILED'

function update(id:string, values:Partial<TrackedClipJob>){
  jobs = jobs.map(t=>t.job.id === id ? {...t, ...values} : t)
  listeners.forEach(fn=>fn())
}

async function poll(id:string){
  let failures = 0
  for (;;) {
    await new Promise(resolve=>window.setTimeout(resolve, POLL_MS))
    if (!jobs.some(t=>t.job.id === id)) return  // dismissed
    try {
      const job = await getClipJob(id)
      failures = 0
      update(id, {job, lost:undefined})
      if (isFinal(job)) {finishListeners.forEach(fn=>fn(job));return}
    } catch (error) {
      if (++failures >= MAX_FAILURES) {update(id, {lost:errorText(error, 'Mất kết nối tới máy chủ')});return}
    }
  }
}

export const clipJobs = {
  track(job:ClipJob, name:string){
    jobs = [...jobs.filter(t=>t.job.id !== job.id), {job, name, startedAt:Date.now()}]
    listeners.forEach(fn=>fn())
    void poll(job.id)
  },
  /** Poll again after a lost connection. */
  resume(id:string){ update(id, {lost:undefined}); void poll(id) },
  dismiss(id:string){ jobs = jobs.filter(t=>t.job.id !== id); listeners.forEach(fn=>fn()) },
  clearFinished(){ jobs = jobs.filter(t=>!isFinal(t.job) && !t.lost); listeners.forEach(fn=>fn()) },
  running:()=>jobs.some(t=>!isFinal(t.job) && !t.lost),
  subscribe(fn:()=>void){ listeners.add(fn); return ()=>{listeners.delete(fn)} },
  /** Called once per job when it ends (DONE or FAILED): pages refresh their list. */
  onFinished(fn:(job:ClipJob)=>void){ finishListeners.add(fn); return ()=>{finishListeners.delete(fn)} },
}

export const useClipJobs = () => useSyncExternalStore(clipJobs.subscribe, ()=>jobs)

window.addEventListener('beforeunload', event=>{
  if (clipJobs.running()) event.preventDefault()  // the server keeps cutting, but this tab would lose the progress
})
