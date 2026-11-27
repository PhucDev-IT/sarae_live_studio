import { useEffect, useMemo, useRef, useState } from 'react'
import { isTauri } from '@tauri-apps/api/core'
import { open } from '@tauri-apps/plugin-dialog'
import { AlertTriangle, Check, Film, Link2, Pin, Play, Search, Trash2, Upload } from 'lucide-react'
import { studioClient, type Media, type Presenter, type Product } from '../../services/studio'
import { EmptyState, ErrorNotice, Modal, PresenterAvatar, errorMessage, formatBytes } from './ui'
import { aspectLabel, dimensionsLabel, durationLabel } from './videoMetadata'
import { MarkerEditor } from './MarkerEditor'

type Props = {
  products: Product[]; presenters: Presenter[]; media: Media[]; loading: boolean; disabled: boolean; nativeDialog: boolean; visible: boolean
  productFilter: string; onProductFilter: (id: string) => void
  presenterFilter: string; onPresenterFilter: (id: string) => void
  onRefresh: () => Promise<void>; onNotice: (message: string) => void
  onBusyChange: (busy: boolean) => void
}
const extensions = ['mp4', 'm4v', 'mov', 'mkv', 'webm', 'avi', 'wmv', 'mpg', 'mpeg', 'ogv']

export function MediaPanel({ products, presenters, media, loading, disabled, nativeDialog, visible: panelVisible, productFilter, onProductFilter, presenterFilter, onPresenterFilter, onRefresh, onNotice, onBusyChange }: Props) {
  const [linking, setLinking] = useState<Media | null>(null)
  const [markering, setMarkering] = useState<Media | null>(null)
  const [query, setQuery] = useState('')
  const [importProduct, setImportProduct] = useState('')
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<number | null>(null)
  const [importName, setImportName] = useState('')
  const [error, setError] = useState('')
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<Media | null>(null)
  const [metadataErrors, setMetadataErrors] = useState<Record<string, string>>({})
  const [thumbnailErrors, setThumbnailErrors] = useState<Record<string, string>>({})
  const [thumbnailPending, setThumbnailPending] = useState<string | null>(null)
  const [metadataRound, setMetadataRound] = useState(0)
  const inspected = useRef(new Set<string>())
  const thumbnailsAttempted = useRef(new Set<string>())
  const thumbnailRetries = useRef(new Set<string>())
  const inspecting = useRef(false)
  const input = useRef<HTMLInputElement>(null)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => { onBusyChange(busy); return () => onBusyChange(false) }, [busy, onBusyChange])
  useEffect(() => { setImportProduct(productFilter === '__unlinked' ? '' : productFilter) }, [productFilter])
  useEffect(() => { if (importProduct && !products.some(product => product.id === importProduct)) setImportProduct('') }, [products, importProduct])
  const productNames = useMemo(() => new Map(products.map(product => [product.id, product.name])), [products])
  const presenterById = useMemo(() => new Map(presenters.map(presenter => [presenter.id, presenter])), [presenters])
  const visible = useMemo(() => media.filter(item => (!productFilter || (productFilter === '__unlinked' ? !item.product_id : item.product_id === productFilter))
    && (!presenterFilter || (presenterFilter === '__none' ? !item.presenter_ids.length : item.presenter_ids.includes(presenterFilter)))
    && item.original_name.toLocaleLowerCase('vi').includes(query.trim().toLocaleLowerCase('vi'))), [media, productFilter, presenterFilter, query])
  const preview = media.find(item => item.id === previewId)
  const importDisabled = disabled || busy || (isTauri() && !nativeDialog)

  // Generate metadata/covers one file at a time while this page is open.
  // Listing never starts a decoder for every video in a large library.
  useEffect(() => {
    if (!panelVisible || disabled || busy || inspecting.current) return
    const next = visible.find(item => item.status === 'ready' && (
      (item.metadata_probed === false && !inspected.current.has(item.id)) ||
      (thumbnailRetries.current.has(item.id) || (!item.has_thumbnail && !thumbnailsAttempted.current.has(item.id)))
    ))
    if (!next) return
    const metadata = next.metadata_probed === false && !inspected.current.has(next.id)
    if (metadata) inspected.current.add(next.id)
    else { thumbnailRetries.current.delete(next.id); thumbnailsAttempted.current.add(next.id); setThumbnailPending(next.id) }
    inspecting.current = true
    void (metadata ? studioClient.inspectMedia(next.id) : studioClient.createMediaThumbnail(next.id))
      .then(async () => {
        if (!metadata && alive.current) setThumbnailErrors(current => { const errors = { ...current }; delete errors[next.id]; return errors })
        await onRefresh()
      })
      .catch(cause => {
        if (alive.current) (metadata ? setMetadataErrors : setThumbnailErrors)(current => ({ ...current, [next.id]: errorMessage(cause) }))
      })
      .finally(() => {
        inspecting.current = false
        if (alive.current) { setThumbnailPending(null); setMetadataRound(round => round + 1) }
      })
  }, [panelVisible, disabled, busy, visible, metadataRound, onRefresh])

  const retryMetadata = (id: string) => {
    inspected.current.delete(id)
    setMetadataErrors(current => { const next = { ...current }; delete next[id]; return next })
    setMetadataRound(round => round + 1)
  }

  const retryThumbnail = (id: string) => {
    thumbnailRetries.current.add(id)
    thumbnailsAttempted.current.delete(id)
    setThumbnailErrors(current => { const errors = { ...current }; delete errors[id]; return errors })
    setMetadataRound(round => round + 1)
  }

  const importFile = async (source: File | string) => {
    setBusy(true)
    setProgress(null)
    setImportName(typeof source === 'string' ? source.split(/[\\/]/).pop() || 'video' : source.name)
    setError('')
    try {
      await studioClient.importMedia(source, importProduct || undefined, percent => {
        if (alive.current) setProgress(Math.min(100, Math.max(0, percent)))
      })
      if (alive.current) onNotice('Đã nhập video vào thư viện trên máy.')
      await onRefresh()
    } catch (cause) {
      if (alive.current) setError(errorMessage(cause))
      if (cause && typeof cause === 'object' && 'code' in cause && cause.code === 'IMPORT_TIMEOUT') {
        // The core may commit after the transport deadline. Refresh before
        // enabling another import so the existing copy can become visible.
        await onRefresh()
      }
    }
    finally { if (alive.current) { setBusy(false); setProgress(null); setImportName('') } }
  }
  const chooseFile = async () => {
    if (importDisabled) return
    setError('')
    if (!isTauri()) { input.current?.click(); return }
    setBusy(true)
    try {
      const chosen = await open({ multiple: false, directory: false, title: 'Nhập video vào thư viện', filters: [{ name: 'Video', extensions }] })
      if (typeof chosen === 'string') await importFile(chosen)
    } catch (cause) { setError(errorMessage(cause)) }
    finally { if (alive.current) setBusy(false) }
  }
  const remove = async () => {
    if (!deleting || disabled || busy) return
    setBusy(true)
    setError('')
    try {
      await studioClient.deleteMedia(deleting.id)
      setDeleting(null)
      onNotice('Đã xóa video khỏi thư viện.')
      await onRefresh()
    } catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy(false) }
  }
  const importButton = <button className="studio-button primary" disabled={importDisabled} onClick={() => void chooseFile()}><Upload size={17} />{busy && importName ? 'Đang nhập video…' : 'Nhập video'}</button>
  return <>
    <div className="studio-page-heading"><div><h1>Thư viện video</h1><p>Nhập, sắp xếp và xem trước video của bạn.</p></div>{importButton}</div>
    <input ref={input} type="file" className="sr-only" tabIndex={-1} accept={extensions.map(extension => `.${extension}`).join(',')} onChange={event => {
      const file = event.target.files?.[0]
      event.target.value = ''
      if (file && !disabled && !busy) void importFile(file)
    }} />
    <section className="studio-import-strip"><span className="studio-import-icon"><Upload size={22} /></span><div className="studio-grow"><strong>Một bản sao an toàn trong thư viện</strong><p>Video được sao chép vào thư mục dữ liệu local. File gốc của bạn được giữ nguyên.</p></div><label className="studio-field studio-import-target">Gắn video mới với sản phẩm<select value={importProduct} disabled={disabled || busy} onChange={event => setImportProduct(event.target.value)}><option value="">Không gắn sản phẩm</option>{products.map(product => <option key={product.id} value={product.id}>{product.name}</option>)}</select></label></section>
    {isTauri() && !nativeDialog && !disabled && <ErrorNotice message="Dialog chọn video native chưa khả dụng trong bản đang chạy." />}
    {busy && importName && <div className="studio-import-progress" role="status" aria-live="polite"><div><strong>Đang nhập {importName}</strong><span>{progress === null ? 'Đang sao chép và kiểm tra…' : `${progress}%`}</span></div><progress max={100} {...(progress === null ? {} : { value: progress })} aria-label="Tiến trình nhập video" /><small>Giữ ứng dụng mở cho đến khi nhập hoàn tất.</small></div>}
    {error && !deleting && <ErrorNotice message={error} />}
    <section className="studio-card">
      <div className="studio-toolbar"><label className="studio-search"><Search size={17} /><input aria-label="Tìm video" placeholder="Tìm theo tên video…" value={query} onChange={event => setQuery(event.target.value)} /></label><select className="studio-filter" aria-label="Lọc video theo sản phẩm" value={productFilter} onChange={event => onProductFilter(event.target.value)}><option value="">Tất cả sản phẩm</option><option value="__unlinked">Chưa gắn sản phẩm</option>{products.map(product => <option key={product.id} value={product.id}>{product.name}</option>)}</select><select className="studio-filter" aria-label="Lọc video theo người giới thiệu" value={presenterFilter} onChange={event => onPresenterFilter(event.target.value)}><option value="">Mọi người giới thiệu</option><option value="__none">Chưa gắn người giới thiệu</option>{presenters.map(presenter => <option key={presenter.id} value={presenter.id}>{presenter.name}{presenter.active ? '' : ' (tạm nghỉ)'}</option>)}</select><span className="studio-muted">{visible.length} video</span></div>
      {loading ? <div className="studio-empty" role="status">Đang tải thư viện…</div> : !visible.length ? <EmptyState icon={<Film size={30} />} title={query || productFilter || presenterFilter ? 'Chưa có video phù hợp' : 'Thư viện đang chờ video đầu tiên'} description={query ? 'Thử tìm bằng tên file khác.' : 'Chọn video từ máy để thêm vào thư viện. Bạn có thể gắn video với một sản phẩm khi nhập.'} action={importButton} /> : <div className="studio-media-grid">{visible.map(item => <article className="studio-media-card" key={item.id}>
        <VideoCover media={item} disabled={disabled} generating={thumbnailPending === item.id} error={thumbnailErrors[item.id]} onOpen={() => setPreviewId(item.id)} onRetry={() => retryThumbnail(item.id)} onError={() => { thumbnailsAttempted.current.add(item.id); setThumbnailErrors(current => ({ ...current, [item.id]: 'Không tải được ảnh bìa từ thư viện local.' })) }} />
        <div className="studio-media-body"><h3 title={item.original_name}>{item.original_name}</h3><p className="studio-media-specs">{formatBytes(item.size_bytes)} · <span title="Tỷ lệ khung hình">{aspectLabel(item.width, item.height) ?? 'Chưa rõ tỷ lệ'}</span> · <span title="Độ phân giải">{dimensionsLabel(item.width, item.height) ?? 'Chưa rõ kích thước'}</span></p><p className="studio-muted">{new Date(item.created_at).toLocaleDateString('vi-VN')}</p>{metadataErrors[item.id] && <button className="studio-media-metadata-error" title={metadataErrors[item.id]} disabled={disabled || busy} onClick={() => retryMetadata(item.id)}>Chưa đọc được thông số · Thử lại</button>}<p className="studio-media-product" title={item.product_id ? productNames.get(item.product_id) : undefined}>{item.product_id ? productNames.get(item.product_id) || 'Sản phẩm không còn tồn tại' : 'Chưa gắn sản phẩm'}</p><PresenterStack ids={item.presenter_ids} presenters={presenterById} /><div className="studio-media-footer"><span className={`studio-tag ${item.status === 'missing' ? 'warning' : 'success'}`}>{item.status === 'missing' ? 'Thiếu file' : 'Đã lưu local'}</span><button className={`studio-marker-chip${item.marker_count ? ' set' : ''}`} disabled={disabled || item.status === 'missing'} title="Mốc ghim sản phẩm theo thời gian video" onClick={() => { setError(''); setMarkering(item) }}><Pin size={12} />{item.marker_count ? `${item.marker_count} mốc ghim` : 'Đặt mốc ghim'}</button><button className="studio-button ghost icon-only studio-push" disabled={disabled || busy} aria-label={`Gắn sản phẩm và người giới thiệu cho ${item.original_name}`} title="Gắn sản phẩm & người giới thiệu" onClick={() => { setError(''); setLinking(item) }}><Link2 size={16} /></button><button className="studio-button ghost icon-only danger" disabled={disabled || busy} aria-label={`Xóa ${item.original_name}`} title="Xóa video" onClick={() => { setError(''); setDeleting(item) }}><Trash2 size={16} /></button></div></div>
      </article>)}</div>}
    </section>
    {preview && <VideoPreview key={preview.id} media={preview} onClose={() => setPreviewId(null)} />}
    {markering && <MarkerEditor key={markering.id} media={markering} products={products} onClose={() => { setMarkering(null); void onRefresh() }} onSaved={() => {}} />}
    {linking && <LinkMedia key={linking.id} media={linking} products={products} presenters={presenters} disabled={disabled} onClose={() => setLinking(null)} onSaved={async () => { setLinking(null); onNotice('Đã cập nhật liên kết video.'); await onRefresh() }} />}
    {deleting && <Modal title="Xóa video khỏi thư viện?" onClose={() => { if (!busy) setDeleting(null) }}><div className="studio-form"><p>Bản sao được quản lý của <strong>{deleting.original_name}</strong> sẽ bị xóa khỏi thư viện. File gốc trên máy vẫn được giữ nguyên.</p><p className="studio-muted">Thao tác này không thể hoàn tác. Bạn có thể nhập lại từ file gốc.</p>{error && <ErrorNotice message={error} />}<div className="studio-form-actions"><button className="studio-button" disabled={busy} onClick={() => setDeleting(null)}>Giữ lại</button><button className="studio-button danger-solid" disabled={disabled || busy} onClick={() => void remove()}>{busy ? 'Đang xóa…' : 'Xóa video'}</button></div></div></Modal>}
  </>
}

function VideoCover({ media, disabled, generating, error, onOpen, onRetry, onError }: {
  media: Media; disabled: boolean; generating: boolean; error?: string
  onOpen: () => void; onRetry: () => void; onError: () => void
}) {
  return <div className="studio-video-cover">
    <button className={`studio-media-poster${media.status === 'missing' ? ' missing' : ''}`} disabled={disabled || media.status === 'missing'} onClick={onOpen} aria-label={`Xem trước ${media.original_name}`}>
      {media.thumbnail_url && !error ? <img src={media.thumbnail_url} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={onError} /> : <span className="studio-cover-placeholder"><Film size={38} strokeWidth={1.2} /><small>{media.status === 'missing' ? 'Không tìm thấy video' : generating ? 'Đang tạo ảnh bìa…' : error ? 'Chưa tạo được ảnh bìa' : 'Đang chờ ảnh bìa…'}</small></span>}
      <span className="studio-play-badge">{media.status === 'missing' ? <AlertTriangle size={17} /> : <Play size={18} fill="currentColor" />}</span>
      <span className="studio-media-duration" aria-label={`Thời lượng: ${durationLabel(media.duration_seconds) ?? 'Chưa xác định'}`}>{durationLabel(media.duration_seconds) ?? '—:—'}</span>
      <span className="studio-file-extension">{media.original_name.split('.').pop()?.toUpperCase()}</span>
    </button>
    {error && media.status === 'ready' && <button className="studio-cover-retry" title={error} disabled={disabled || generating} onClick={onRetry}>{generating ? 'Đang tạo ảnh bìa…' : 'Tạo lại ảnh bìa'}</button>}
  </div>
}

function PresenterStack({ ids, presenters }: { ids: string[]; presenters: Map<string, Presenter> }) {
  const known = ids.map(id => presenters.get(id)).filter((presenter): presenter is Presenter => Boolean(presenter))
  if (!known.length) return <p className="studio-media-presenters empty">Chưa gắn người giới thiệu</p>
  const names = known.map(presenter => presenter.name).join(', ')
  return <div className="studio-media-presenters" title={names}><span className="studio-avatar-stack">{known.slice(0, 3).map(presenter => <PresenterAvatar key={presenter.id} presenter={presenter} />)}</span><span>{known.length > 3 ? `${known[0].name} và ${known.length - 1} người khác` : names}</span></div>
}

function LinkMedia({ media, products, presenters, disabled, onClose, onSaved }: { media: Media; products: Product[]; presenters: Presenter[]; disabled: boolean; onClose: () => void; onSaved: () => Promise<void> }) {
  const [productId, setProductId] = useState(media.product_id ?? '')
  const [selected, setSelected] = useState(() => new Set(media.presenter_ids))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // Inactive presenters stay visible only when already linked, so a save never silently drops them.
  const choices = presenters.filter(presenter => presenter.active || media.presenter_ids.includes(presenter.id))
  const flip = (id: string) => setSelected(current => {
    const next = new Set(current)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })
  const save = async () => {
    if (busy || disabled) return
    setBusy(true)
    setError('')
    try {
      await studioClient.updateMedia(media.id, { product_id: productId || null, presenter_ids: [...selected] })
      await onSaved()
    } catch (cause) { setError(errorMessage(cause)); setBusy(false) }
  }
  return <Modal title="Gắn sản phẩm & người giới thiệu" onClose={() => { if (!busy) onClose() }}><div className="studio-form">
    <p className="studio-link-file"><Film size={16} /><span title={media.original_name}>{media.original_name}</span></p>
    <label className="studio-field">Sản phẩm<select value={productId} disabled={busy} onChange={event => setProductId(event.target.value)}><option value="">Không gắn sản phẩm</option>{products.map(product => <option key={product.id} value={product.id}>{product.name}</option>)}</select></label>
    <fieldset className="studio-field studio-chip-field"><legend>Người giới thiệu xuất hiện trong video</legend>
      {choices.length ? <div className="studio-chip-list">{choices.map(presenter => {
        const checked = selected.has(presenter.id)
        return <label key={presenter.id} className={`studio-chip${checked ? ' selected' : ''}`}><input type="checkbox" className="sr-only" checked={checked} disabled={busy} onChange={() => flip(presenter.id)} /><PresenterAvatar presenter={presenter} /><span>{presenter.name}{presenter.active ? '' : ' · tạm nghỉ'}</span>{checked && <Check size={14} />}</label>
      })}</div> : <p className="studio-muted">Chưa có người giới thiệu đang hoạt động. Thêm ở mục Người giới thiệu.</p>}
    </fieldset>
    {error && <ErrorNotice message={error} />}
    {disabled && <ErrorNotice message="Studio Core đang mất kết nối. Kết nối lại trước khi lưu." />}
    <div className="studio-form-actions"><button className="studio-button" disabled={busy} onClick={onClose}>Hủy</button><button className="studio-button primary" disabled={busy || disabled} onClick={() => void save()}>{busy ? 'Đang lưu…' : 'Lưu liên kết'}</button></div>
  </div></Modal>
}

function VideoPreview({ media, onClose }: { media: Media; onClose: () => void }) {
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let current = true
    setError('')
    setUrl('')
    void studioClient.mediaUrl(media.id).then(result => { if (current) setUrl(result) }).catch(cause => { if (current) setError(errorMessage(cause)) })
    return () => { current = false }
  }, [media.id, attempt])
  return <Modal title={media.original_name} onClose={onClose} wide><div className="studio-preview">{error ? <><ErrorNotice message={error} /><button className="studio-button" onClick={() => setAttempt(value => value + 1)}>Thử tải lại</button></> : url ? <video src={url} poster={media.thumbnail_url ?? undefined} controls muted playsInline preload="metadata" onError={() => setError('Không phát được video. File có thể đã bị di chuyển hoặc định dạng/codec chưa được trình duyệt hỗ trợ.')} /> : <p role="status">Đang mở video local…</p>}<dl className="studio-video-facts"><div><dt>File gốc</dt><dd>{media.original_name}</dd></div><div><dt>Dung lượng</dt><dd>{formatBytes(media.size_bytes)}</dd></div><div><dt>Thời lượng</dt><dd>{durationLabel(media.duration_seconds) ?? 'Chưa xác định'}</dd></div><div><dt>Kích thước</dt><dd>{dimensionsLabel(media.width, media.height) ?? 'Chưa xác định'}</dd></div><div><dt>Tỷ lệ</dt><dd>{aspectLabel(media.width, media.height) ?? 'Chưa xác định'}</dd></div><div><dt>Nhập vào thư viện</dt><dd>{new Date(media.created_at).toLocaleString('vi-VN')}</dd></div></dl><p className="studio-muted">Xem trước tắt tiếng mặc định. Bạn có thể bật tiếng trong trình phát.</p></div></Modal>
}
