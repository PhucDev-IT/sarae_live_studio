import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '../../vendor/react-ui-core/packages/ui/src/theme/index.scss'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
