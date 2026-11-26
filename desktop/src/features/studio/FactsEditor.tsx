import { useState, type KeyboardEvent } from 'react'
import { Plus, Ruler, X } from 'lucide-react'
import type { ProductFacts, SizeRow } from '../../services/studio'

const emptyRow: SizeRow = { size: '', weight_min: null, weight_max: null, height_min: null, height_max: null, note: '' }
export const EXTRA_KEY = /^[a-z][a-z0-9_]{1,30}$/

/** Client-side checks mirroring the core validator, so the operator sees the problem before saving. */
export function factsProblem(facts: ProductFacts): string {
  for (const row of facts.sizes) {
    if (!row.size.trim()) return 'Mỗi dòng size cần có tên size (ví dụ S, M, 38).'
    if (row.weight_min !== null && row.weight_max !== null && row.weight_min > row.weight_max) return `Size ${row.size}: cân nặng "từ" lớn hơn "đến".`
    if (row.height_min !== null && row.height_max !== null && row.height_min > row.height_max) return `Size ${row.size}: chiều cao "từ" lớn hơn "đến".`
    for (const value of [row.weight_min, row.weight_max]) if (value !== null && (value < 20 || value > 200)) return `Size ${row.size}: cân nặng phải từ 20 đến 200 kg.`
    for (const value of [row.height_min, row.height_max]) if (value !== null && (value < 90 || value > 230)) return `Size ${row.size}: chiều cao phải từ 90 đến 230 cm.`
  }
  for (const key of Object.keys(facts.extras)) if (!EXTRA_KEY.test(key)) return `Tên thông tin “${key}” chỉ gồm chữ thường không dấu, số và _ (ví dụ: form, bao_quan).`
  return ''
}

export function FactsEditor({ value, onChange, disabled }: { value: ProductFacts; onChange: (facts: ProductFacts) => void; disabled: boolean }) {
  const [color, setColor] = useState('')
  const set = (patch: Partial<ProductFacts>) => onChange({ ...value, ...patch })
  const setRow = (index: number, patch: Partial<SizeRow>) => set({ sizes: value.sizes.map((row, i) => i === index ? { ...row, ...patch } : row) })
  const number = (raw: string) => raw.trim() === '' ? null : Number(raw)
  const addColor = () => {
    const next = color.split(',').map(item => item.trim()).filter(Boolean)
    const colors = [...value.colors]
    for (const item of next) if (colors.length < 20 && !colors.some(existing => existing.toLocaleLowerCase('vi') === item.toLocaleLowerCase('vi'))) colors.push(item.slice(0, 30))
    set({ colors })
    setColor('')
  }
  const onColorKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter' || event.key === ',') { event.preventDefault(); addColor() }
    if (event.key === 'Backspace' && !color && value.colors.length) set({ colors: value.colors.slice(0, -1) })
  }
  const extras = Object.entries(value.extras)
  const setExtras = (entries: [string, string][]) => set({ extras: Object.fromEntries(entries) })

  return <div className="studio-facts">
    <section>
      <div className="studio-facts-heading"><Ruler size={16} /><strong>Bảng size</strong><span>AI chỉ gợi ý size khi cân nặng/chiều cao của khách nằm trong khoảng ở đây.</span></div>
      {value.sizes.length ? <div className="studio-size-table" role="table" aria-label="Bảng size">
        <div className="studio-size-row head" role="row"><span>Size</span><span>Cân nặng (kg)</span><span>Chiều cao (cm)</span><span>Ghi chú</span><span /></div>
        {value.sizes.map((row, index) => <div className="studio-size-row" role="row" key={index}>
          <input aria-label="Tên size" value={row.size} maxLength={10} disabled={disabled} placeholder="M" onChange={event => setRow(index, { size: event.target.value.toUpperCase() })} />
          <span className="studio-range"><input aria-label={`Cân nặng từ, size ${row.size}`} type="number" inputMode="decimal" value={row.weight_min ?? ''} disabled={disabled} placeholder="45" onChange={event => setRow(index, { weight_min: number(event.target.value) })} /><i>–</i><input aria-label={`Cân nặng đến, size ${row.size}`} type="number" inputMode="decimal" value={row.weight_max ?? ''} disabled={disabled} placeholder="55" onChange={event => setRow(index, { weight_max: number(event.target.value) })} /></span>
          <span className="studio-range"><input aria-label={`Chiều cao từ, size ${row.size}`} type="number" inputMode="decimal" value={row.height_min ?? ''} disabled={disabled} placeholder="150" onChange={event => setRow(index, { height_min: number(event.target.value) })} /><i>–</i><input aria-label={`Chiều cao đến, size ${row.size}`} type="number" inputMode="decimal" value={row.height_max ?? ''} disabled={disabled} placeholder="165" onChange={event => setRow(index, { height_max: number(event.target.value) })} /></span>
          <input aria-label={`Ghi chú size ${row.size}`} value={row.note} maxLength={80} disabled={disabled} placeholder="Ôm vừa" onChange={event => setRow(index, { note: event.target.value })} />
          <button type="button" className="studio-button ghost icon-only danger" disabled={disabled} aria-label={`Xóa size ${row.size || index + 1}`} onClick={() => set({ sizes: value.sizes.filter((_, i) => i !== index) })}><X size={15} /></button>
        </div>)}
      </div> : <p className="studio-muted studio-facts-empty">Chưa có bảng size. Bỏ qua nếu sản phẩm không chia size.</p>}
      <button type="button" className="studio-button ghost studio-add-row" disabled={disabled || value.sizes.length >= 12} onClick={() => set({ sizes: [...value.sizes, { ...emptyRow }] })}><Plus size={15} />Thêm size</button>
    </section>
    <section className="studio-facts-grid">
      <div className="studio-field">Màu sắc
        <div className={`studio-chip-input${disabled ? ' disabled' : ''}`}>{value.colors.map(item => <span key={item} className="studio-chip selected small">{item}<button type="button" aria-label={`Bỏ màu ${item}`} disabled={disabled} onClick={() => set({ colors: value.colors.filter(existing => existing !== item) })}><X size={12} /></button></span>)}
          <input aria-label="Thêm màu" value={color} disabled={disabled || value.colors.length >= 20} placeholder={value.colors.length ? '' : 'Gõ màu rồi Enter'} onChange={event => setColor(event.target.value)} onKeyDown={onColorKey} onBlur={() => color.trim() && addColor()} />
        </div>
      </div>
      <label className="studio-field">Chất liệu<input value={value.material} maxLength={200} disabled={disabled} placeholder="Linen 100%" onChange={event => set({ material: event.target.value })} /></label>
      <label className="studio-field">Giao hàng & đổi trả<input value={value.shipping} maxLength={300} disabled={disabled} placeholder="Giao 2-4 ngày, đổi size trong 7 ngày" onChange={event => set({ shipping: event.target.value })} /></label>
      <label className="studio-field">Lưu ý khác<input value={value.notes} maxLength={500} disabled={disabled} placeholder="Giặt tay, không sấy nóng" onChange={event => set({ notes: event.target.value })} /></label>
    </section>
    <section>
      <div className="studio-facts-heading"><strong>Thông tin thêm</strong><span>Mỗi dòng là một dữ kiện AI được phép nói, ví dụ form → rộng.</span></div>
      {extras.map(([key, text], index) => <div className="studio-extra-row" key={index}>
        <input aria-label="Tên thông tin" value={key} maxLength={31} disabled={disabled} placeholder="form" className={key && !EXTRA_KEY.test(key) ? 'invalid' : ''} onChange={event => setExtras(extras.map((entry, i) => i === index ? [event.target.value.toLowerCase().replace(/\s+/g, '_'), entry[1]] : entry))} />
        <input aria-label={`Giá trị ${key}`} value={text} maxLength={200} disabled={disabled} placeholder="rộng, che khuyết điểm" onChange={event => setExtras(extras.map((entry, i) => i === index ? [entry[0], event.target.value] : entry))} />
        <button type="button" className="studio-button ghost icon-only danger" disabled={disabled} aria-label={`Xóa thông tin ${key}`} onClick={() => setExtras(extras.filter((_, i) => i !== index))}><X size={15} /></button>
      </div>)}
      <button type="button" className="studio-button ghost studio-add-row" disabled={disabled || extras.length >= 30 || extras.some(([key]) => !key)} onClick={() => setExtras([...extras, ['', '']])}><Plus size={15} />Thêm thông tin</button>
    </section>
  </div>
}
