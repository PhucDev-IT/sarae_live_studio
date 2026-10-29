/** Media Library videos usable as the main video of a Single Video LIVE session. */
import { useEffect, useState } from 'react'
import { durationLabel } from './mediaApi'
import { listCloudMedia, type CloudMedia } from './cloudMediaApi'

/** Available videos of the Media Library, loaded while `enabled`. */
export function useLibraryVideos(enabled = true){
  const [videos,setVideos] = useState<CloudMedia[]>([])
  useEffect(()=>{
    if (!enabled) return
    let active = true
    void listCloudMedia().then(r=>{if (active) setVideos(r.items.filter(v=>v.available))}).catch(()=>{if (active) setVideos([])})
    return ()=>{active = false}
  },[enabled])
  return videos
}

export const videoOptions = (videos:CloudMedia[]) => videos.map(v=>({value:v.id, label:`${v.display_name || v.filename}${v.duration_seconds ? ' · ' + durationLabel(v.duration_seconds) : ''}`}))
