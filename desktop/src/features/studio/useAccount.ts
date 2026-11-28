import { useCallback, useEffect, useRef, useState } from 'react'
import { studioClient, type AccountStatus } from '../../services/studio'

function isAccount(value: unknown): value is AccountStatus {
  return !!value && typeof value === 'object' && 'state' in value && 'licensed' in value
}

/** This machine's activation and license, kept current from Core events with a light poll as backup. */
export function useAccount() {
  const [account, setAccount] = useState<AccountStatus | null>(null)
  const latest = useRef<AccountStatus | null>(null)
  const apply = useCallback((next: AccountStatus) => { latest.current = next; setAccount(next) }, [])
  const refresh = useCallback(async () => {
    try {
      const next = await studioClient.accountStatus()
      apply(next)
      return next
    } catch {
      return null // Core not reachable yet: the shell shows the connection state.
    }
  }, [apply])

  useEffect(() => {
    let disposed = false
    let stop: (() => void) | undefined
    void refresh()
    studioClient.subscribe(frame => {
      if (disposed) return
      if (frame.event === 'studio.account.changed' && isAccount(frame.data)) apply(frame.data)
      else if (frame.event === 'core.ready') void refresh()
      else if (frame.event === 'studio.connection' && frame.data && typeof frame.data === 'object' && 'phase' in frame.data && frame.data.phase === 'ready') void refresh()
      // A LIVE that ends while the license is invalid locks the app again.
      else if (frame.event === 'studio.live.changed' && latest.current && !latest.current.licensed) void refresh()
    }).then(unsubscribe => { if (disposed) unsubscribe(); else stop = unsubscribe }).catch(() => { /* polling below */ })
    let ticks = 0
    const timer = window.setInterval(() => {
      ticks += 1
      // Fast until the Core answers once, then every 20 s while visible.
      if (!latest.current || (ticks % 4 === 0 && document.visibilityState === 'visible')) void refresh()
    }, 5_000)
    const onFocus = () => { void refresh() }
    window.addEventListener('focus', onFocus)
    return () => {
      disposed = true
      stop?.()
      window.clearInterval(timer)
      window.removeEventListener('focus', onFocus)
    }
  }, [apply, refresh])

  return { account, apply, refresh }
}
