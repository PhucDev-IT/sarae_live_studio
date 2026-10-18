import { useState } from 'react'
import { Film } from 'lucide-react'
import { assetUrl } from './mediaApi'

/** Thumbnail the Windows Agent made for a video (the MP4 itself stays on that PC); a film icon when there is none. */
export default function VideoThumb({src}:{src?:string|null}){
  const url = assetUrl(src)
  const [failed,setFailed] = useState('')
  return <div className="media-thumb-frame">
    {url && failed !== url ? <img src={url} alt="" loading="lazy" onError={()=>setFailed(url)}/> : <Film size={26}/>}
  </div>
}
