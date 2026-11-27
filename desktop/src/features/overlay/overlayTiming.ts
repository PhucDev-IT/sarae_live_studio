import { MIN_DURATION_MS } from './overlayModel.ts'

/** Use the actual video's duration, including videos longer than one hour. */
export function wholeVideoTiming(videoMs: number | null | undefined): { start_ms: number; duration_ms: number } | null {
  if (videoMs == null || !Number.isFinite(videoMs)) return null
  const duration_ms = Math.floor(videoMs)
  if (!Number.isSafeInteger(duration_ms) || duration_ms < MIN_DURATION_MS) return null
  return { start_ms: 0, duration_ms }
}
