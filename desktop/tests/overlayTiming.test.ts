import { test } from 'node:test'
import assert from 'node:assert/strict'
import { wholeVideoTiming } from '../src/features/overlay/overlayTiming.ts'
import { normalizeOverlay } from '../src/features/overlay/overlayModel.ts'

test('whole-video overlay uses the full duration without the former one-hour cap', () => {
  for (const duration of [4500, 3601000, 10800125, 108000000]) {
    const timing = wholeVideoTiming(duration)
    assert.deepEqual(timing, { start_ms: 0, duration_ms: duration })
    const saved = normalizeOverlay({ media_id: 'video', ...timing, elements: [{ kind: 'text', text: 'Sale' }] })
    const reopened = normalizeOverlay(JSON.parse(JSON.stringify(saved)))
    assert.equal(reopened.duration_ms, duration)
    assert.equal(reopened.start_ms, 0)
  }
})

test('full-video timing rounds down fractional milliseconds to stay within the video', () => {
  assert.deepEqual(wholeVideoTiming(10800125.75), { start_ms: 0, duration_ms: 10800125 })
})

test('unknown or invalid video durations cannot apply full-video timing', () => {
  for (const value of [null, undefined, NaN, Infinity, -10, 0, 499, Number.MAX_SAFE_INTEGER + 10]) {
    assert.equal(wholeVideoTiming(value), null)
  }
})
