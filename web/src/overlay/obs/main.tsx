import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import ObsOverlayApp from './ObsOverlayApp'

createRoot(document.getElementById('root')!).render(<StrictMode><ObsOverlayApp/></StrictMode>)
