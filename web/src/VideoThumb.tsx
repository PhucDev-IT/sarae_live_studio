import { useEffect, useRef, useState } from 'react'
import { Film } from 'lucide-react'
import { fileUrl } from './mediaApi'

/** Metadata read by the browser from the video file, used when the backend has not probed it yet. */
export type ClientMeta = { duration?:number; width?:number; height?:number; missing?:boolean }

/** Shows the video's first frame, loading it only while the thumbnail is near the viewport. */
export default function VideoThumb({id,onMeta}:{id:number;onMeta?:(meta:ClientMeta)=>void}){
  const ref = useRef<HTMLDivElement>(null)
  const [near,setNear] = useState(false)
  const [failed,setFailed] = useState(false)
  useEffect(()=>{
    const el = ref.current
    if (!el) return
    const observer = new IntersectionObserver(([entry])=>setNear(entry.isIntersecting), {rootMargin:'200px'})
    observer.observe(el)
    return ()=>observer.disconnect()
  },[])
  return <div ref={ref} className="media-thumb-frame">
    {near && !failed
      ? <video src={fileUrl(id) + '#t=0.5'} preload="metadata" muted playsInline tabIndex={-1}
        onLoadedMetadata={e=>{const v = e.currentTarget;onMeta?.({duration:v.duration,width:v.videoWidth,height:v.videoHeight,missing:false})}}
        onError={()=>{setFailed(true);onMeta?.({missing:true})}}/>
      : <Film size={26}/>}
  </div>
}
