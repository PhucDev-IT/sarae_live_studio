export type OverlaySaveState = 'idle' | 'pending' | 'saving' | 'saved' | 'error' | 'blocked'

/** One writer per editor. A close must await flush(), including edits made during a request. */
export function createOverlayAutosave<T, R>(options: {
  snapshot: () => T
  validate: (value: T) => string | null
  persist: (value: T) => Promise<R>
  onState: (state: OverlaySaveState) => void
  onSaved: (result: R) => void
  onError: (error: unknown) => void
}) {
  let revision = 0
  let savedRevision = 0
  let inFlight: Promise<boolean> | null = null
  const flush = (): Promise<boolean> => {
    if (inFlight) return inFlight
    if (savedRevision === revision) return Promise.resolve(true)
    inFlight = (async () => {
      while (savedRevision !== revision) {
        const writingRevision = revision
        const value = structuredClone(options.snapshot())
        const problem = options.validate(value)
        if (problem) {
          options.onState('blocked')
          options.onError(new Error(problem))
          return false
        }
        options.onState('saving')
        let result: R
        try { result = await options.persist(value) }
        catch (error) {
          options.onState('error')
          options.onError(error)
          return false
        }
        savedRevision = writingRevision
        if (savedRevision === revision) {
          options.onSaved(result)
          options.onState('saved')
        }
      }
      return true
    })().finally(() => { inFlight = null })
    return inFlight
  }
  return {
    changed: () => { revision += 1; options.onState('pending') },
    flush,
  }
}
