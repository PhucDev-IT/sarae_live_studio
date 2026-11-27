import type { Product, ProductFacts } from '../../services/studio'

/** Fill individual blank size-chart cells too, after TikTok supplied just size names. */
export function mergeProductInformation(description: string, facts: ProductFacts, nextDescription: string, next: ProductFacts, replace: boolean) {
  const merged = structuredClone(facts)
  const warnings: string[] = []
  if (next.sizes.length) {
    if (replace || !merged.sizes.length) merged.sizes = structuredClone(next.sizes)
    else {
      for (const row of merged.sizes) {
        const candidate = next.sizes.find(item => item.size.trim().toLocaleUpperCase('vi') === row.size.trim().toLocaleUpperCase('vi'))
        if (!candidate) continue
        const previous = { ...row }
        for (const key of ['weight_min', 'weight_max', 'height_min', 'height_max'] as const) {
          if (row[key] === null) row[key] = candidate[key]
        }
        if (!row.note.trim()) row.note = candidate.note
        for (const [min, max] of [['weight_min', 'weight_max'], ['height_min', 'height_max']] as const) {
          if (row[min] !== null && row[max] !== null && row[min]! > row[max]!) {
            row[min] = previous[min]; row[max] = previous[max]
            warnings.push(`Size ${row.size}: dữ kiện mới mâu thuẫn với khoảng đã nhập; giữ dữ liệu hiện tại.`)
          }
        }
      }
    }
  }
  if (next.colors.length && (replace || !merged.colors.length)) merged.colors = [...next.colors]
  for (const key of ['material', 'shipping', 'notes'] as const) if (next[key] && (replace || !merged[key].trim())) merged[key] = next[key]
  for (const [key, value] of Object.entries(next.extras)) if (value && (replace || !merged.extras[key]?.trim())) merged.extras[key] = value
  return { description: nextDescription.trim() && (replace || !description.trim()) ? nextDescription : description, facts: merged, warnings }
}

export function productExportData(products: Product[], extra: Record<string, unknown> = {}) {
  return {
    schema: 'sarae.product-information', version: 1, exported_at: new Date().toISOString(),
    products: products.map(({ thumbnail_url: _thumbnail, ...product }) => product), ...extra,
  }
}
