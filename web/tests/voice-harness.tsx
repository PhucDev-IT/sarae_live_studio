import {createRoot} from 'react-dom/client'
import {ThemeProvider,ToastProvider,Button} from '../src/ui/core'
import {AgentProvider} from '../src/agentContext'
import VoiceSettings from '../src/VoiceSettings'
import VoiceUtterances from '../src/VoiceUtterances'
import {TtsReadinessView} from '../src/TtsReadiness'
import {useTtsReadiness} from '../src/useTtsReadiness'
import '../src/App.css'
import '../src/index.css'
import '../src/ai-settings.css'
import '@sarae/ui/styles'
function Ready(){const tts=useTtsReadiness(1,'agent');return <section><h3>Readiness phiên</h3><TtsReadinessView report={tts.report} active={tts.active}/><Button onClick={()=>void tts.check()}>Đồng bộ TTS của phiên</Button>{tts.error}</section>}
createRoot(document.getElementById('root')!).render(<ThemeProvider value={{mode:'light'}}><ToastProvider><AgentProvider><main style={{maxWidth:1100,margin:'24px auto',padding:16}}><VoiceSettings/><Ready/><VoiceUtterances agentId="agent"/></main></AgentProvider></ToastProvider></ThemeProvider>)
