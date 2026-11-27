import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Check, CornerDownRight, Pin, PinOff, Trash2 } from 'lucide-react'
import { studioClient, type MarkerAction, type Media, type Product } from '../../services/studio'
import { ErrorNotice, Modal, errorMessage } from './ui'

type Draft = { key: number; at_ms: number; action: MarkerAction; product_id: string | null }
const palette = ['#d95739', '#28765e', '#be9344', '#9a6790', '#46708f', '#c0587e', '#5a8a3a', '#7a5bc0']

export function clock(ms: number, tenths = false) {
  const total = Math.max(0, ms) / 1000
  const minutes = Math.floor(total / 60)
  const seconds = total - minutes * 60
  return `${String(minutes).padStart(2, '0')}:${tenths ? seconds.toFixed(1).padStart(4, '0') : String(Math.floor(seconds)).padStart(2, '0')}`
}

function parseClock(text: string): number | null {
  const match = text.trim().match(/^(?:(\d+):)?(\d+(?:[.,]\d+)?)$/)
  if (!match) return null
  const value = (Number(match[1] ?? 0) * 60 + Number(match[2].replace(',', '.'))) * 1000
  return Number.isFinite(value) ? Math.round(value) : null
}

/** Pin markers of one video: products are pinned on TikTok at these positions whenever the video plays LIVE. */
export function MarkerEditor({ media, products, onClose, onSaved }: { media: Media; products: Product[]; onClose: () => void; onSaved: (count: number) => void }) {
  const pinnable = useMemo(() => products.filter(product => product.source === 'tiktok').sort((a, b) => a.name.localeCompare(b.name, 'vi')), [products])
  const color = useMemo(() => new Map(pinnable.map((product, index) => [product.id, palette[index % palette.length]])), [pinnable])
  const names = useMemo(() => new Map(products.map(product => [product.id, product.name])), [products])
  const [url, setUrl] = useState('')
  const [markers, setMarkers] = useState<Draft[]>([])
  const [loaded, setLoaded] = useState(false)
  const [productId, setProductId] = useState(media.product_id && color.has(media.product_id) ? media.product_id : pinnable[0]?.id ?? '')
  const [now, setNow] = useState(0)
  const [duration, setDuration] = useState((media.duration_seconds ?? 0) * 1000)
  const [saving, setSaving] = useState<'idle' | 'pending' | 'saving' | 'saved' | 'error'>('idle')
  const [error, setError] = useState('')
  const video = useRef<HTMLVideoElement>(null)
  const nextKey = useRef(1)
  const timer = useRef<number | undefined>(undefined)
  const latest = useRef<Draft[]>([])

  useEffect(() => {
    let alive = true
    void Promise.all([studioClient.mediaUrl(media.id), studioClient.listMarkers(media.id)]).then(([nextUrl, list]) => {
      if (!alive) return
      setUrl(nextUrl)
      const drafts = list.map(m => ({ key: nextKey.current++, at_ms: m.at_ms, action: m.action, product_id: m.product_id }))
      latest.current = drafts
      setMarkers(drafts)
      setLoaded(true)
    }).catch(cause => { if (alive) setError(errorMessage(cause)) })
    return () => { alive = false }
  }, [media.id])

  const save = useCallback(async () => {
    window.clearTimeout(timer.current)
    setSaving('saving')
    try {
      const saved = await studioClient.setMarkers(media.id, latest.current.map(m => ({ at_ms: m.at_ms, action: m.action, ...(m.action === 'PIN_PRODUCT' ? { product_id: m.product_id } : {}) })))
      setSaving('saved')
      setError('')
      onSaved(saved.length)
    } catch (cause) { setSaving('error'); setError(errorMessage(cause)) }
  }, [media.id, onSaved])

  const change = (next: Draft[]) => {
    const sorted = [...next].sort((a, b) => a.at_ms - b.at_ms || a.key - b.key)
    latest.current = sorted
    setMarkers(sorted)
    setSaving('pending')
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => void save(), 500)
  }
  const close = async () => {
    if (saving === 'pending') await save()
    onClose()
  }
  const add = (action: MarkerAction) => {
    if (action === 'PIN_PRODUCT' && !productId) return
    const at = Math.round(now)
    change([...markers.filter(m => m.at_ms !== at), { key: nextKey.current++, at_ms: at, action, product_id: action === 'PIN_PRODUCT' ? productId : null }])
  }
  const seek = (ms: number) => { if (video.current) { video.current.currentTime = ms / 1000; setNow(ms) } }

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const tag = (event.target as HTMLElement | null)?.tagName
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return
      if (event.key === 'p' || event.key === 'P') { event.preventDefault(); add('PIN_PRODUCT') }
      if (event.key === 'u' || event.key === 'U') { event.preventDefault(); add('UNPIN_PRODUCT') }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })
  useEffect(() => () => window.clearTimeout(timer.current), [])

  const length = Math.max(duration, ...markers.map(m => m.at_ms + 1000), 1)
  // Coloured stretches: which product is pinned from each marker to the next.
  const segments = markers.map((marker, index) => ({ start: marker.at_ms, end: markers[index + 1]?.at_ms ?? length, product: marker.action === 'PIN_PRODUCT' ? marker.product_id : null }))
  const pinnedNow = segments.filter(s => s.start <= now).at(-1)?.product ?? null
  const status = { idle: '', pending: 'Chưa lưu…', saving: 'Đang lưu…', saved: 'Đã lưu', error: 'Chưa lưu được' }[saving]

  return <Modal title={`Mốc ghim · ${media.original_name}`} onClose={() => void close()} wide>
    <div className="studio-form studio-marker-editor">
      <div className="studio-marker-stage">
        <div className="studio-marker-video">{url ? <video ref={video} src={url} controls playsInline preload="metadata" onTimeUpdate={event => setNow(event.currentTarget.currentTime * 1000)} onSeeked={event => setNow(event.currentTarget.currentTime * 1000)} onLoadedMetadata={event => { if (Number.isFinite(event.currentTarget.duration)) setDuration(event.currentTarget.duration * 1000) }} /> : <p role="status">Đang mở video…</p>}</div>
        <div className="studio-marker-side">
          <div className="studio-marker-now"><small>Đang xem</small><strong>{clock(now, true)}</strong><span>{pinnedNow ? <><Pin size={13} />Ghim: {names.get(pinnedNow) ?? 'sản phẩm đã xóa'}</> : 'Không ghim sản phẩm'}</span></div>
          <label className="studio-field">Sản phẩm sẽ ghim<select value={productId} onChange={event => setProductId(event.target.value)}>{!pinnable.length && <option value="">Chưa có sản phẩm TikTok</option>}{pinnable.map(product => <option key={product.id} value={product.id}>{product.name}</option>)}</select></label>
          <button type="button" className="studio-button primary" disabled={!loaded || !productId} onClick={() => add('PIN_PRODUCT')}><Pin size={15} />Ghim từ {clock(now)}<kbd>P</kbd></button>
          <button type="button" className="studio-button" disabled={!loaded} onClick={() => add('UNPIN_PRODUCT')}><PinOff size={15} />Bỏ ghim từ {clock(now)}<kbd>U</kbd></button>
          <p className="studio-muted studio-marker-help">Xem video tới đoạn giới thiệu sản phẩm rồi bấm Ghim. Khi LIVE, Studio ghim đúng sản phẩm ở đúng đoạn này, mỗi lần video phát lại.</p>
        </div>
      </div>
      <div className="studio-marker-track" role="slider" aria-label="Dòng thời gian mốc ghim" aria-valuemin={0} aria-valuemax={Math.round(length)} aria-valuenow={Math.round(now)} tabIndex={0}
        onClick={event => { const box = event.currentTarget.getBoundingClientRect(); seek((event.clientX - box.left) / box.width * length) }}
        onKeyDown={event => { if (event.key === 'ArrowRight') seek(now + 1000); if (event.key === 'ArrowLeft') seek(now - 1000) }}>
        {segments.map((segment, index) => segment.product && <span key={index} className="studio-marker-band" style={{ left: `${segment.start / length * 100}%`, width: `${(segment.end - segment.start) / length * 100}%`, background: color.get(segment.product) ?? '#a3978a' }} title={names.get(segment.product)} />)}
        {markers.map(marker => <i key={marker.key} className={`studio-marker-flag ${marker.action === 'PIN_PRODUCT' ? 'pin' : 'unpin'}`} style={{ left: `${marker.at_ms / length * 100}%`, borderColor: marker.product_id ? color.get(marker.product_id) : undefined }} />)}
        <b className="studio-marker-head" style={{ left: `${Math.min(100, now / length * 100)}%` }} />
      </div>
      <div className="studio-marker-scale"><span>00:00</span><span>{clock(length)}</span></div>
      <div className="studio-marker-list">
        <div className="studio-marker-list-head"><strong>{markers.length ? `${markers.length} mốc` : 'Chưa có mốc nào'}</strong><span className={`studio-save-state ${saving}`}>{saving === 'saved' && <Check size={13} />}{status}</span></div>
        {markers.map(marker => <MarkerRow key={marker.key} marker={marker} pinnable={pinnable} color={marker.product_id ? color.get(marker.product_id) : undefined}
          onSeek={() => seek(marker.at_ms)} onDelete={() => change(markers.filter(m => m.key !== marker.key))}
          onChange={patch => change(markers.map(m => m.key === marker.key ? { ...m, ...patch } : m))} />)}
      </div>
      {error && <ErrorNotice message={error} />}
      <div className="studio-form-actions"><button type="button" className="studio-button primary" onClick={() => void close()}>Xong</button></div>
    </div>
  </Modal>
}

function MarkerRow({ marker, pinnable, color, onSeek, onDelete, onChange }: { marker: Draft; pinnable: Product[]; color?: string; onSeek: () => void; onDelete: () => void; onChange: (patch: Partial<Draft>) => void }) {
  const [text, setText] = useState(clock(marker.at_ms, true))
  useEffect(() => setText(clock(marker.at_ms, true)), [marker.at_ms])
  const commit = () => { const value = parseClock(text); if (value === null) setText(clock(marker.at_ms, true)); else if (value !== marker.at_ms) onChange({ at_ms: value }) }
  return <div className="studio-marker-row">
    <span className="studio-marker-dot" style={{ background: marker.action === 'PIN_PRODUCT' ? color : '#c9bdb0' }} />
    <input aria-label="Thời điểm" className="studio-marker-time" value={text} onChange={event => setText(event.target.value)} onBlur={commit} onKeyDown={event => { if (event.key === 'Enter') commit() }} />
    {marker.action === 'PIN_PRODUCT'
      ? <select aria-label="Sản phẩm" value={marker.product_id ?? ''} onChange={event => onChange({ product_id: event.target.value })}>{!pinnable.some(p => p.id === marker.product_id) && <option value={marker.product_id ?? ''}>Sản phẩm đã xóa</option>}{pinnable.map(product => <option key={product.id} value={product.id}>Ghim · {product.name}</option>)}</select>
      : <span className="studio-marker-unpin">Bỏ ghim sản phẩm</span>}
    <button type="button" className="studio-button ghost icon-only" aria-label="Tới mốc này" title="Tới mốc này" onClick={onSeek}><CornerDownRight size={15} /></button>
    <button type="button" className="studio-button ghost icon-only danger" aria-label="Xóa mốc" title="Xóa mốc" onClick={onDelete}><Trash2 size={15} /></button>
  </div>
}
