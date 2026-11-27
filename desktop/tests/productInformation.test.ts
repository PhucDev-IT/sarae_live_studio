import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mergeProductInformation, productExportData } from '../src/features/studio/productInformation.ts'

const empty = () => ({ sizes: [], colors: [], material: '', shipping: '', notes: '', extras: {} })
const size = (weight_min = null, weight_max = null) => ({ size: 'M', weight_min, weight_max, height_min: null, height_max: null, note: '' })

test('fills blank chart cells after reading TikTok size names, preserves filled cells and source objects', () => {
  const current = { ...empty(), sizes: [size(46)], material: 'Cotton đã kiểm tra', extras: { form: 'rộng', bao_quan: '' } }
  const incoming = { ...empty(), sizes: [{ ...size(45, 55), note: 'Theo ảnh bảng size' }], material: 'Linen', colors: ['Trắng'], extras: { form: 'ôm', bao_quan: 'Giặt tay' } }
  const result = mergeProductInformation('Đã viết', current, 'AI mới', incoming, false)
  assert.equal(result.description, 'Đã viết')
  assert.equal(result.facts.material, 'Cotton đã kiểm tra')
  assert.deepEqual(result.facts.sizes[0], { ...size(46, 55), note: 'Theo ảnh bảng size' })
  assert.deepEqual(result.facts.extras, { form: 'rộng', bao_quan: 'Giặt tay' })
  assert.equal(current.sizes[0].weight_max, null)
  assert.deepEqual(incoming.colors, ['Trắng'])
})

test('conflicting ranges remain unchanged and report the conflict', () => {
  const result = mergeProductInformation('', { ...empty(), sizes: [size(60)] }, '', { ...empty(), sizes: [size(45, 55)] }, false)
  assert.deepEqual(result.facts.sizes[0], size(60))
  assert.equal(result.warnings.length, 1)
})

test('replace mode applies supplied facts and does not clear unrelated fields', () => {
  const result = mergeProductInformation('Cũ', { ...empty(), sizes: [size(60)], material: 'Cotton', shipping: '3 ngày' }, 'Mới', { ...empty(), sizes: [size(45, 55)], material: 'Linen' }, true)
  assert.equal(result.description, 'Mới')
  assert.equal(result.facts.material, 'Linen')
  assert.equal(result.facts.shipping, '3 ngày')
  assert.deepEqual(result.facts.sizes[0], size(45, 55))
})

test('export round trips Vietnamese data and omits local session thumbnail tokens', () => {
  const product = { id: 'one', name: 'Áo trắng', description: 'Cotton mềm', facts: empty(), thumbnail_url: 'http://127.0.0.1/studio/thumbnails/one?token=SECRET', tiktok_product_id: '1234567890123456789' }
  const data = JSON.stringify(productExportData([product], { draft: true, detail: { colors: ['Trắng'] } }))
  assert.equal(data.includes('SECRET'), false)
  const parsed = JSON.parse(data)
  assert.equal(parsed.products[0].name, 'Áo trắng')
  assert.equal(parsed.products[0].tiktok_product_id, '1234567890123456789')
  assert.equal(parsed.draft, true)
  assert.deepEqual(parsed.detail.colors, ['Trắng'])
})
