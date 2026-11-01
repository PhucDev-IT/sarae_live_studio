import { useEffect, useRef, useState, type ChangeEvent, type ReactNode } from 'react'
import { Badge, Button, Card, EmptyState, Input, Modal, Select, Switch, useToast } from '../ui/core'
import {
  ArrowDown, ArrowUp, BookmarkPlus, CircleAlert, ClipboardPaste, Copy, CopyPlus, Crosshair, Eye, EyeOff, Image as ImageIcon, Layers, MoreVertical, Play, Plus, Redo2, Shapes, Tag,
  Trash2, Type, Undo2, Upload, X,
} from 'lucide-react'
import {
  ANIMATIONS, CANVAS, MAX_ELEMENTS, PRODUCT_FIELDS, clampFrame, endOf, normalizeElement, round4, safeImageUrl,
  type AnimationName, type BlockKind, type Frame, type Overlay, type OverlayElement, type OverlayIssue, type OverlayPreset, type TextStyle,
} from './overlayModel'
import { newStep, overlayName, trackName } from './overlayDraft'
import { hms } from '../liveSessionsApi'
import type { LiveProduct } from '../liveSessionsApi'
import { errorText } from '../mediaApi'
import { overlaysApi, type OverlayAsset } from '../overlaysApi'
import { assetUrl } from '../mediaApi'
import { overlayThumb } from '../timeline/thumbs'

const ANIMATION_LABEL:Record<AnimationName, string> = {
  none:'Không', fade:'Mờ dần', slide_up:'Trượt lên', slide_down:'Trượt xuống', slide_left:'Trượt sang trái', slide_right:'Trượt sang phải', zoom:'Phóng to',
}
const BLOCK_LABEL:Record<BlockKind, string> = {text:'Chữ', product_field:'Thông tin sản phẩm', image:'Ảnh', shape:'Hình nền'}
const BLOCK_ICON:Record<BlockKind, typeof Type> = {text:Type, product_field:Tag, image:ImageIcon, shape:Shapes}
const FIELD_LABEL:Record<string, string> = {name:'Tên', price:'Giá', origin_price:'Giá gốc', sku:'SKU'}
const NEW_BLOCK:Record<BlockKind, Partial<OverlayElement>> = {
  text:{kind:'text', text:'Chữ mới', frame:{x:0.05, y:0.1, width:0.9, height:0.8}},
  product_field:{kind:'product_field', field:'name', frame:{x:0.05, y:0.1, width:0.9, height:0.8}},
  image:{kind:'image', source:'asset', asset_id:null, frame:{x:0.05, y:0.05, width:0.9, height:0.9}},
  shape:{kind:'shape', fill:'#000000A0', radius:0.1},
}

type Props = {
  overlays:Overlay[]; selected:number|null; mediaId:string; now:number; editable:boolean; dirty:boolean; busy:boolean
  presets:OverlayPreset[]; products:LiveProduct[]; assets:OverlayAsset[]; issues:OverlayIssue[]; apiBase:string
  canUndo:boolean; canRedo:boolean; canPaste:boolean; blocking:boolean
  onSelect:(index:number|null)=>void; onAdd:(preset:OverlayPreset|null)=>void; onChange:(index:number, next:Overlay, field:string)=>void; onSeal:()=>void
  onRemove:(index:number)=>void; onDuplicate:(index:number)=>void; onCopy:(index:number)=>void; onPaste:()=>void; onUndo:()=>void; onRedo:()=>void
  onPreview:(index:number, part:'in'|'out')=>void; onSeek:(ms:number)=>void; onSave:()=>void; onRevert:()=>void
  onAssetsChanged:(assets:OverlayAsset[])=>void; readOnlyAction?:ReactNode
  /** Reload the templates after one was saved, duplicated or removed (presets include the owner's templates). */
  onTemplatesChanged:()=>void
  /** Turn an overlay on / off (kept in the version, not shown). */
  onToggle:(index:number)=>void
}

const durationText = (ms:number) => {const s = Math.round(ms / 1000);return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`}

/**
 * Overlays of the video shown (list + "new from preset"), and the inspector of the selected one: timing, box, z-index,
 * opacity, animations, its product and its building blocks. Every number is kept as the Backend stores it
 * (fractions of the canvas / of the box); the inputs show canvas pixels and percentages.
 */
export default function OverlayPanel(p:Props){
  const shown = p.overlays.map((o, i)=>({o, i})).filter(x=>x.o.media_id === p.mediaId)
  const current = p.selected != null ? p.overlays[p.selected] : undefined
  const [presetOpen,setPresetOpen] = useState(false)
  const general = p.issues.filter(i=>i.overlay == null && (i.media_id == null || i.media_id === p.mediaId))
  return <div className="ove-panel">
    <Card className="ove-list" padding={false} title={<span className="te-card-title"><Layers size={17}/>Danh sách overlay ({shown.length}{p.overlays.length !== shown.length ? ` / ${p.overlays.length} cả phiên` : ''})</span>}
      description={p.editable ? undefined : 'Phiên bản đã duyệt / đang dùng chỉ xem; muốn sửa hãy tạo bản nháp mới (không đổi phiên bản đang dùng).'}
      actions={p.editable ? <div className="ove-toolbar">
        <button type="button" className="te-icon-btn" title="Hoàn tác (Ctrl+Z)" aria-label="Hoàn tác" disabled={!p.canUndo} onClick={p.onUndo}><Undo2 size={15}/></button>
        <button type="button" className="te-icon-btn" title="Làm lại (Ctrl+Shift+Z / Ctrl+Y)" aria-label="Làm lại" disabled={!p.canRedo} onClick={p.onRedo}><Redo2 size={15}/></button>
        <button type="button" className="te-icon-btn" title="Dán overlay tại vị trí hiện tại (Ctrl+V)" aria-label="Dán" disabled={!p.canPaste} onClick={p.onPaste}><ClipboardPaste size={15}/></button>
        <Button size="sm" leftIcon={<Plus size={14}/>} onClick={()=>setPresetOpen(true)}>Thêm overlay</Button>
      </div> : undefined}>
      {!p.editable && p.readOnlyAction && <div className="ove-readonly">{p.readOnlyAction}</div>}
      {general.length > 0 && <ul className="te-issues">{general.map(i=><li key={i.code + (i.at_ms ?? '')} className={'is-' + i.level}><CircleAlert size={14}/>
        <span>{i.message}</span>{i.at_ms != null && <button type="button" className="ove-link" onClick={()=>p.onSeek(i.at_ms!)}>Tua tới</button>}</li>)}</ul>}
      {!shown.length ? <EmptyState icon={<Layers size={26}/>} title="Video này chưa có overlay" description={p.editable ? 'Tua tới chỗ cần hiện rồi bấm “Thêm overlay”, chọn một mẫu hoặc khung trống.' : 'Phiên bản này không có overlay cho video này.'}/>
        : <ol className="ove-items">{shown.map(({o, i})=>{
          const errors = p.issues.filter(x=>x.overlay === i)
          const on = o.enabled !== false
          const product = o.product_id != null ? p.products.find(x=>x.id === o.product_id) : undefined
          return <li key={i} className={'ove-card' + (i === p.selected ? ' is-selected' : '') + (on ? '' : ' is-off')}>
            <button type="button" className="ove-card-main" onClick={()=>{p.onSelect(i);p.onSeek(o.start_ms)}}>
              <span className="ove-radio" aria-hidden/>
              <CardThumb src={overlayThumb(o, id=>p.products.find(x=>x.id === id), id=>{const a = p.assets.find(x=>x.id === id);return a ? assetUrl(a.url) ?? undefined : undefined})}/>
              <span className="ove-card-text">
                <span className="ove-card-title"><strong>{overlayName(o, p.presets)}</strong><span className={'st-chip' + (on ? ' is-on' : '')}>{on ? 'Đang bật' : 'Tạm ẩn'}</span>
                  {errors.some(x=>x.level === 'error') ? <Badge tone="danger">Lỗi</Badge> : errors.length ? <Badge tone="warning">Chú ý</Badge> : null}</span>
                <small>{hms(o.start_ms)} – {hms(endOf(o))} ({durationText(o.duration_ms)}) · {trackName(o.template, p.presets)}</small>
                {product && <span className="ove-card-product">{product.name}</span>}
              </span>
            </button>
            <ItemMenu editable={p.editable} on={on} onDuplicate={()=>p.onDuplicate(i)} onCopy={()=>p.onCopy(i)} onToggle={()=>p.onToggle(i)} onRemove={()=>p.onRemove(i)}/>
          </li>
        })}</ol>}
      {p.dirty && <div className="te-savebar"><span><CircleAlert size={15}/>Overlay có thay đổi chưa lưu</span>
        <Button size="sm" variant="ghost" disabled={p.busy} onClick={p.onRevert}>Bỏ thay đổi</Button>
        <Button size="sm" loading={p.busy} disabled={p.blocking} title={p.blocking ? 'Sửa các lỗi trước khi lưu' : undefined} onClick={p.onSave}>Lưu nháp</Button></div>}
    </Card>

    {current && p.selected != null ? <Inspector key={p.selected} {...p} index={p.selected} overlay={current}/>
      : <Card className="ove-inspector"><EmptyState icon={<Layers size={26}/>} title="Chọn một overlay" description="Bấm vào overlay trên khung xem trước, trên track hoặc trong danh sách để sửa."/></Card>}

    <Modal open={presetOpen} onClose={()=>setPresetOpen(false)} title={`Thêm overlay tại ${hms(p.now)}`} size="lg">
      <TemplateLibrary presets={p.presets} onPick={preset=>{setPresetOpen(false);p.onAdd(preset)}} onChanged={p.onTemplatesChanged}/>
    </Modal>
  </div>
}

function CardThumb({src}:{src?:string}){
  const [broken,setBroken] = useState(false)
  return <span className="ove-card-thumb">{src && !broken ? <img src={src} alt="" loading="lazy" onError={()=>setBroken(true)}/> : <Layers size={20}/>}</span>
}

/** ⋮ menu of an overlay card. */
function ItemMenu({editable, on, onDuplicate, onCopy, onToggle, onRemove}:{editable:boolean; on:boolean; onDuplicate:()=>void; onCopy:()=>void; onToggle:()=>void; onRemove:()=>void}){
  const [open,setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(()=>{
    if (!open) return
    const close = (e:MouseEvent) => {if (!box.current?.contains(e.target as Node)) setOpen(false)}
    document.addEventListener('mousedown', close)
    return ()=>document.removeEventListener('mousedown', close)
  },[open])
  const item = (label:string, icon:ReactNode, run:()=>void, danger = false) => <button type="button" role="menuitem" className={danger ? 'is-danger' : undefined}
    onClick={()=>{setOpen(false);run()}}>{icon}{label}</button>
  return <div ref={box} className="ove-menu">
    <button type="button" className="te-icon-btn" aria-label="Thao tác" aria-haspopup="menu" aria-expanded={open} onClick={()=>setOpen(v=>!v)}><MoreVertical size={16}/></button>
    {open && <div className="ove-menu-list" role="menu">
      {item('Sao chép', <Copy size={14}/>, onCopy)}
      {editable && item('Nhân bản', <CopyPlus size={14}/>, onDuplicate)}
      {editable && item(on ? 'Tạm ẩn' : 'Bật', on ? <EyeOff size={14}/> : <Eye size={14}/>, onToggle)}
      {editable && item('Xóa', <Trash2 size={14}/>, onRemove, true)}
    </div>}
  </div>
}

// ----- templates -----

/** Built-in presets and the owner's own templates (reusable in every session): a starting point, never a type. */
export function TemplateLibrary({presets, onPick, onChanged}:{presets:OverlayPreset[]; onPick:(preset:OverlayPreset|null)=>void; onChanged:()=>void}){
  const toast = useToast()
  const [busy,setBusy] = useState<number|null>(null)
  const builtIn = presets.filter(x=>!x.owner), mine = presets.filter(x=>x.owner)
  const act = async (id:number, run:()=>Promise<unknown>, done:string) => {
    setBusy(id)
    try {await run();toast.push({tone:'success', title:done});onChanged()}
    catch(e){toast.push({tone:'danger', title:'Không thực hiện được', description:errorText(e, '')})}
    finally{setBusy(null)}
  }
  const card = (preset:OverlayPreset|null) => <button key={preset?.id ?? preset?.template ?? 'blank'} type="button" className="ove-preset" onClick={()=>onPick(preset)}>
    <strong>{preset ? preset.label : 'Khung trống'}</strong>
    <small>{preset ? `${preset.template || 'không nhãn'} · ${preset.elements.length} khối${preset.duration_ms ? ` · ${preset.duration_ms / 1000}s` : ''}` : 'Nền + một dòng chữ'}</small>
    {!!preset?.missing_assets?.length && <small className="ove-preset-warn"><CircleAlert size={12}/>Có ảnh đã bị xóa khỏi kho</small>}
  </button>
  return <>
    <p className="te-muted">Mẫu chỉ là điểm bắt đầu: sau khi thêm, sửa tự do các khối, vị trí, nhãn. Không có danh sách loại cố định.</p>
    <h4 className="ove-preset-group">Mẫu có sẵn</h4>
    <div className="ove-presets">{[null, ...builtIn].map(card)}</div>
    <h4 className="ove-preset-group">Mẫu của tôi <small className="te-muted">dùng lại ở mọi phiên; lưu từ nút <BookmarkPlus size={12}/> của một overlay</small></h4>
    {!mine.length ? <p className="te-muted">Chưa có mẫu riêng.</p> : <div className="ove-presets">{mine.map(t=><div key={t.id} className="ove-preset-wrap">
      {card(t)}
      <div className="ove-preset-actions">
        <button type="button" className="te-icon-btn" title="Nhân bản mẫu" aria-label="Nhân bản mẫu" disabled={busy === t.id}
          onClick={()=>void act(t.id!, ()=>overlaysApi.duplicateTemplate(t.id!), 'Đã nhân bản mẫu')}><CopyPlus size={14}/></button>
        <button type="button" className="te-icon-btn is-danger" title="Xóa mẫu (overlay đã tạo từ mẫu không đổi)" aria-label="Xóa mẫu" disabled={busy === t.id}
          onClick={()=>void act(t.id!, ()=>overlaysApi.removeTemplate(t.id!), 'Đã xóa mẫu')}><Trash2 size={14}/></button>
      </div>
    </div>)}</div>}
  </>
}

/** Save the selected overlay's arrangement as an owner template (no product bound, no times). */
function SaveTemplateModal({overlay, presets, onClose, onSaved}:{overlay:Overlay; presets:OverlayPreset[]; onClose:()=>void; onSaved:()=>void}){
  const toast = useToast()
  const [name,setName] = useState(overlayName(overlay, presets).slice(0, 80))
  const [description,setDescription] = useState('')
  const [busy,setBusy] = useState(false)
  const save = async () => {
    setBusy(true)
    try {
      await overlaysApi.createTemplate({name:name.trim(), description, template:overlay.template, layout:overlay.layout, elements:overlay.elements,
        animation_in:overlay.animation_in, animation_out:overlay.animation_out, duration_ms:overlay.duration_ms})
      toast.push({tone:'success', title:`Đã lưu mẫu “${name.trim()}”`, description:'Dùng lại ở mọi phiên trong “Thêm overlay” → Mẫu của tôi.'})
      onSaved();onClose()
    } catch(e){toast.push({tone:'danger', title:'Không lưu được mẫu', description:errorText(e, '')})}
    finally{setBusy(false)}
  }
  return <Modal open title="Lưu overlay làm mẫu" size="sm" onClose={onClose} footer={<>
    <Button variant="ghost" onClick={onClose}>Hủy</Button>
    <Button loading={busy} disabled={!name.trim()} onClick={()=>void save()}>Lưu mẫu</Button>
  </>}>
    <div className="ove-form">
      <Input label="Tên mẫu" maxLength={80} value={name} onChange={e=>setName(e.target.value)}/>
      <Input label="Ghi chú" placeholder="(không bắt buộc)" maxLength={300} value={description} onChange={e=>setDescription(e.target.value)}/>
      <small className="te-muted">Mẫu giữ các khối, vị trí, hiệu ứng và thời lượng; không giữ sản phẩm (khối sản phẩm đọc sản phẩm của overlay dùng mẫu).</small>
    </div>
  </Modal>
}

// ----- inspector -----

const px = (fraction:number, side:number) => Math.round(fraction * side)
const numberOr = (text:string, fallback:number) => {const v = Number(text.replace(',', '.'));return Number.isFinite(v) ? v : fallback}

function Inspector(p:Props & {index:number; overlay:Overlay}){
  const {overlay:o, index, editable} = p
  const [saving,setSaving] = useState(false)
  const change = (patch:Partial<Overlay>, field:string) => p.onChange(index, {...o, ...patch}, field)
  const layout = (patch:Partial<Frame & {z_index:number; opacity:number}>, field:string) => {
    const merged = {...o.layout, ...patch}
    const c = clampFrame(merged, 0.005)
    change({layout:{...merged, x:round4(c.x), y:round4(c.y), width:round4(c.width), height:round4(c.height)}}, field)
  }
  const setBlock = (n:number, next:OverlayElement, field:string) => change({elements:o.elements.map((e, i)=>i === n ? next : e)}, `block-${n}-${field}`)
  const moveBlock = (n:number, to:number) => {
    if (to < 0 || to >= o.elements.length) return
    const list = [...o.elements];[list[n], list[to]] = [list[to], list[n]]
    change({elements:list}, newStep('blocks'))
  }
  const issues = p.issues.filter(i=>i.overlay === index)
  const productOptions = [{value:'', label:'Không có'}, ...p.products.map(x=>({value:String(x.id), label:x.name + (x.sku ? ' · ' + x.sku : '')}))]
  const labels = [...new Set([...p.presets.map(x=>x.template), ...p.overlays.map(x=>x.template)].filter(Boolean))]
  const on = o.enabled !== false
  const product = o.product_id != null ? p.products.find(x=>x.id === o.product_id) : undefined
  return <Card className="ove-inspector" padding={false}
    title={<span className="te-card-title"><Layers size={17}/>Thuộc tính overlay <span className="ove-insp-name">{overlayName(o, p.presets)}</span></span>}
    description="Thông tin, thời gian hiển thị và nội dung của overlay đang chọn."
    actions={<div className="ove-toolbar">
      <span className={'st-chip' + (on ? ' is-on' : '')}>{on ? 'Đang bật' : 'Tạm ẩn'}</span>
      <button type="button" className="te-icon-btn" title="Xem hiệu ứng vào" aria-label="Xem hiệu ứng vào" onClick={()=>p.onPreview(index, 'in')}><Play size={15}/></button>
      <button type="button" className="te-icon-btn" title="Sao chép (Ctrl+C)" aria-label="Sao chép" onClick={()=>p.onCopy(index)}><Copy size={15}/></button>
      <button type="button" className="te-icon-btn" title="Lưu làm mẫu (dùng lại ở mọi phiên)" aria-label="Lưu làm mẫu" onClick={()=>setSaving(true)}><BookmarkPlus size={15}/></button>
      {editable && <>
        <Button size="sm" variant="outline" leftIcon={<CopyPlus size={14}/>} onClick={()=>p.onDuplicate(index)} title="Nhân bản (Ctrl+D)">Nhân bản</Button>
        <Button size="sm" variant="ghost" className="ove-danger" leftIcon={<Trash2 size={14}/>} onClick={()=>p.onRemove(index)} title="Xóa overlay (Delete)">Xóa</Button>
      </>}
    </div>}>
    <fieldset className="ove-form" disabled={!editable} onBlur={p.onSeal}>
      {issues.length > 0 && <ul className="te-issues ove-issues">{issues.map(i=><li key={i.code + i.message} className={'is-' + i.level}><CircleAlert size={14}/>{i.message}</li>)}</ul>}
      <div className="ove-cols">
      <section className="ove-col">
      <h4 className="ove-col-title">Thông tin cơ bản</h4>
      <div className="ove-row">
        <Switch label={on ? 'Đang bật' : 'Tạm ẩn'} checked={on} onChange={e=>change({enabled:e.target.checked}, newStep('enabled'))}/>
      </div>
      <div className="ove-row ove-2">
        <Input label="Tên hiển thị" placeholder="(không bắt buộc)" maxLength={200} value={o.label} onChange={e=>change({label:e.target.value}, 'label')}/>
        <div>
          <Input label="Nhãn mẫu (track)" placeholder="VD: SIZE_CHART hoặc nhãn riêng" list="ove-labels" maxLength={40} value={o.template}
            onChange={e=>change({template:e.target.value.toUpperCase().replace(/[^A-Z0-9_-]/g, '_')}, 'template')}/>
          <datalist id="ove-labels">{labels.map(l=><option key={l} value={l}>{trackName(l, p.presets)}</option>)}</datalist>
        </div>
      </div>

      <h4 className="ove-col-title">Thời gian hiển thị</h4>
      <div className="ove-row ove-3">
        <Input label="Bắt đầu (giây)" type="number" min={0} step={0.1} value={String(o.start_ms / 1000)} onChange={e=>change({start_ms:Math.max(0, Math.round(numberOr(e.target.value, 0) * 1000))}, 'start')}/>
        <Input label="Thời lượng (giây)" type="number" min={0.5} step={0.1} value={String(o.duration_ms / 1000)} onChange={e=>change({duration_ms:Math.max(0, Math.round(numberOr(e.target.value, 0) * 1000))}, 'duration')}/>
        <div className="ove-field-note"><span className="te-muted">Kết thúc</span><strong>{hms(endOf(o))}</strong>
          {editable && <button type="button" className="ove-link" onClick={()=>change({start_ms:Math.round(p.now / 100) * 100}, newStep('start-now'))}><Crosshair size={13}/>Bắt đầu tại {hms(p.now)}</button>}</div>
      </div>

      <details className="ove-more">
      <summary>Vị trí, lớp, độ trong suốt & hiệu ứng <small className="te-muted">(kéo trên khung xem trước cũng được)</small></summary>
      <h4>Vị trí & kích thước <small className="te-muted">(px trên khung 1080×1920)</small></h4>
      <div className="ove-row ove-4">
        <Input label="X" type="number" min={0} max={CANVAS.width} value={String(px(o.layout.x, CANVAS.width))} onChange={e=>layout({x:numberOr(e.target.value, 0) / CANVAS.width}, 'x')}/>
        <Input label="Y" type="number" min={0} max={CANVAS.height} value={String(px(o.layout.y, CANVAS.height))} onChange={e=>layout({y:numberOr(e.target.value, 0) / CANVAS.height}, 'y')}/>
        <Input label="Rộng" type="number" min={1} max={CANVAS.width} value={String(px(o.layout.width, CANVAS.width))} onChange={e=>layout({width:numberOr(e.target.value, 1) / CANVAS.width}, 'w')}/>
        <Input label="Cao" type="number" min={1} max={CANVAS.height} value={String(px(o.layout.height, CANVAS.height))} onChange={e=>layout({height:numberOr(e.target.value, 1) / CANVAS.height}, 'h')}/>
      </div>
      <div className="ove-row ove-2">
        <Input label="Lớp (z-index 0–100)" type="number" min={0} max={100} step={1} value={String(o.layout.z_index)} onChange={e=>layout({z_index:Math.max(0, Math.min(100, Math.round(numberOr(e.target.value, 0))))}, 'z')}/>
        <Range label="Độ trong suốt" value={o.layout.opacity} onChange={v=>layout({opacity:v}, 'opacity')}/>
      </div>

      <h4>Hiệu ứng</h4>
      <div className="ove-row ove-2">
        <AnimationField label="Vào" value={o.animation_in} onChange={v=>change({animation_in:v}, 'anim-in')} onPreview={()=>p.onPreview(index, 'in')}/>
        <AnimationField label="Ra" value={o.animation_out} onChange={v=>change({animation_out:v}, 'anim-out')} onPreview={()=>p.onPreview(index, 'out')}/>
      </div>
      </details>
      </section>

      <section className="ove-col">
      <h4 className="ove-col-title">Nội dung hiển thị</h4>
      <h4>Sản phẩm của overlay</h4>
      {product ? <div className="ove-product">
        <CardThumb src={product.image_url ? assetUrl(product.image_url) ?? undefined : undefined}/>
        <span className="ove-product-name" title={product.name}>{product.name}</span>
        {editable && <button type="button" className="te-icon-btn" aria-label="Bỏ sản phẩm" title="Bỏ sản phẩm của overlay" onClick={()=>change({product_id:null}, newStep('product'))}><X size={15}/></button>}
      </div> : <Select aria-label="Sản phẩm của overlay" value="" onChange={v=>change({product_id:v ? Number(v) : null}, newStep('product'))} options={productOptions}/>}
      <small className="te-muted">Các khối thông tin / ảnh sản phẩm không chọn sản phẩm riêng sẽ đọc sản phẩm này.</small>

      <h4>Các khối ({o.elements.length}/{MAX_ELEMENTS}) <small className="te-muted">vẽ theo thứ tự, khối sau đè lên khối trước</small></h4>
      <ol className="ove-blocks">{o.elements.map((e, n)=><BlockEditor key={n} element={e} index={n} count={o.elements.length} products={productOptions} assets={p.assets} apiBase={p.apiBase}
        editable={editable} onChange={(next, field)=>setBlock(n, next, field)} onMove={to=>moveBlock(n, to)}
        onRemove={()=>change({elements:o.elements.filter((_, i)=>i !== n)}, newStep('blocks'))} onAssetsChanged={p.onAssetsChanged}/>)}</ol>
      {editable && o.elements.length < MAX_ELEMENTS && <div className="ove-add-blocks">
        {(Object.keys(BLOCK_LABEL) as BlockKind[]).map(kind=>{
          const Icon = BLOCK_ICON[kind]
          return <Button key={kind} size="sm" variant="outline" leftIcon={<Icon size={14}/>} onClick={()=>change({elements:[...o.elements, normalizeElement(NEW_BLOCK[kind])]}, newStep('blocks'))}>{BLOCK_LABEL[kind]}</Button>
        })}
      </div>}
      </section>
      </div>
    </fieldset>
    {saving && <SaveTemplateModal overlay={o} presets={p.presets} onClose={()=>setSaving(false)} onSaved={p.onTemplatesChanged}/>}
  </Card>
}

function Range({label, value, onChange}:{label:string; value:number; onChange:(v:number)=>void}){
  return <label className="ove-range"><span>{label} <b>{Math.round(value * 100)}%</b></span>
    <input type="range" min={0} max={100} step={1} value={Math.round(value * 100)} onChange={e=>onChange(Number(e.target.value) / 100)}/></label>
}

function AnimationField({label, value, onChange, onPreview}:{label:string; value:{name:AnimationName; duration_ms:number}; onChange:(v:{name:AnimationName; duration_ms:number})=>void; onPreview:()=>void}){
  return <div className="ove-anim">
    <Select label={label} value={value.name} onChange={v=>onChange({...value, name:v as AnimationName})} options={ANIMATIONS.map(a=>({value:a, label:ANIMATION_LABEL[a]}))}/>
    <Input label="ms" type="number" min={0} max={5000} step={50} value={String(value.duration_ms)} onChange={e=>onChange({...value, duration_ms:Math.max(0, Math.min(5000, Math.round(numberOr(e.target.value, 0))))})}/>
    <button type="button" className="te-icon-btn" title={`Xem hiệu ứng ${label.toLowerCase()}`} aria-label={`Xem hiệu ứng ${label.toLowerCase()}`} onClick={onPreview}><Play size={14}/></button>
  </div>
}

/** #RRGGBB / #RRGGBBAA with a colour picker for the RGB part (the alpha part is kept). */
function ColorField({label, value, onChange, optional}:{label:string; value:string|null; onChange:(v:string|null)=>void; optional?:boolean}){
  const valid = value == null || /^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$/.test(value)
  return <div className="ove-color">
    <Input label={label} placeholder={optional ? 'không' : '#RRGGBB'} value={value ?? ''} maxLength={9} error={valid ? undefined : 'Sai mã màu'}
      onChange={e=>onChange(e.target.value ? e.target.value : optional ? null : '#000000')}/>
    <input type="color" aria-label={`${label}: chọn màu`} value={(value && valid ? value : '#000000').slice(0, 7)} onChange={e=>onChange(e.target.value.toUpperCase() + (value && valid ? value.slice(7) : ''))}/>
  </div>
}

function BlockEditor({element:e, index, count, products, assets, apiBase, editable, onChange, onMove, onRemove, onAssetsChanged}:{
  element:OverlayElement; index:number; count:number; products:{value:string;label:string}[]; assets:OverlayAsset[]; apiBase:string; editable:boolean
  onChange:(next:OverlayElement, field:string)=>void; onMove:(to:number)=>void; onRemove:()=>void; onAssetsChanged:(assets:OverlayAsset[])=>void
}){
  const [open,setOpen] = useState(false)
  const [picker,setPicker] = useState(false)
  const Icon = BLOCK_ICON[e.kind]
  const frame = (patch:Partial<Frame>, field:string) => {const c = clampFrame({...e.frame, ...patch}, 0.01);onChange({...e, frame:{x:round4(c.x), y:round4(c.y), width:round4(c.width), height:round4(c.height)}}, field)}
  const style = (patch:Partial<TextStyle>, field:string) => 'style' in e && onChange({...e, style:{...e.style, ...patch}} as OverlayElement, field)
  const summary = e.kind === 'text' ? e.text : e.kind === 'product_field' ? FIELD_LABEL[e.field] : e.kind === 'image' ? (e.source === 'product' ? 'Ảnh sản phẩm' : e.asset_id ? 'Ảnh từ kho' : 'Chưa chọn ảnh') : e.shape === 'ellipse' ? 'Elip' : 'Chữ nhật'
  return <li className={'ove-block' + (open ? ' is-open' : '')}>
    <div className="ove-block-head">
      <button type="button" className="ove-block-toggle" onClick={()=>setOpen(v=>!v)} aria-expanded={open}><Icon size={14}/><b>#{index + 1} {BLOCK_LABEL[e.kind]}</b><span>{summary}</span></button>
      {editable && <>
        <button type="button" className="te-icon-btn" aria-label="Lên" title="Vẽ trước (xuống dưới)" disabled={index === 0} onClick={()=>onMove(index - 1)}><ArrowUp size={14}/></button>
        <button type="button" className="te-icon-btn" aria-label="Xuống" title="Vẽ sau (đè lên trên)" disabled={index === count - 1} onClick={()=>onMove(index + 1)}><ArrowDown size={14}/></button>
        <button type="button" className="te-icon-btn is-danger" aria-label="Xóa khối" title="Xóa khối" disabled={count <= 1} onClick={onRemove}><Trash2 size={14}/></button>
      </>}
    </div>
    {open && <div className="ove-block-body">
      {e.kind === 'text' && <Input label="Chữ" maxLength={500} value={e.text} onChange={x=>onChange({...e, text:x.target.value}, 'text')}/>}
      {e.kind === 'product_field' && <div className="ove-row ove-2">
        <Select label="Trường" value={e.field} onChange={v=>onChange({...e, field:v as typeof e.field}, 'field')} options={PRODUCT_FIELDS.map(f=>({value:f, label:FIELD_LABEL[f]}))}/>
        <Select label="Sản phẩm" value={e.product_id != null ? String(e.product_id) : ''} onChange={v=>onChange({...e, product_id:v ? Number(v) : null}, 'product')}
          options={[{value:'', label:'Theo overlay'}, ...products.slice(1)]}/>
        <Input label="Tiền tố" maxLength={60} value={e.prefix} onChange={x=>onChange({...e, prefix:x.target.value}, 'prefix')}/>
        <Input label="Hậu tố" maxLength={60} value={e.suffix} onChange={x=>onChange({...e, suffix:x.target.value}, 'suffix')}/>
      </div>}
      {(e.kind === 'text' || e.kind === 'product_field') && <div className="ove-row ove-3">
        <Input label="Cỡ chữ (px)" type="number" min={8} max={400} value={String(e.style.font_size)} onChange={x=>style({font_size:Math.max(8, Math.min(400, numberOr(x.target.value, 48)))}, 'size')}/>
        <Select label="Độ đậm" value={String(e.style.font_weight)} onChange={v=>style({font_weight:Number(v)}, 'weight')} options={[300, 400, 500, 600, 700, 800, 900].map(w=>({value:String(w), label:String(w)}))}/>
        <Select label="Căn" value={e.style.align} onChange={v=>style({align:v as TextStyle['align']}, 'align')} options={[{value:'left', label:'Trái'}, {value:'center', label:'Giữa'}, {value:'right', label:'Phải'}]}/>
        <ColorField label="Màu chữ" value={e.style.color} onChange={v=>style({color:v ?? '#FFFFFF'}, 'color')}/>
        <ColorField label="Nền chữ" optional value={e.style.background} onChange={v=>style({background:v}, 'bg')}/>
        <Input label="Tối đa dòng (0 = không giới hạn)" type="number" min={0} max={20} value={String(e.style.max_lines)} onChange={x=>style({max_lines:Math.max(0, Math.min(20, Math.round(numberOr(x.target.value, 0))))}, 'lines')}/>
        <Switch label="Đổ bóng chữ" checked={!!e.style.shadow} onChange={x=>style({shadow:x.target.checked}, 'shadow')}/>
      </div>}
      {e.kind === 'image' && <div className="ove-row ove-2">
        <Select label="Nguồn ảnh" value={e.source} onChange={v=>onChange({...e, source:v as 'asset'|'product', ...(v === 'product' ? {asset_id:null} : {product_id:null})}, 'source')}
          options={[{value:'asset', label:'Kho ảnh overlay'}, {value:'product', label:'Ảnh sản phẩm'}]}/>
        {e.source === 'asset'
          ? <div className="ove-asset-pick">
              {e.asset_id && assets.find(a=>a.id === e.asset_id) ? <img src={safeImageUrl(assets.find(a=>a.id === e.asset_id)!.url, apiBase) ?? undefined} alt=""/> : <span className="ove-asset-empty">{e.asset_id ? 'Ảnh đã bị xóa' : 'Chưa chọn'}</span>}
              {editable && <Button size="sm" variant="outline" leftIcon={<ImageIcon size={14}/>} onClick={()=>setPicker(true)}>Chọn ảnh</Button>}
            </div>
          : <Select label="Sản phẩm" value={e.product_id != null ? String(e.product_id) : ''} onChange={v=>onChange({...e, product_id:v ? Number(v) : null}, 'product')} options={[{value:'', label:'Theo overlay'}, ...products.slice(1)]}/>}
        <Select label="Cách lấp khung" value={e.fit} onChange={v=>onChange({...e, fit:v as 'contain'|'cover'}, 'fit')} options={[{value:'contain', label:'Vừa khung (giữ toàn ảnh)'}, {value:'cover', label:'Phủ kín (cắt bớt)'}]}/>
        <Range label="Bo góc" value={e.radius * 2} onChange={v=>onChange({...e, radius:round4(v / 2)}, 'radius')}/>
      </div>}
      {e.kind === 'shape' && <div className="ove-row ove-3">
        <Select label="Hình" value={e.shape} onChange={v=>onChange({...e, shape:v as 'rect'|'ellipse'}, 'shape')} options={[{value:'rect', label:'Chữ nhật'}, {value:'ellipse', label:'Elip'}]}/>
        <ColorField label="Màu nền" optional value={e.fill} onChange={v=>onChange({...e, fill:v}, 'fill')}/>
        <ColorField label="Viền" optional value={e.stroke} onChange={v=>onChange({...e, stroke:v}, 'stroke')}/>
        <Input label="Độ dày viền (px)" type="number" min={0} max={40} value={String(e.stroke_width)} onChange={x=>onChange({...e, stroke_width:Math.max(0, Math.min(40, numberOr(x.target.value, 0)))}, 'stroke-w')}/>
        <Range label="Bo góc" value={e.radius * 2} onChange={v=>onChange({...e, radius:round4(v / 2)}, 'radius')}/>
      </div>}
      <h5>Khung trong overlay <small className="te-muted">(% của khung overlay)</small></h5>
      <div className="ove-row ove-4">
        {(['x', 'y', 'width', 'height'] as const).map(k=><Input key={k} label={{x:'X', y:'Y', width:'Rộng', height:'Cao'}[k]} type="number" min={0} max={100} step={1}
          value={String(Math.round(e.frame[k] * 1000) / 10)} onChange={x=>frame({[k]:numberOr(x.target.value, 0) / 100}, 'frame-' + k)}/>)}
      </div>
      <Range label="Độ trong suốt của khối" value={e.opacity} onChange={v=>onChange({...e, opacity:v}, 'opacity')}/>
    </div>}
    {picker && e.kind === 'image' && <AssetPicker assets={assets} apiBase={apiBase} selected={e.asset_id} onClose={()=>setPicker(false)} onAssetsChanged={onAssetsChanged}
      onPick={id=>{setPicker(false);onChange({...e, source:'asset', asset_id:id, product_id:null}, newStep('asset'))}}/>}
  </li>
}

const ASSET_MAX_BYTES = 5 * 1024 * 1024
const ASSET_TYPES = ['image/png', 'image/jpeg', 'image/webp']

/** The owner's overlay images: pick one, upload a new one (PNG/JPEG/WebP ≤ 5 MB, checked again by the Backend), delete. */
export function AssetPicker({assets, apiBase, selected, onPick, onClose, onAssetsChanged}:{
  assets:OverlayAsset[]; apiBase:string; selected:string|null; onPick:(id:string)=>void; onClose:()=>void; onAssetsChanged:(assets:OverlayAsset[])=>void
}){
  const toast = useToast()
  const input = useRef<HTMLInputElement>(null)
  const [busy,setBusy] = useState(false)
  const upload = async (e:ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    if (!ASSET_TYPES.includes(file.type)) {toast.push({tone:'warning', title:'Chỉ nhận ảnh PNG, JPEG hoặc WebP'});return}
    if (file.size > ASSET_MAX_BYTES) {toast.push({tone:'warning', title:'Ảnh tối đa 5 MB'});return}
    setBusy(true)
    try {const asset = await overlaysApi.upload(file);onAssetsChanged([asset, ...assets]);onPick(asset.id)}
    catch(err){toast.push({tone:'danger', title:'Không tải ảnh lên được', description:errorText(err, '')})}
    finally{setBusy(false)}
  }
  const remove = async (id:string) => {
    setBusy(true)
    try {await overlaysApi.removeAsset(id);onAssetsChanged(assets.filter(a=>a.id !== id))}
    catch(err){toast.push({tone:'danger', title:'Không xóa được ảnh', description:errorText(err, '')})}
    finally{setBusy(false)}
  }
  return <Modal open onClose={onClose} title="Kho ảnh overlay" size="lg"
    footer={<><input ref={input} type="file" accept={ASSET_TYPES.join(',')} hidden onChange={e=>void upload(e)}/>
      <Button leftIcon={<Upload size={15}/>} loading={busy} onClick={()=>input.current?.click()}>Tải ảnh lên</Button></>}>
    <p className="te-muted">PNG, JPEG hoặc WebP, tối đa 5 MB và 4096 px mỗi cạnh. Ảnh chỉ được tải qua đường dẫn ký riêng cho tài khoản của bạn.</p>
    {!assets.length ? <EmptyState icon={<ImageIcon size={26}/>} title="Chưa có ảnh nào" description="Tải ảnh bảng size, ảnh so sánh… lên để dùng trong overlay."/>
      : <div className="ove-assets">{assets.map(a=><div key={a.id} className={'ove-asset' + (a.id === selected ? ' is-selected' : '')}>
        <button type="button" onClick={()=>onPick(a.id)} title={a.filename}><img src={safeImageUrl(a.url, apiBase) ?? undefined} alt={a.filename}/></button>
        <small>{a.filename || a.id.slice(0, 8)} · {a.width}×{a.height}</small>
        <button type="button" className="te-icon-btn is-danger" aria-label="Xóa ảnh" title="Xóa ảnh khỏi kho" disabled={busy} onClick={()=>void remove(a.id)}><Trash2 size={13}/></button>
      </div>)}</div>}
  </Modal>
}
