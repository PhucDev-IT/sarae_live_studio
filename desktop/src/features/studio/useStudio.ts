import { useCallback, useEffect, useRef, useState } from 'react'
import { isTauri } from '@tauri-apps/api/core'
import { coreProcess, onCoreState } from '../../services/core'
import { studioClient, type Media, type Presenter, type Product, type StudioStatus } from '../../services/studio'
import { errorMessage } from './ui'

export function useStudio() {
  const [status, setStatus] = useState<StudioStatus | null>(null)
  const [products, setProducts] = useState<Product[]>([])
  const [presenters, setPresenters] = useState<Presenter[]>([])
  const [media, setMedia] = useState<Media[]>([])
  const [connected, setConnected] = useState(false)
  const [loading, setLoading] = useState(true)
  const [retrying, setRetrying] = useState(false)
  const [error, setError] = useState('')
  const mounted = useRef(false)
  const sequence = useRef(0)
  const reconnectEvents = useRef<() => Promise<void>>(async () => {})

  const refresh = useCallback(async () => {
    const generation = ++sequence.current
    try {
      const [nextStatus, productResult, presenterResult, mediaResult] = await Promise.all([
        studioClient.getStatus(),
        studioClient.listProducts(),
        studioClient.listPresenters(),
        studioClient.listMedia(),
      ])
      if (!mounted.current || generation !== sequence.current) return
      setStatus(nextStatus)
      setProducts(productResult)
      setPresenters(presenterResult)
      setMedia(mediaResult)
      setConnected(true)
      setError('')
    } catch (cause) {
      if (!mounted.current || generation !== sequence.current) return
      setConnected(false)
      setError(errorMessage(cause))
    } finally {
      if (mounted.current && generation === sequence.current) setLoading(false)
    }
  }, [])

  const retry = useCallback(async () => {
    setRetrying(true)
    setError('')
    try {
      if (isTauri()) await coreProcess.restart()
      await refresh()
      await reconnectEvents.current()
    } catch (cause) {
      if (mounted.current) { setConnected(false); setError(errorMessage(cause)) }
    } finally {
      if (mounted.current) setRetrying(false)
    }
  }, [refresh])

  useEffect(() => {
    mounted.current = true
    let disposed = false
    let unsubscribe: (() => void) | undefined
    let unlistenState: (() => void) | undefined
    let subscribing = false
    const subscribe = async () => {
      if (subscribing || unsubscribe || disposed) return
      subscribing = true
      try {
        const stop = await studioClient.subscribe(frame => {
          if (disposed) return
          if (frame.event === 'studio.changed' || frame.event === 'core.ready') void refresh()
          if (frame.event === 'studio.connection' && frame.data && typeof frame.data === 'object' && 'phase' in frame.data) {
            const phase = frame.data.phase
            if (phase === 'ready') void refresh()
            if (phase === 'disconnected' || phase === 'crashed' || phase === 'stopped') {
              ++sequence.current
              setConnected(false)
              setError('Mất kết nối với Studio Core. Đang chờ kết nối lại.')
              if (!isTauri()) { unsubscribe?.(); unsubscribe = undefined }
            }
          }
          if (frame.event === 'core.stopped') {
            ++sequence.current
            setConnected(false)
            setError('Studio Core đã dừng. Hãy kết nối lại để tiếp tục.')
          }
        })
        if (disposed) stop()
        else unsubscribe = stop
      } catch { /* Polling keeps recovery available if the event channel is unavailable. */ }
      finally { subscribing = false }
    }
    reconnectEvents.current = async () => {
      unsubscribe?.()
      unsubscribe = undefined
      await subscribe()
    }
    void (async () => {
      try {
        if (isTauri()) {
          const stop = await onCoreState(state => {
            if (disposed) return
            if (state.phase === 'ready') void refresh()
            if (state.phase === 'crashed' || state.phase === 'stopped') {
              ++sequence.current
              setConnected(false)
              setLoading(false)
              setError(state.last_error?.message || 'Studio Core chưa chạy. Hãy kết nối lại để tiếp tục.')
            }
          })
          if (disposed) { stop(); return }
          unlistenState = stop
          await coreProcess.start()
        }
        if (!disposed) { await refresh(); await subscribe() }
      } catch (cause) {
        if (!disposed) { setConnected(false); setLoading(false); setError(errorMessage(cause)) }
      }
    })()
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') { void refresh(); void subscribe() }
    }, 15_000)
    const onFocus = () => { void refresh(); void subscribe() }
    window.addEventListener('focus', onFocus)
    return () => {
      disposed = true
      reconnectEvents.current = async () => {}
      mounted.current = false
      ++sequence.current
      unsubscribe?.()
      unlistenState?.()
      window.clearInterval(timer)
      window.removeEventListener('focus', onFocus)
    }
  }, [refresh])

  return { status, products, presenters, media, connected, loading, retrying, error, refresh, retry }
}
