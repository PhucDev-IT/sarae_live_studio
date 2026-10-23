/** Capture a cover frame (and duration/size) from a video in the browser: a local File before upload, or a signed
 * stream URL of a stored video (the Backend serves it with CORS, so the canvas is not tainted). */
export type FrameCapture = { blob:Blob|null; duration:number|null; width:number|null; height:number|null }

const MAX_EDGE = 540
const TIMEOUT_MS = 15000

export function captureFrame(source:File|string, atSeconds?:number):Promise<FrameCapture>{
  return new Promise(resolve=>{
    const video = document.createElement('video')
    const url = typeof source === 'string' ? source : URL.createObjectURL(source)
    let done = false
    const finish = (result:FrameCapture) => {
      if (done) return
      done = true
      window.clearTimeout(timer)
      video.removeAttribute('src')
      video.load()
      if (typeof source !== 'string') URL.revokeObjectURL(url)
      resolve(result)
    }
    const meta = () => ({duration:Number.isFinite(video.duration) ? video.duration : null,
      width:video.videoWidth || null, height:video.videoHeight || null})
    const timer = window.setTimeout(()=>finish({blob:null, ...meta()}), TIMEOUT_MS)
    video.muted = true
    video.playsInline = true
    video.preload = 'metadata'
    if (typeof source === 'string') video.crossOrigin = 'anonymous'
    video.onloadedmetadata = () => {
      const target = atSeconds ?? Math.min(1, (video.duration || 2) * 0.1)
      video.currentTime = Math.max(0, Math.min(target, (video.duration || target) - 0.05))
    }
    video.onseeked = () => {
      const {width, height} = meta()
      if (!width || !height) return finish({blob:null, ...meta()})
      const scale = Math.min(1, MAX_EDGE / Math.max(width, height))
      const canvas = document.createElement('canvas')
      canvas.width = Math.round(width * scale)
      canvas.height = Math.round(height * scale)
      try {
        canvas.getContext('2d')?.drawImage(video, 0, 0, canvas.width, canvas.height)
        canvas.toBlob(blob=>finish({blob, ...meta()}), 'image/jpeg', 0.82)
      } catch {
        finish({blob:null, ...meta()})
      }
    }
    video.onerror = () => finish({blob:null, duration:null, width:null, height:null})
    video.src = url
  })
}

/** `count` small frames spread evenly over a stored video, delivered one by one as they are read (cutter timeline). */
export function filmstrip(url:string, count:number, height:number, onFrame:(index:number, image:string)=>void, signal:AbortSignal):Promise<void>{
  return new Promise(resolve=>{
    const video = document.createElement('video')
    const canvas = document.createElement('canvas')
    let index = 0, done = false
    const finish = () => {
      if (done) return
      done = true
      video.removeAttribute('src')
      video.load()
      resolve()
    }
    const next = () => {
      if (signal.aborted || index >= count) return finish()
      video.currentTime = Math.min(video.duration - 0.05, (index + 0.5) * video.duration / count)
    }
    signal.addEventListener('abort', finish, {once:true})
    video.muted = true
    video.playsInline = true
    video.preload = 'auto'
    video.crossOrigin = 'anonymous'
    video.onloadedmetadata = () => {
      canvas.height = height
      canvas.width = video.videoWidth && video.videoHeight ? Math.round(height * video.videoWidth / video.videoHeight) : Math.round(height * 9 / 16)
      next()
    }
    video.onseeked = () => {
      if (done) return
      try {
        canvas.getContext('2d')?.drawImage(video, 0, 0, canvas.width, canvas.height)
        onFrame(index, canvas.toDataURL('image/jpeg', 0.7))
      } catch {
        return finish()
      }
      index++
      next()
    }
    video.onerror = finish
    video.src = url
  })
}

/** The frame currently shown by a playing <video> element (used by "Đặt làm ảnh bìa"). */
export function captureElement(video:HTMLVideoElement):Promise<Blob|null>{
  const width = video.videoWidth, height = video.videoHeight
  if (!width || !height) return Promise.resolve(null)
  const scale = Math.min(1, MAX_EDGE / Math.max(width, height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(width * scale)
  canvas.height = Math.round(height * scale)
  try {
    canvas.getContext('2d')?.drawImage(video, 0, 0, canvas.width, canvas.height)
  } catch {
    return Promise.resolve(null)
  }
  return new Promise(resolve=>canvas.toBlob(resolve, 'image/jpeg', 0.85))
}
