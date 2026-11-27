import { test } from 'node:test'
import assert from 'node:assert/strict'
import { pinPresentation, broadcastPresentation } from '../src/features/studio/livePresentation.ts'
const pins = (patch = {}) => ({ real: true, chrome_connected: true, live: true, desired_product_id: 'one', actual_product_id: 'one', actual_known: true, in_flight: null, last_error: null, last_applied: null, product_names: { one: 'Áo trắng' }, ...patch })
const state = (patch = {}, pinPatch = {}) => ({ active: true, pins: pins(pinPatch), console_live: { state: 'LIVE' }, ...patch })

test('rehearsal never claims to have pinned on TikTok', () => {
  const view = pinPresentation(state({}, { real: false }))
  assert.match(view.label, /không ghim thật/)
  assert.notEqual(view.tone, 'success')
})
test('confirmed, pending, unknown and failed pins have distinct states', () => {
  assert.equal(pinPresentation(state()).label, 'Đã ghim trên TikTok')
  assert.equal(pinPresentation(state({}, { actual_known: false })).tone, 'pending')
  assert.equal(pinPresentation(state({}, { in_flight: { action: 'PIN_PRODUCT', product_id: 'one' } })).label, 'Đang ghim…')
  assert.equal(pinPresentation(state({}, { last_error: 'Không thấy card' })).detail, 'Không thấy card')
  assert.equal(pinPresentation(state({}, { actual_product_id: null })).label, 'Chưa có sản phẩm đang ghim')
})
test('missing Console or offline LIVE never displays a confirmed real pin', () => {
  assert.notEqual(pinPresentation(state({}, { chrome_connected: false })).tone, 'success')
  assert.notEqual(pinPresentation(state({ console_live: { state: 'OFFLINE' } })).tone, 'success')
})
test('local OBS playback is not proof of a TikTok broadcast', () => {
  const view = broadcastPresentation(state({ console_live: { state: 'UNKNOWN' }, broadcast: { state: 'unknown', username: 'shop' } }))
  assert.equal(view.label, 'Chưa xác nhận TikTok LIVE')
  assert.notEqual(view.tone, 'success')
})
test('public TikTok state is displayed independently from Console pin authorization', () => {
  const value = state({ console_live: { state: 'OFFLINE' }, broadcast: { state: 'live', username: 'shop' } })
  assert.equal(broadcastPresentation(value).label, 'TikTok đang LIVE')
  assert.notEqual(pinPresentation(value).tone, 'success')
})
