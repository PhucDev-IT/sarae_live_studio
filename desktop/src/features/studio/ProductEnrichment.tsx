import { useEffect, useState } from 'react'
import { Download, ImagePlus, RefreshCw, Sparkles, X } from 'lucide-react'
import { studioClient, type Product, type ProductAnalysis, type ProductDetail, type ProductFacts } from '../../services/studio'
import { ErrorNotice, errorMessage } from './ui'
import { mergeProductInformation, productExportData } from './productInformation'

export function exportProducts(products: Product[], extra: Record<string, unknown> = {}) {
  const blob = new Blob([JSON.stringify(productExportData(products, extra), null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = products.length === 1 ? `san-pham-${products[0].tiktok_product_id || products[0].id}.json` : 'san-pham-studio.json'
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

async function readImage(file: File): Promise<string> {
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 20 * 1024 * 1024) throw new Error('Chọn ảnh JPEG, PNG hoặc WebP, tối đa 20 MB mỗi ảnh.')
  const bitmap = await createImageBitmap(file)
  try {
    const canvas = document.createElement('canvas')
    const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height))
    canvas.width = Math.max(1, Math.round(bitmap.width * scale))
    canvas.height = Math.max(1, Math.round(bitmap.height * scale))
    const ctx = canvas.getContext('2d')!
    let result = ''
    for (const quality of [0.88, 0.7, 0.5, 0.35]) {
      ctx.fillStyle = '#fff'
      ctx.fillRect(0, 0, canvas.width, canvas.height)
      ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
      result = canvas.toDataURL('image/jpeg', quality)
      if (result.length <= 175000) return result
    }
    throw new Error('Ảnh có quá nhiều chi tiết. Hãy cắt riêng vùng sản phẩm hoặc bảng size rồi chọn lại.')
  } finally { bitmap.close() }
}

export function ProductEnrichment({ product, description, facts, busy, disabled, onBusy, onApply }: {
  product: Product; description: string; facts: ProductFacts; busy: boolean; disabled: boolean
  onBusy: (busy: boolean) => void; onApply: (description: string, facts: ProductFacts) => void
}) {
  const [detail, setDetail] = useState<ProductDetail | null>(null)
  const [analysis, setAnalysis] = useState<ProductAnalysis | null>(null)
  const [text, setText] = useState('')
  const [images, setImages] = useState<{ name: string; data: string }[]>([])
  const [replace, setReplace] = useState(false)
  const [working, setWorking] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [mergeWarnings, setMergeWarnings] = useState<string[]>([])
  useEffect(() => {
    let alive = true
    void studioClient.productDetail(product.id).then(result => { if (alive) setDetail(result) }, cause => { if (alive) setError(errorMessage(cause)) })
    return () => { alive = false }
  }, [product.id])

  const apply = (nextDescription: string, nextFacts: ProductFacts) => {
    const merged = mergeProductInformation(description, facts, nextDescription, nextFacts, replace)
    setMergeWarnings(merged.warnings)
    onApply(merged.description, merged.facts)
  }
  const run = async (action: 'read' | 'analyze') => {
    if (busy || disabled) return
    onBusy(true); setWorking(action); setError(''); setMessage(''); setMergeWarnings([])
    try {
      if (action === 'read') {
        const result = await studioClient.readProductDetail(product.id)
        setDetail(result); setAnalysis(null)
        if (!result.incomplete) {
          apply(result.description || '', {
            sizes: result.sizes.slice(0, 12).filter(size => size.length <= 10).map(size => ({ size, weight_min: null, weight_max: null, height_min: null, height_max: null, note: '' })),
            colors: result.colors.slice(0, 20).filter(color => color.length <= 30), material: '', shipping: '', notes: '', extras: {},
          })
        }
        setMessage(result.incomplete ? 'Trang chưa trả đủ dữ liệu. Chưa điền vào các ô; bạn có thể thử lấy lại hoặc bổ sung nội dung/ảnh bên dưới.' : 'Đã đọc chi tiết và điền dữ kiện có sẵn vào bản nháp. Bấm Phân tích để AI đọc thêm thông số và ảnh bảng size.')
      } else {
        const result = await studioClient.analyzeProduct({ id: product.id, text: [text, description].filter(Boolean).join('\n\n').slice(0, 20000), images: images.map(image => image.data) })
        setAnalysis(result)
        apply(result.description, result.facts)
        setMessage(`Đã phân tích nội dung và ${result.images_used} ảnh. Kiểm tra các ô trước khi lưu.${replace ? '' : ' Các ô đã có nội dung được giữ nguyên.'}`)
      }
    } catch (cause) { setError(errorMessage(cause)) }
    finally { onBusy(false); setWorking('') }
  }
  const addImages = async (files: File[]) => {
    if (!files.length || busy) return
    if (images.length + files.length > 4) { setError('Chọn tối đa 4 ảnh.'); return }
    onBusy(true); setWorking('images'); setError('')
    try {
      const additions = await Promise.all(files.map(async file => ({ name: file.name, data: await readImage(file) })))
      setImages(current => [...current, ...additions]); setAnalysis(null)
    } catch (cause) { setError(errorMessage(cause)) }
    finally { onBusy(false); setWorking('') }
  }

  return <section className="studio-enrichment" aria-label="Lấy chi tiết và tự điền bằng AI">
    <div className="studio-enrichment-heading"><div><strong>Lấy chi tiết & tự điền bằng AI</strong><p>Đọc mô tả, màu, size và ảnh bảng size từ TikTok; hoặc thêm nguồn bên dưới.</p></div>
      <button type="button" className="studio-button" disabled={busy} onClick={() => exportProducts([{ ...product, description, facts }], { draft: true, detail, analysis, merge_warnings: mergeWarnings, source_text: text, uploaded_images: images.map(image => image.name) })}><Download size={15} />Xuất JSON</button></div>
    <label className="studio-field">Nội dung bổ sung<textarea rows={3} maxLength={20000} disabled={busy} placeholder="Dán mô tả, thông số, chính sách hoặc nội dung bảng size…" value={text} onChange={event => { setText(event.target.value); setAnalysis(null) }} /></label>
    <div className="studio-enrichment-images">{images.map((image, index) => <div key={`${index}-${image.name}`}><img src={image.data} alt={image.name} /><button type="button" disabled={busy} aria-label={`Bỏ ảnh ${image.name}`} onClick={() => { setImages(current => current.filter((_, i) => i !== index)); setAnalysis(null) }}><X size={14} /></button></div>)}</div>
    <div className="studio-enrichment-actions">
      <label className={`studio-button${busy ? ' disabled' : ''}`}><ImagePlus size={15} />{working === 'images' ? 'Đang xử lý ảnh…' : `Thêm ảnh (${images.length}/4)`}<input type="file" accept="image/jpeg,image/png,image/webp" multiple disabled={busy || images.length >= 4} onChange={event => { const files = Array.from(event.target.files || []); event.target.value = ''; void addImages(files) }} /></label>
      {product.tiktok_product_id && <button type="button" className="studio-button" disabled={busy || disabled} onClick={() => void run('read')}><RefreshCw size={15} className={working === 'read' ? 'studio-spin' : ''} />{working === 'read' ? 'Đang lấy chi tiết…' : 'Lấy chi tiết TikTok'}</button>}
      <button type="button" className="studio-button primary" disabled={busy || disabled} onClick={() => void run('analyze')}><Sparkles size={15} />{working === 'analyze' ? 'Đang phân tích…' : 'Phân tích & tự điền'}</button>
    </div>
    <label className="studio-enrichment-option"><input type="checkbox" checked={replace} disabled={busy} onChange={event => setReplace(event.target.checked)} />Cho phép thay nội dung đã có bằng dữ kiện đọc được</label>
    <p className="studio-muted">AI dùng nhà cung cấp đã cấu hình trong Trợ lý AI. Nội dung và ảnh được gửi khi bấm Phân tích; model cần hỗ trợ ảnh. Dữ kiện chưa rõ được để trống.</p>
    {detail && <details><summary>Chi tiết đã đọc · {detail.colors.length} màu · {detail.sizes.length} size · {detail.images.length} ảnh{detail.incomplete ? ' · chưa đầy đủ' : ''}</summary><p className="studio-enrichment-description">{detail.description || 'Chưa đọc được mô tả.'}</p>{detail.attributes.length > 0 && <dl>{detail.attributes.map((item, i) => <div key={i}><dt>{item.name}</dt><dd>{item.value}</dd></div>)}</dl>}<p className="studio-muted">{new Date(detail.read_at).toLocaleString('vi-VN')}</p></details>}
    {message && <p role="status" className="studio-enrichment-message">{message}</p>}
    {((analysis?.warnings.length ?? 0) > 0 || mergeWarnings.length > 0) && <ul className="studio-enrichment-warnings">{[...(analysis?.warnings ?? []), ...mergeWarnings].map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
    {error && <ErrorNotice message={error} />}
  </section>
}
