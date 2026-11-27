import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode, type PointerEvent as ReactPointerEvent } from 'react'
import { isTauri } from '@tauri-apps/api/core'
import { open } from '@tauri-apps/plugin-dialog'
import {
  ArrowDown, ArrowUp, BadgePercent, Captions, Check, Columns2, Palette, Ruler, Sparkles, ZoomIn, CircleAlert, CopyPlus, Crosshair, Eye, EyeOff, Image as ImageIcon, Layers, MonitorPlay, MoreVertical, Pause, Pin, Play, Plus,
  ScanLine, Shapes, Smartphone, Tag, Trash2, Type, Upload, X,
} from 'lucide-react'
import { studioClient, type Media, type MediaMarker, type OverlayAsset, type Product } from '../../services/studio'
import {
  ANIMATIONS, CANVAS, MAX_ELEMENTS, PRODUCT_FIELDS, clampFrame, endOf, fromPreset, normalizeElement, normalizeOverlay, round4, safeImageUrl,
  type Animation, type AnimationName, type BlockKind, type Frame, type Overlay, type OverlayElement, type OverlayIssue, type OverlayLayout,
  type OverlayPreset, type OverlayScene, type TextStyle,
} from '../overlay/overlayModel'
import { overlayName, secondsText, trackName } from '../overlay/overlayDraft'
import { OverlayPreview } from '../overlay/OverlayStage'
import '../overlay/overlay-editor.css'
import '../overlay/overlay-panel.css'
import { clock } from './MarkerEditor'
import { EmptyState, ErrorNotice, Modal, errorMessage } from './ui'

const ANIMATION_LABEL: Record<AnimationName, string> = {
  none: 'Không', fade: 'Mờ dần', slide_up: 'Trượt lên', slide_down: 'Trượt xuống', slide_left: 'Trượt sang trái', slide_right: 'Trượt sang phải', zoom: 'Phóng to',
}
const BLOCK_LABEL: Record<BlockKind, string> = { text: 'Chữ', product_field: 'Thông tin sản phẩm', image: 'Ảnh', shape: 'Hình nền' }
const BLOCK_ICON: Record<BlockKind, typeof Type> = { text: Type, product_field: Tag, image: ImageIcon, shape: Shapes }
const FIELD_LABEL: Record<string, string> = { name: 'Tên', price: 'Giá', origin_price: 'Giá gốc', sku: 'Mã TikTok' }
const NEW_BLOCK: Record<BlockKind, Partial<OverlayElement>> = {
  text: { kind: 'text', text: 'Chữ mới', frame: { x: 0.05, y: 0.1, width: 0.9, height: 0.8 } },
  product_field: { kind: 'product_field', field: 'name', frame: { x: 0.05, y: 0.1, width: 0.9, height: 0.8 } },
  image: { kind: 'image', source: 'asset', asset_id: null, frame: { x: 0.05, y: 0.05, width: 0.9, height: 0.9 } },
  shape: { kind: 'shape', fill: '#000000A0', radius: 0.1 },
}
const PRESET_ICON: Record<string, typeof Type> = {
  PRODUCT_INFO: Tag, PRICE_TAG: BadgePercent, SIZE_CHART: Ruler, CAPTION: Captions, PRODUCT_SPOTLIGHT: Sparkles, COLOR_OPTIONS: Palette,
  DETAIL_IMAGE: ZoomIn, COMPARE_LOOKS: Columns2, PRODUCT_IMAGE: ImageIcon, TEXT_CARD: Type,
}

type Scene = OverlayScene & { api_base: string }
type SaveState = 'idle' | 'pending' | 'saving' | 'saved' | 'error' | 'blocked'

/** Checks the core would refuse (so autosave waits), shown on the overlay. */
function localProblem(o: Overlay): string | null {
  for (const e of o.elements) {
    if (e.kind === 'image' && e.source === 'asset' && !e.asset_id) return 'Chọn ảnh cho khối ảnh.'
    if ((e.kind === 'product_field' || (e.kind === 'image' && e.source === 'product')) && !e.product_id && !o.product_id) return 'Chọn sản phẩm cho overlay.'
    if (e.kind === 'text' && !e.text.trim()) return 'Khối chữ đang trống.'
  }
  if (o.animation_in.duration_ms + o.animation_out.duration_ms > o.duration_ms) return 'Hiệu ứng vào + ra dài hơn thời lượng.'
  return null
}

const productThumb = (p?: Product) => p ? (p.thumbnail_url || p.image_url || undefined) : undefined
const numberOr = (text: string, fallback: number) => { const v = Number(text.replace(',', '.')); return Number.isFinite(v) ? v : fallback }
const px = (fraction: number, side: number) => Math.round(fraction * side)

/** Overlays of one video, drawn over it in OBS whenever it plays LIVE. Layout of dev's overlay studio; autosaved. */
export function OverlayEditor({ media, products, onClose }: { media: Media; products: Product[]; onClose: () => void }) {
  const [url, setUrl] = useState('')
  const [overlays, setOverlays] = useState<Overlay[]>([])
  const [scene, setScene] = useState<Scene>({ overlays: [], products: {}, assets: {}, api_base: '' })
  const [issues, setIssues] = useState<OverlayIssue[]>([])
  const [presets, setPresets] = useState<OverlayPreset[]>([])
  const [markers, setMarkers] = useState<MediaMarker[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [now, setNow] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [duration, setDuration] = useState((media.duration_seconds ?? 0) * 1000)
  const [mode, setMode] = useState<'obs' | 'tiktok'>('obs')
  const [zones, setZones] = useState(false)
  const [library, setLibrary] = useState(false)
  const [picking, setPicking] = useState<number | null>(null)
  const [saving, setSaving] = useState<SaveState>('idle')
  const [error, setError] = useState('')
  const video = useRef<HTMLVideoElement>(null)
  const timer = useRef<number | undefined>(undefined)
  const latest = useRef<Overlay[]>([])
  const pinnable = useMemo(() => products.filter(p => p.source === 'tiktok').sort((a, b) => a.name.localeCompare(b.name, 'vi')), [products])
  const productById = useMemo(() => new Map(products.map(p => [p.id, p])), [products])

  useEffect(() => {
    let alive = true
    void Promise.all([studioClient.mediaUrl(media.id), studioClient.listOverlays(media.id), studioClient.overlayTemplates(), studioClient.listMarkers(media.id)])
      .then(([nextUrl, data, templates, pins]) => {
        if (!alive) return
        setUrl(nextUrl)
        const list = data.overlays.map(o => normalizeOverlay({ ...o, media_id: media.id }))
        latest.current = list
        setOverlays(list)
        setSelected(list.length ? 0 : null)
        setScene(data.scene)
        setIssues(data.issues)
        setPresets(templates.templates)
        setMarkers(pins)
      }).catch(cause => { if (alive) setError(errorMessage(cause)) })
    return () => { alive = false }
  }, [media.id])

  // Smooth clock while playing (timeupdate fires only ~4 times a second).
  useEffect(() => {
    if (!playing) return
    let frame = 0
    const tick = () => { if (video.current) setNow(video.current.currentTime * 1000); frame = requestAnimationFrame(tick) }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [playing])

  const save = useCallback(async () => {
    window.clearTimeout(timer.current)
    if (latest.current.some(o => localProblem(o))) { setSaving('blocked'); return }
    setSaving('saving')
    try {
      const data = await studioClient.setOverlays(media.id, latest.current)
      setScene(data.scene)
      setIssues(data.issues)
      setSaving('saved')
      setError('')
    } catch (cause) { setSaving('error'); setError(errorMessage(cause)) }
  }, [media.id])

  const change = (next: Overlay[], select: number | null = selected) => {
    latest.current = next
    setOverlays(next)
    setSelected(select)
    setSaving('pending')
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => void save(), 700)
  }
  const patch = (index: number, value: Partial<Overlay>) => change(latest.current.map((o, i) => i === index ? { ...o, ...value } : o))
  const close = async () => { if (saving === 'pending') await save(); onClose() }
  useEffect(() => () => window.clearTimeout(timer.current), [])

  const length = Math.max(duration, ...overlays.map(endOf), 1000)
  const seek = (ms: number) => { const at = Math.max(0, Math.min(ms, length)); if (video.current) video.current.currentTime = at / 1000; setNow(at) }
  const togglePlay = () => { const v = video.current; if (v) void (v.paused ? v.play() : v.pause()) }
  /** Replay the entrance (or exit) of an overlay on the preview. */
  const previewAnimation = (index: number, part: 'in' | 'out') => {
    const o = overlays[index]
    seek(part === 'in' ? o.start_ms : Math.max(o.start_ms, endOf(o) - o.animation_out.duration_ms - 400))
    void video.current?.play()
  }
  // The scene the preview draws: the draft (instant), with the products/images the core resolved.
  const preview: OverlayScene = useMemo(() => {
    const productsOf: OverlayScene['products'] = { ...scene.products }
    for (const o of overlays) for (const id of [o.product_id, ...o.elements.map(e => 'product_id' in e ? e.product_id : null)]) {
      const p = id ? productById.get(id) : undefined
      if (p && id && !productsOf[id]) productsOf[id] = { name: p.name, sku: p.tiktok_product_id ?? '', price: p.price, origin_price: p.origin_price }
    }
    return { ...scene, overlays, products: productsOf }
  }, [scene, overlays, productById])
  const current = selected !== null ? overlays[selected] : undefined
  const assetUrl = (id: string | null) => id && scene.assets[id] ? safeImageUrl(scene.assets[id].url, scene.api_base) ?? undefined : undefined
  const thumbOf = (o: Overlay) => {
    const own = o.elements.map(e => e.kind === 'product_field' || e.kind === 'image' ? e.product_id : null).find(Boolean)
    const fromProduct = productThumb(productById.get(o.product_id ?? own ?? ''))
    if (fromProduct) return fromProduct
    const image = o.elements.find(e => e.kind === 'image' && e.source === 'asset' && e.asset_id)
    return image?.kind === 'image' ? assetUrl(image.asset_id) : undefined
  }
  const addPreset = (preset: OverlayPreset | null) => {
    const start = Math.min(Math.round(now / 100) * 100, Math.max(0, length - 3000))
    const fresh = fromPreset(preset, media.id, start, media.product_id && productById.get(media.product_id)?.source === 'tiktok' ? media.product_id : null)
    fresh.duration_ms = Math.min(fresh.duration_ms, Math.max(500, length - start))
    change([...overlays, fresh], overlays.length)
    setLibrary(false)
  }
  const duplicate = (index: number) => {
    const o = overlays[index]
    change([...overlays, structuredClone({ ...o, start_ms: Math.min(endOf(o), Math.max(0, length - o.duration_ms)) })], overlays.length)
  }
  const remove = (index: number) => change(overlays.filter((_, i) => i !== index), overlays.length > 1 ? Math.max(0, index - 1) : null)
  const toggle = (index: number) => patch(index, { enabled: overlays[index].enabled === false })
  const general = issues.filter(i => i.overlay === undefined || i.overlay === null)
  const stateText = { idle: '', pending: 'Chưa lưu…', saving: 'Đang lưu…', saved: 'Đã lưu', error: 'Chưa lưu được', blocked: 'Chưa lưu: còn thiếu thông tin' }[saving]

  return <Modal title={`Overlay · ${media.original_name}`} onClose={() => void close()} wide>
    <div className="studio-overlay-editor">
      <div className="ovs-shell">
        <section className="ovs-stage">
          <div className="ovs-stage-tools">
            <div className="studio-segmented" role="group" aria-label="Kiểu xem">
              <button type="button" className={mode === 'obs' ? 'active' : ''} aria-pressed={mode === 'obs'} onClick={() => setMode('obs')}><MonitorPlay size={13} />OBS</button>
              <button type="button" className={mode === 'tiktok' ? 'active' : ''} aria-pressed={mode === 'tiktok'} onClick={() => setMode('tiktok')}><Smartphone size={13} />Người xem TikTok</button>
            </div>
            <label className="studio-switch"><input type="checkbox" role="switch" checked={zones} onChange={e => setZones(e.target.checked)} /><span aria-hidden="true" /><small>Vùng an toàn</small></label>
          </div>
          <OverlayPreview scene={preview} mediaId={media.id} now={now} animate={playing} apiBase={scene.api_base} mode={mode} showZones={zones}
            selected={selected} editable onSelect={setSelected} onGestureEnd={() => {}}
            onLayout={(index: number, layout: OverlayLayout) => patch(index, { layout })}>
            {url ? <video ref={video} src={url} playsInline preload="metadata" onClick={togglePlay}
              onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onTimeUpdate={e => { if (!playing) setNow(e.currentTarget.currentTime * 1000) }}
              onLoadedMetadata={e => { if (Number.isFinite(e.currentTarget.duration)) setDuration(e.currentTarget.duration * 1000) }} /> : <p>Đang mở video…</p>}
          </OverlayPreview>
          <div className="ovs-transport">
            <button type="button" className="ovs-play" aria-label={playing ? 'Tạm dừng' : 'Phát'} onClick={togglePlay}>{playing ? <Pause size={16} /> : <Play size={16} />}</button>
            <input type="range" className="ovs-scrub" aria-label="Tua video" min={0} max={Math.round(length)} step={100} value={Math.round(now)} onChange={e => seek(Number(e.target.value))} />
            <span className="ovs-time"><strong>{clock(now, true)}</strong> / {clock(length)}</span>
          </div>
        </section>

        <div className="ovs-side">
          <section className="ovs-card">
            <header className="ovs-card-head">
              <span className="te-card-title"><Layers size={17} />Overlay của video <b className="ovs-count">{overlays.length}</b></span>
              {stateText && <span className={`studio-save-state ${saving}`}>{saving === 'saved' && <Check size={13} />}{stateText}</span>}
              <button type="button" className="studio-button primary ovs-add" onClick={() => setLibrary(true)}><Plus size={15} />Thêm overlay</button>
            </header>
            {general.length > 0 && <ul className="te-issues">{general.map(i => <li key={i.code + (i.at_ms ?? '')} className={`is-${i.level}`}><CircleAlert size={14} />
              <span>{i.message}</span>{i.at_ms != null && <button type="button" className="ove-link" onClick={() => seek(i.at_ms!)}>Tua tới</button>}</li>)}</ul>}
            {!overlays.length
              ? <EmptyState icon={<Layers size={26} />} title="Video này chưa có overlay" description="Tua tới chỗ cần hiện rồi bấm “Thêm overlay”, chọn một mẫu hoặc khung trống." />
              : <ol className="ove-items">{overlays.map((o, i) => {
                const errors = issues.filter(x => x.overlay === i)
                const problem = localProblem(o)
                const on = o.enabled !== false
                const product = o.product_id ? productById.get(o.product_id) : undefined
                return <li key={i} className={'ove-card' + (i === selected ? ' is-selected' : '') + (on ? '' : ' is-off')}>
                  <button type="button" className="ove-card-main" onClick={() => { setSelected(i); seek(o.start_ms + Math.min(o.animation_in.duration_ms, 400)) }}>
                    <span className="ove-radio" aria-hidden />
                    <Thumb src={thumbOf(o)} />
                    <span className="ove-card-text">
                      <span className="ove-card-title"><strong>{overlayName(o, presets)}</strong><span className={'st-chip' + (on ? ' is-on' : '')}>{on ? 'Đang bật' : 'Tạm ẩn'}</span>
                        {errors.some(x => x.level === 'error') ? <span className="ovs-badge danger">Lỗi</span> : (errors.length || problem) ? <span className="ovs-badge warning">Chú ý</span> : null}</span>
                      <small>{clock(o.start_ms)} – {clock(endOf(o))} ({secondsText(o.duration_ms)}) · {trackName(o.template, presets)}</small>
                      {product && <span className="ove-card-product">{product.name}</span>}
                    </span>
                  </button>
                  <ItemMenu on={on} onDuplicate={() => duplicate(i)} onToggle={() => toggle(i)} onRemove={() => remove(i)} />
                </li>
              })}</ol>}
          </section>

          {current && selected !== null
            ? <Inspector key={selected} overlay={current} presets={presets} products={pinnable} productById={productById} issues={issues.filter(i => i.overlay === selected)}
              problem={localProblem(current)} assetUrl={assetUrl} now={now} on={current.enabled !== false}
              onChange={value => patch(selected, value)} onPickImage={block => setPicking(block)} onPreview={part => previewAnimation(selected, part)}
              onDuplicate={() => duplicate(selected)} onRemove={() => remove(selected)} onToggle={() => toggle(selected)} />
            : <section className="ovs-card"><EmptyState icon={<Layers size={26} />} title="Chọn một overlay" description="Bấm vào overlay trên khung xem trước, trên dòng thời gian hoặc trong danh sách để sửa." /></section>}
        </div>
      </div>

      <Timeline overlays={overlays} presets={presets} selected={selected} now={now} length={length} markers={markers}
        onSeek={seek} onSelect={setSelected} onToggle={toggle} onMove={(index, start) => patch(index, { start_ms: start })} />

      {error && <ErrorNotice message={error} />}
      <div className="ovs-foot">
        <span className="studio-muted">Overlay hiện trong OBS (nguồn “Sarae Studio Overlay”) khi video này phát LIVE, đúng các mốc trên. Thay đổi tự lưu.</span>
        <button type="button" className="studio-button primary" onClick={() => void close()}>Xong</button>
      </div>

      {library && <Modal title={`Thêm overlay tại ${clock(now)}`} onClose={() => setLibrary(false)} wide>
        <TemplateLibrary presets={presets} onPick={addPreset} />
      </Modal>}
      {picking !== null && current && selected !== null && <AssetPicker apiBase={scene.api_base} selected={(() => { const e = current.elements[picking]; return e?.kind === 'image' ? e.asset_id : null })()}
        onClose={() => setPicking(null)} onPick={asset => {
          const elements = current.elements.map((e, i) => i === picking && e.kind === 'image' ? { ...e, source: 'asset' as const, asset_id: asset.id, product_id: null } : e)
          setScene(s => ({ ...s, assets: { ...s.assets, [asset.id]: { url: asset.url, mime_type: asset.mime_type, width: asset.width, height: asset.height } } }))
          patch(selected, { elements })
          setPicking(null)
        }} />}
    </div>
  </Modal>
}

function Thumb({ src, size }: { src?: string; size?: 'sm' }) {
  const [broken, setBroken] = useState(false)
  return <span className={`ove-card-thumb${size === 'sm' ? ' is-sm' : ''}`}>{src && !broken ? <img src={src} alt="" loading="lazy" onError={() => setBroken(true)} /> : <Layers size={20} />}</span>
}

/** ⋮ menu of an overlay card. */
function ItemMenu({ on, onDuplicate, onToggle, onRemove }: { on: boolean; onDuplicate: () => void; onToggle: () => void; onRemove: () => void }) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const closeMenu = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', closeMenu)
    return () => document.removeEventListener('mousedown', closeMenu)
  }, [open])
  const item = (label: string, icon: ReactNode, run: () => void, danger = false) => <button type="button" role="menuitem" className={danger ? 'is-danger' : undefined}
    onClick={() => { setOpen(false); run() }}>{icon}{label}</button>
  return <div ref={box} className="ove-menu">
    <button type="button" className="te-icon-btn" aria-label="Thao tác" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(v => !v)}><MoreVertical size={16} /></button>
    {open && <div className="ove-menu-list" role="menu">
      {item('Nhân bản', <CopyPlus size={14} />, onDuplicate)}
      {item(on ? 'Tạm ẩn' : 'Bật', on ? <EyeOff size={14} /> : <Eye size={14} />, onToggle)}
      {item('Xóa', <Trash2 size={14} />, onRemove, true)}
    </div>}
  </div>
}

/** Built-in presets: a starting point, edited freely after adding. */
function TemplateLibrary({ presets, onPick }: { presets: OverlayPreset[]; onPick: (preset: OverlayPreset | null) => void }) {
  const card = (preset: OverlayPreset | null) => {
    const Icon = preset ? PRESET_ICON[preset.template] ?? Layers : Plus
    return <button key={preset?.template ?? 'blank'} type="button" className="ove-preset" onClick={() => onPick(preset)}>
      <span className="ovs-preset-icon"><Icon size={18} /></span>
      <strong>{preset ? preset.label : 'Khung trống'}</strong>
      <small>{preset ? `${preset.template || 'không nhãn'} · ${preset.elements.length} khối${preset.duration_ms ? ` · ${preset.duration_ms / 1000}s` : ''}` : 'Nền + một dòng chữ'}</small>
    </button>
  }
  return <div className="studio-overlay-editor ovs-library">
    <p className="te-muted">Mẫu chỉ là điểm bắt đầu: sau khi thêm, sửa tự do các khối, vị trí, nhãn. Overlay bắt đầu tại vị trí đang tua của video.</p>
    <h4 className="ove-preset-group">Mẫu có sẵn</h4>
    <div className="ove-presets">{[null, ...presets].map(card)}</div>
  </div>
}

// ----- inspector -----

type InspectorProps = {
  overlay: Overlay; presets: OverlayPreset[]; products: Product[]; productById: Map<string, Product>; issues: OverlayIssue[]; problem: string | null
  assetUrl: (id: string | null) => string | undefined; now: number; on: boolean
  onChange: (value: Partial<Overlay>) => void; onPickImage: (block: number) => void; onPreview: (part: 'in' | 'out') => void
  onDuplicate: () => void; onRemove: () => void; onToggle: () => void
}

function Inspector(p: InspectorProps) {
  const { overlay: o } = p
  const layout = (value: Partial<Frame & { z_index: number; opacity: number }>) => {
    const merged = { ...o.layout, ...value }
    const c = clampFrame(merged, 0.005)
    p.onChange({ layout: { ...merged, x: round4(c.x), y: round4(c.y), width: round4(c.width), height: round4(c.height) } })
  }
  const setBlock = (n: number, next: OverlayElement) => p.onChange({ elements: o.elements.map((e, i) => i === n ? next : e) })
  const moveBlock = (n: number, to: number) => {
    if (to < 0 || to >= o.elements.length) return
    const list = [...o.elements];[list[n], list[to]] = [list[to], list[n]]
    p.onChange({ elements: list })
  }
  const product = o.product_id ? p.productById.get(o.product_id) : undefined
  const productOptions = p.products.map(x => ({ value: x.id, label: x.name }))
  const labels = [...new Set(p.presets.map(x => x.template).filter(Boolean))]
  const problems = [...(p.problem ? [{ level: 'warning' as const, code: 'LOCAL', message: p.problem }] : []), ...p.issues]
  return <section className="ovs-card ove-inspector">
    <header className="ovs-card-head">
      <span className="te-card-title"><Layers size={17} />Thuộc tính <span className="ove-insp-name">{overlayName(o, p.presets)}</span></span>
      <div className="ove-toolbar">
        <span className={'st-chip' + (p.on ? ' is-on' : '')}>{p.on ? 'Đang bật' : 'Tạm ẩn'}</span>
        <button type="button" className="te-icon-btn" title="Xem hiệu ứng vào" aria-label="Xem hiệu ứng vào" onClick={() => p.onPreview('in')}><Play size={15} /></button>
        <button type="button" className="studio-button ghost ovs-sm" onClick={p.onDuplicate}><CopyPlus size={14} />Nhân bản</button>
        <button type="button" className="studio-button ghost ovs-sm ove-danger" onClick={p.onRemove}><Trash2 size={14} />Xóa</button>
      </div>
    </header>
    <div className="ove-form">
      {problems.length > 0 && <ul className="te-issues ove-issues">{problems.map(i => <li key={i.code + i.message} className={`is-${i.level}`}><CircleAlert size={14} />{i.message}</li>)}</ul>}
      <div className="ove-cols">
        <section className="ove-col">
          <h4 className="ove-col-title">Thông tin cơ bản</h4>
          <Switch label={p.on ? 'Đang bật' : 'Tạm ẩn'} checked={p.on} onChange={p.onToggle} />
          <div className="ove-row ove-2">
            <TextField label="Tên hiển thị" placeholder="(không bắt buộc)" maxLength={200} value={o.label} onChange={v => p.onChange({ label: v })} />
            <TextField label="Nhãn (track)" placeholder="VD: SIZE_CHART" maxLength={40} list="ovs-labels" value={o.template}
              onChange={v => p.onChange({ template: v.toUpperCase().replace(/[^A-Z0-9_-]/g, '_') })} />
            <datalist id="ovs-labels">{labels.map(l => <option key={l} value={l}>{trackName(l, p.presets)}</option>)}</datalist>
          </div>

          <h4 className="ove-col-title">Thời gian hiển thị</h4>
          <div className="ove-row ove-3">
            <NumberField label="Bắt đầu (giây)" min={0} step={0.1} value={o.start_ms / 1000} onChange={v => p.onChange({ start_ms: Math.max(0, Math.round(v * 1000)) })} />
            <NumberField label="Thời lượng (giây)" min={0.5} step={0.1} value={o.duration_ms / 1000} onChange={v => p.onChange({ duration_ms: Math.max(500, Math.round(v * 1000)) })} />
            <div className="ove-field-note"><span className="te-muted">Kết thúc</span><strong>{clock(endOf(o), true)}</strong>
              <button type="button" className="ove-link" onClick={() => p.onChange({ start_ms: Math.round(p.now / 100) * 100 })}><Crosshair size={13} />Bắt đầu tại {clock(p.now)}</button></div>
          </div>

          <details className="ove-more">
            <summary>Vị trí, lớp, độ trong suốt & hiệu ứng <small className="te-muted">(kéo trên khung xem trước cũng được)</small></summary>
            <h4>Vị trí & kích thước <small className="te-muted">(px trên khung 1080×1920)</small></h4>
            <div className="ove-row ove-4">
              <NumberField label="X" min={0} max={CANVAS.width} value={px(o.layout.x, CANVAS.width)} onChange={v => layout({ x: v / CANVAS.width })} />
              <NumberField label="Y" min={0} max={CANVAS.height} value={px(o.layout.y, CANVAS.height)} onChange={v => layout({ y: v / CANVAS.height })} />
              <NumberField label="Rộng" min={1} max={CANVAS.width} value={px(o.layout.width, CANVAS.width)} onChange={v => layout({ width: v / CANVAS.width })} />
              <NumberField label="Cao" min={1} max={CANVAS.height} value={px(o.layout.height, CANVAS.height)} onChange={v => layout({ height: v / CANVAS.height })} />
            </div>
            <div className="ove-row ove-2">
              <NumberField label="Lớp (0–100)" min={0} max={100} step={1} value={o.layout.z_index} onChange={v => layout({ z_index: Math.max(0, Math.min(100, Math.round(v))) })} />
              <Range label="Độ trong suốt" value={o.layout.opacity} onChange={v => layout({ opacity: v })} />
            </div>
            <h4>Hiệu ứng</h4>
            <div className="ove-row ove-2">
              <AnimationField label="Vào" value={o.animation_in} onChange={v => p.onChange({ animation_in: v })} onPreview={() => p.onPreview('in')} />
              <AnimationField label="Ra" value={o.animation_out} onChange={v => p.onChange({ animation_out: v })} onPreview={() => p.onPreview('out')} />
            </div>
          </details>
        </section>

        <section className="ove-col">
          <h4 className="ove-col-title">Nội dung hiển thị</h4>
          <h4>Sản phẩm của overlay</h4>
          {product ? <div className="ove-product">
            <Thumb src={productThumb(product)} size="sm" />
            <span className="ove-product-name" title={product.name}>{product.name}</span>
            <button type="button" className="te-icon-btn" aria-label="Bỏ sản phẩm" title="Bỏ sản phẩm của overlay" onClick={() => p.onChange({ product_id: null })}><X size={15} /></button>
          </div> : <SelectField label="" ariaLabel="Sản phẩm của overlay" value="" onChange={v => p.onChange({ product_id: v || null })}
            options={[{ value: '', label: o.product_id ? 'Sản phẩm đã bị xóa — chọn lại' : 'Không có' }, ...productOptions]} />}
          <small className="te-muted">Các khối thông tin / ảnh sản phẩm không chọn sản phẩm riêng sẽ đọc sản phẩm này.</small>

          <h4>Các khối ({o.elements.length}/{MAX_ELEMENTS}) <small className="te-muted">khối sau đè lên khối trước</small></h4>
          <ol className="ove-blocks">{o.elements.map((e, n) => <BlockEditor key={n} element={e} index={n} count={o.elements.length} products={productOptions} assetUrl={p.assetUrl}
            onChange={next => setBlock(n, next)} onMove={to => moveBlock(n, to)} onPickImage={() => p.onPickImage(n)}
            onRemove={() => p.onChange({ elements: o.elements.filter((_, i) => i !== n) })} />)}</ol>
          {o.elements.length < MAX_ELEMENTS && <div className="ove-add-blocks">
            {(Object.keys(BLOCK_LABEL) as BlockKind[]).map(kind => {
              const Icon = BLOCK_ICON[kind]
              return <button key={kind} type="button" className="studio-button ovs-sm" onClick={() => p.onChange({ elements: [...o.elements, normalizeElement(NEW_BLOCK[kind])] })}><Icon size={14} />{BLOCK_LABEL[kind]}</button>
            })}
          </div>}
        </section>
      </div>
    </div>
  </section>
}

// ----- fields -----

function TextField({ label, value, onChange, placeholder, maxLength, list }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string; maxLength?: number; list?: string }) {
  return <label className="studio-field">{label}<input value={value} placeholder={placeholder} maxLength={maxLength} list={list} onChange={e => onChange(e.target.value)} /></label>
}

/** A number input that lets the user type freely and applies the value as it becomes valid. */
function NumberField({ label, value, onChange, min, max, step = 1 }: { label: string; value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number }) {
  const shown = String(Math.round(value * 1000) / 1000)
  const [text, setText] = useState(shown)
  const [focused, setFocused] = useState(false)
  return <label className="studio-field">{label}<input type="number" inputMode="decimal" min={min} max={max} step={step} value={focused ? text : shown}
    onFocus={() => { setFocused(true); setText(shown) }} onBlur={() => setFocused(false)}
    onChange={e => { setText(e.target.value); if (e.target.value.trim() !== '') onChange(numberOr(e.target.value, value)) }} /></label>
}

function SelectField({ label, ariaLabel, value, options, onChange }: { label: string; ariaLabel?: string; value: string; options: { value: string; label: string }[]; onChange: (v: string) => void }) {
  return <label className="studio-field">{label}<select aria-label={ariaLabel} value={value} onChange={e => onChange(e.target.value)}>{options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select></label>
}

function Switch({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return <label className="studio-switch ovs-switch"><input type="checkbox" role="switch" checked={checked} onChange={e => onChange(e.target.checked)} /><span aria-hidden="true" /><small>{label}</small></label>
}

function Range({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return <label className="ove-range"><span>{label} <b>{Math.round(value * 100)}%</b></span>
    <input type="range" min={0} max={100} step={1} value={Math.round(value * 100)} onChange={e => onChange(Number(e.target.value) / 100)} /></label>
}

function AnimationField({ label, value, onChange, onPreview }: { label: string; value: Animation; onChange: (v: Animation) => void; onPreview: () => void }) {
  return <div className="ove-anim">
    <SelectField label={label} value={value.name} onChange={v => onChange({ ...value, name: v as AnimationName })} options={ANIMATIONS.map(a => ({ value: a, label: ANIMATION_LABEL[a] }))} />
    <NumberField label="ms" min={0} max={5000} step={50} value={value.duration_ms} onChange={v => onChange({ ...value, duration_ms: Math.max(0, Math.min(5000, Math.round(v))) })} />
    <button type="button" className="te-icon-btn" title={`Xem hiệu ứng ${label.toLowerCase()}`} aria-label={`Xem hiệu ứng ${label.toLowerCase()}`} onClick={onPreview}><Play size={14} /></button>
  </div>
}

/** #RRGGBB / #RRGGBBAA with a colour picker for the RGB part (the alpha part is kept). */
function ColorField({ label, value, onChange, optional }: { label: string; value: string | null; onChange: (v: string | null) => void; optional?: boolean }) {
  const valid = value == null || /^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$/.test(value)
  return <div className={`ove-color${valid ? '' : ' is-invalid'}`}>
    <label className="studio-field">{label}<input placeholder={optional ? 'không' : '#RRGGBB'} value={value ?? ''} maxLength={9}
      onChange={e => onChange(e.target.value ? e.target.value : optional ? null : '#000000')} /></label>
    <input type="color" aria-label={`${label}: chọn màu`} value={(value && valid ? value : '#000000').slice(0, 7)} onChange={e => onChange(e.target.value.toUpperCase() + (value && valid ? value.slice(7) : ''))} />
  </div>
}

function BlockEditor({ element: e, index, count, products, assetUrl, onChange, onMove, onRemove, onPickImage }: {
  element: OverlayElement; index: number; count: number; products: { value: string; label: string }[]; assetUrl: (id: string | null) => string | undefined
  onChange: (next: OverlayElement) => void; onMove: (to: number) => void; onRemove: () => void; onPickImage: () => void
}) {
  const [open, setOpen] = useState(false)
  const Icon = BLOCK_ICON[e.kind]
  const frame = (value: Partial<Frame>) => { const c = clampFrame({ ...e.frame, ...value }, 0.01); onChange({ ...e, frame: { x: round4(c.x), y: round4(c.y), width: round4(c.width), height: round4(c.height) } }) }
  const style = (value: Partial<TextStyle>) => { if ('style' in e) onChange({ ...e, style: { ...e.style, ...value } } as OverlayElement) }
  const summary = e.kind === 'text' ? e.text : e.kind === 'product_field' ? FIELD_LABEL[e.field] : e.kind === 'image' ? (e.source === 'product' ? 'Ảnh sản phẩm' : e.asset_id ? 'Ảnh từ kho' : 'Chưa chọn ảnh') : e.shape === 'ellipse' ? 'Elip' : 'Chữ nhật'
  const byOverlay = [{ value: '', label: 'Theo overlay' }, ...products]
  return <li className={'ove-block' + (open ? ' is-open' : '')}>
    <div className="ove-block-head">
      <button type="button" className="ove-block-toggle" onClick={() => setOpen(v => !v)} aria-expanded={open}><Icon size={14} /><b>#{index + 1} {BLOCK_LABEL[e.kind]}</b><span>{summary}</span></button>
      <button type="button" className="te-icon-btn" aria-label="Lên" title="Vẽ trước (xuống dưới)" disabled={index === 0} onClick={() => onMove(index - 1)}><ArrowUp size={14} /></button>
      <button type="button" className="te-icon-btn" aria-label="Xuống" title="Vẽ sau (đè lên trên)" disabled={index === count - 1} onClick={() => onMove(index + 1)}><ArrowDown size={14} /></button>
      <button type="button" className="te-icon-btn is-danger" aria-label="Xóa khối" title="Xóa khối" disabled={count <= 1} onClick={onRemove}><Trash2 size={14} /></button>
    </div>
    {open && <div className="ove-block-body">
      {e.kind === 'text' && <TextField label="Chữ" maxLength={500} value={e.text} onChange={v => onChange({ ...e, text: v })} />}
      {e.kind === 'product_field' && <div className="ove-row ove-2">
        <SelectField label="Trường" value={e.field} onChange={v => onChange({ ...e, field: v as typeof e.field })} options={PRODUCT_FIELDS.map(f => ({ value: f, label: FIELD_LABEL[f] }))} />
        <SelectField label="Sản phẩm" value={e.product_id ?? ''} onChange={v => onChange({ ...e, product_id: v || null })} options={byOverlay} />
        <TextField label="Tiền tố" maxLength={60} value={e.prefix} onChange={v => onChange({ ...e, prefix: v })} />
        <TextField label="Hậu tố" maxLength={60} value={e.suffix} onChange={v => onChange({ ...e, suffix: v })} />
      </div>}
      {(e.kind === 'text' || e.kind === 'product_field') && <div className="ove-row ove-3">
        <NumberField label="Cỡ chữ (px)" min={8} max={400} value={e.style.font_size} onChange={v => style({ font_size: Math.max(8, Math.min(400, v)) })} />
        <SelectField label="Độ đậm" value={String(e.style.font_weight)} onChange={v => style({ font_weight: Number(v) })} options={[300, 400, 500, 600, 700, 800, 900].map(w => ({ value: String(w), label: String(w) }))} />
        <SelectField label="Căn" value={e.style.align} onChange={v => style({ align: v as TextStyle['align'] })} options={[{ value: 'left', label: 'Trái' }, { value: 'center', label: 'Giữa' }, { value: 'right', label: 'Phải' }]} />
        <ColorField label="Màu chữ" value={e.style.color} onChange={v => style({ color: v ?? '#FFFFFF' })} />
        <ColorField label="Nền chữ" optional value={e.style.background} onChange={v => style({ background: v })} />
        <NumberField label="Tối đa dòng (0 = không giới hạn)" min={0} max={20} value={e.style.max_lines} onChange={v => style({ max_lines: Math.max(0, Math.min(20, Math.round(v))) })} />
        <Switch label="Đổ bóng chữ" checked={!!e.style.shadow} onChange={checked => style({ shadow: checked })} />
      </div>}
      {e.kind === 'image' && <div className="ove-row ove-2">
        <SelectField label="Nguồn ảnh" value={e.source} onChange={v => onChange({ ...e, source: v as 'asset' | 'product', ...(v === 'product' ? { asset_id: null } : { product_id: null }) })}
          options={[{ value: 'asset', label: 'Kho ảnh overlay' }, { value: 'product', label: 'Ảnh sản phẩm' }]} />
        {e.source === 'asset'
          ? <div className="ove-asset-pick">
            {assetUrl(e.asset_id) ? <img src={assetUrl(e.asset_id)} alt="" /> : <span className="ove-asset-empty">{e.asset_id ? 'Ảnh đã bị xóa' : 'Chưa chọn'}</span>}
            <button type="button" className="studio-button ovs-sm" onClick={onPickImage}><ImageIcon size={14} />Chọn ảnh</button>
          </div>
          : <SelectField label="Sản phẩm" value={e.product_id ?? ''} onChange={v => onChange({ ...e, product_id: v || null })} options={byOverlay} />}
        <SelectField label="Cách lấp khung" value={e.fit} onChange={v => onChange({ ...e, fit: v as 'contain' | 'cover' })} options={[{ value: 'contain', label: 'Vừa khung (giữ toàn ảnh)' }, { value: 'cover', label: 'Phủ kín (cắt bớt)' }]} />
        <Range label="Bo góc" value={e.radius * 2} onChange={v => onChange({ ...e, radius: round4(v / 2) })} />
      </div>}
      {e.kind === 'shape' && <div className="ove-row ove-3">
        <SelectField label="Hình" value={e.shape} onChange={v => onChange({ ...e, shape: v as 'rect' | 'ellipse' })} options={[{ value: 'rect', label: 'Chữ nhật' }, { value: 'ellipse', label: 'Elip' }]} />
        <ColorField label="Màu nền" optional value={e.fill} onChange={v => onChange({ ...e, fill: v })} />
        <ColorField label="Viền" optional value={e.stroke} onChange={v => onChange({ ...e, stroke: v })} />
        <NumberField label="Độ dày viền (px)" min={0} max={40} value={e.stroke_width} onChange={v => onChange({ ...e, stroke_width: Math.max(0, Math.min(40, v)) })} />
        <Range label="Bo góc" value={e.radius * 2} onChange={v => onChange({ ...e, radius: round4(v / 2) })} />
      </div>}
      <h5>Khung trong overlay <small className="te-muted">(% của khung overlay)</small></h5>
      <div className="ove-row ove-4">
        {(['x', 'y', 'width', 'height'] as const).map(k => <NumberField key={k} label={{ x: 'X', y: 'Y', width: 'Rộng', height: 'Cao' }[k]} min={0} max={100} step={1}
          value={Math.round(e.frame[k] * 1000) / 10} onChange={v => frame({ [k]: v / 100 })} />)}
      </div>
      <Range label="Độ trong suốt của khối" value={e.opacity} onChange={v => onChange({ ...e, opacity: v })} />
    </div>}
  </li>
}

// ----- timeline -----

const TICK_STEPS = [1000, 2000, 5000, 10000, 15000, 30000, 60000, 120000, 300000, 600000]

/** Ruler + one row per overlay (like dev's studio timeline): click the ruler to seek, drag a bar to move it. */
function Timeline({ overlays, presets, selected, now, length, markers, onSeek, onSelect, onToggle, onMove }: {
  overlays: Overlay[]; presets: OverlayPreset[]; selected: number | null; now: number; length: number; markers: MediaMarker[]
  onSeek: (ms: number) => void; onSelect: (index: number) => void; onToggle: (index: number) => void; onMove: (index: number, start: number) => void
}) {
  const step = TICK_STEPS.find(s => length / s <= 12) ?? 600000
  const ticks = Array.from({ length: Math.floor(length / step) + 1 }, (_, i) => i * step)
  const pct = (ms: number) => `${Math.max(0, Math.min(100, ms / length * 100))}%`
  const drag = useRef<{ index: number; x: number; start: number; width: number; moved: boolean } | null>(null)
  const seekAt = (event: ReactPointerEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect()
    onSeek((event.clientX - box.left) / box.width * length)
  }
  const down = (event: ReactPointerEvent<HTMLButtonElement>, index: number) => {
    event.stopPropagation()
    const lane = event.currentTarget.parentElement!.getBoundingClientRect()
    drag.current = { index, x: event.clientX, start: overlays[index].start_ms, width: lane.width, moved: false }
    event.currentTarget.setPointerCapture(event.pointerId)
    onSelect(index)
  }
  const move = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const d = drag.current
    if (!d) return
    if (!d.moved && Math.abs(event.clientX - d.x) < 3) return
    d.moved = true
    const o = overlays[d.index]
    const delta = (event.clientX - d.x) / d.width * length
    onMove(d.index, Math.round(Math.max(0, Math.min(length - o.duration_ms, d.start + delta)) / 100) * 100)
  }
  const up = (index: number) => {
    const d = drag.current
    drag.current = null
    if (d && !d.moved) onSeek(overlays[index].start_ms + Math.min(overlays[index].animation_in.duration_ms, 400))
  }
  return <section className="ovs-card ovs-timeline">
    <header className="ovs-card-head">
      <span className="te-card-title"><ScanLine size={17} />Dòng thời gian</span>
      <small className="te-muted">Bấm thước để tua · kéo thanh để dời overlay{markers.length ? ' · biểu tượng ghim là mốc ghim sản phẩm của video' : ''}</small>
    </header>
    <div className="ovs-tl">
      <div className="ovs-tl-row ovs-tl-ruler">
        <div className="ovs-tl-head"><span className="ovs-tl-clock">{clock(now, true)}</span></div>
        <div className="ovs-tl-lane" onPointerDown={seekAt}>
          {ticks.map(t => <span key={t} className={`ovs-tick${t > 0 && length - t < step * 0.6 ? ' is-end' : ''}`} style={{ left: pct(t) }}><small>{clock(t)}</small></span>)}
          {markers.map(m => <span key={m.id} className={`ovs-pin${m.action === 'PIN_PRODUCT' ? '' : ' is-unpin'}`} style={{ left: pct(m.at_ms) }}
            title={`${clock(m.at_ms)} · ${m.action === 'PIN_PRODUCT' ? `Ghim ${m.product_name ?? ''}` : 'Bỏ ghim'}`}><Pin size={9} /></span>)}
          <i className="ovs-playhead" style={{ left: pct(now) }} />
        </div>
      </div>
      {!overlays.length && <div className="ovs-tl-row"><div className="ovs-tl-head" /><div className="ovs-tl-lane ovs-tl-empty">Chưa có overlay</div></div>}
      {overlays.map((o, i) => {
        const on = o.enabled !== false
        return <div key={i} className={`ovs-tl-row${i === selected ? ' is-selected' : ''}`}>
          <div className="ovs-tl-head">
            <button type="button" className="st-eye" aria-label={on ? 'Tạm ẩn' : 'Bật'} title={on ? 'Đang bật — bấm để ẩn' : 'Đang ẩn — bấm để bật'} onClick={() => onToggle(i)}>{on ? <Eye size={14} /> : <EyeOff size={14} />}</button>
            <button type="button" className="ovs-tl-name" onClick={() => onSelect(i)} title={overlayName(o, presets)}>{overlayName(o, presets)}</button>
          </div>
          <div className="ovs-tl-lane" onPointerDown={seekAt}>
            <button type="button" className={`ovs-tl-bar${i === selected ? ' is-selected' : ''}${on ? '' : ' is-off'}`} style={{ left: pct(o.start_ms), width: `max(6px, ${o.duration_ms / length * 100}%)` }}
              onPointerDown={e => down(e, i)} onPointerMove={move} onPointerUp={() => up(i)} onPointerCancel={() => { drag.current = null }}
              title={`${overlayName(o, presets)} · ${clock(o.start_ms)} – ${clock(endOf(o))}`}>
              <span>{overlayName(o, presets)}</span>
            </button>
            <i className="ovs-playhead" style={{ left: pct(now) }} />
          </div>
        </div>
      })}
    </div>
  </section>
}

// ----- image library -----

function AssetPicker({ apiBase, selected, onPick, onClose }: { apiBase: string; selected: string | null; onPick: (asset: OverlayAsset) => void; onClose: () => void }) {
  const [assets, setAssets] = useState<OverlayAsset[]>([])
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const load = async () => {
    try { setAssets(await studioClient.listOverlayAssets()) } catch (cause) { setError(errorMessage(cause)) } finally { setLoaded(true) }
  }
  useEffect(() => { void load() }, [])
  const upload = async (source: File | string) => {
    setBusy(true)
    setError('')
    try { onPick(await studioClient.uploadOverlayAsset(source)) } catch (cause) { setError(errorMessage(cause)); setBusy(false) }
  }
  const choose = async () => {
    if (!isTauri()) { input.current?.click(); return }
    const path = await open({ multiple: false, directory: false, title: 'Chọn ảnh overlay', filters: [{ name: 'Ảnh', extensions: ['png', 'jpg', 'jpeg', 'webp'] }] })
    if (typeof path === 'string') await upload(path)
  }
  const remove = async (asset: OverlayAsset) => {
    setBusy(true)
    try { await studioClient.deleteOverlayAsset(asset.id); await load() } catch (cause) { setError(errorMessage(cause)) } finally { setBusy(false) }
  }
  return <Modal title="Kho ảnh overlay" onClose={() => { if (!busy) onClose() }} wide>
    <div className="studio-overlay-editor ovs-library">
      <input ref={input} type="file" className="sr-only" tabIndex={-1} accept="image/png,image/jpeg,image/webp" onChange={e => { const file = e.target.files?.[0]; e.target.value = ''; if (file) void upload(file) }} />
      <div className="ovs-library-head">
        <p className="te-muted">PNG, JPEG hoặc WebP, tối đa 5 MB và 4096 px mỗi cạnh. Ảnh lưu trên máy này, dùng lại được ở mọi video.</p>
        <button type="button" className="studio-button primary" disabled={busy} onClick={() => void choose()}><Upload size={15} />{busy ? 'Đang tải…' : 'Tải ảnh lên'}</button>
      </div>
      {loaded && !assets.length ? <EmptyState icon={<ImageIcon size={26} />} title="Chưa có ảnh nào" description="Tải ảnh bảng size, ảnh so sánh… lên để dùng trong overlay." />
        : <div className="ove-assets">{assets.map(a => <div key={a.id} className={'ove-asset' + (a.id === selected ? ' is-selected' : '')}>
          <button type="button" onClick={() => onPick(a)} title={a.original_name}><img src={apiBase + a.url} alt={a.original_name} /></button>
          <small>{a.original_name || a.id.slice(0, 8)} · {a.width}×{a.height}</small>
          <button type="button" className="te-icon-btn is-danger" aria-label={`Xóa ${a.original_name}`} title="Xóa ảnh khỏi kho" disabled={busy} onClick={() => void remove(a)}><Trash2 size={13} /></button>
        </div>)}</div>}
      {error && <ErrorNotice message={error} />}
    </div>
  </Modal>
}
