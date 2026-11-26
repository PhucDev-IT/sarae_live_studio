import { useEffect, useMemo, useRef, useState } from 'react'
import { isTauri } from '@tauri-apps/api/core'
import { open } from '@tauri-apps/plugin-dialog'
import { AlertTriangle, Film, Play, Search, Trash2, Upload } from 'lucide-react'
import { studioClient, type Media, type Product } from '../../services/studio'
import { EmptyState, ErrorNotice, Modal, errorMessage, formatBytes } from './ui'

type Props = {
  products: Product[]; media: Media[]; loading: boolean; disabled: boolean; nativeDialog: boolean
  productFilter: string; onProductFilter: (id: string) => void
  onRefresh: () => Promise<void>; onNotice: (message: string) => void
  onBusyChange: (busy: boolean) => void
}
const extensions = ['mp4', 'm4v', 'mov', 'mkv', 'webm', 'avi', 'wmv', 'mpg', 'mpeg', 'ogv']

export function MediaPanel({ products, media, loading, disabled, nativeDialog, productFilter, onProductFilter, onRefresh, onNotice, onBusyChange }: Props) {
  const [query, setQuery] = useState('')
  const [importProduct, setImportProduct] = useState('')
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<number | null>(null)
  const [importName, setImportName] = useState('')
  const [error, setError] = useState('')
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<Media | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => { onBusyChange(busy); return () => onBusyChange(false) }, [busy, onBusyChange])
  useEffect(() => { setImportProduct(productFilter === '__unlinked' ? '' : productFilter) }, [productFilter])
  useEffect(() => { if (importProduct && !products.some(product => product.id === importProduct)) setImportProduct('') }, [products, importProduct])
  const productNames = useMemo(() => new Map(products.map(product => [product.id, product.name])), [products])
  const visible = useMemo(() => media.filter(item => (!productFilter || (productFilter === '__unlinked' ? !item.product_id : item.product_id === productFilter)) && item.original_name.toLocaleLowerCase('vi').includes(query.trim().toLocaleLowerCase('vi'))), [media, productFilter, query])
  const preview = media.find(item => item.id === previewId)
  const importDisabled = disabled || busy || (isTauri() && !nativeDialog)

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
      <div className="studio-toolbar"><label className="studio-search"><Search size={17} /><input aria-label="Tìm video" placeholder="Tìm theo tên video…" value={query} onChange={event => setQuery(event.target.value)} /></label><select className="studio-filter" aria-label="Lọc video theo sản phẩm" value={productFilter} onChange={event => onProductFilter(event.target.value)}><option value="">Tất cả sản phẩm</option><option value="__unlinked">Chưa gắn sản phẩm</option>{products.map(product => <option key={product.id} value={product.id}>{product.name}</option>)}</select><span className="studio-muted">{visible.length} video</span></div>
      {loading ? <div className="studio-empty" role="status">Đang tải thư viện…</div> : !visible.length ? <EmptyState icon={<Film size={30} />} title={query || productFilter ? 'Chưa có video phù hợp' : 'Thư viện đang chờ video đầu tiên'} description={query ? 'Thử tìm bằng tên file khác.' : 'Chọn video từ máy để thêm vào thư viện. Bạn có thể gắn video với một sản phẩm khi nhập.'} action={importButton} /> : <div className="studio-media-grid">{visible.map(item => <article className="studio-media-card" key={item.id}>
        <button className={`studio-media-poster${item.status === 'missing' ? ' missing' : ''}`} disabled={disabled || item.status === 'missing'} onClick={() => setPreviewId(item.id)} aria-label={`Xem trước ${item.original_name}`}><Film size={38} strokeWidth={1.2} /><span className="studio-play-badge">{item.status === 'missing' ? <AlertTriangle size={17} /> : <Play size={18} fill="currentColor" />}</span><span className="studio-file-extension">{item.original_name.split('.').pop()?.toUpperCase()}</span></button>
        <div className="studio-media-body"><h3 title={item.original_name}>{item.original_name}</h3><p className="studio-muted">{formatBytes(item.size_bytes)} · {new Date(item.created_at).toLocaleDateString('vi-VN')}</p><p className="studio-media-product" title={item.product_id ? productNames.get(item.product_id) : undefined}>{item.product_id ? productNames.get(item.product_id) || 'Sản phẩm không còn tồn tại' : 'Chưa gắn sản phẩm'}</p><div className="studio-media-footer"><span className={`studio-tag ${item.status === 'missing' ? 'warning' : 'success'}`}>{item.status === 'missing' ? 'Thiếu file' : 'Đã lưu local'}</span><button className="studio-button ghost icon-only danger" disabled={disabled || busy} aria-label={`Xóa ${item.original_name}`} title="Xóa video" onClick={() => { setError(''); setDeleting(item) }}><Trash2 size={16} /></button></div></div>
      </article>)}</div>}
    </section>
    {preview && <VideoPreview key={preview.id} media={preview} onClose={() => setPreviewId(null)} />}
    {deleting && <Modal title="Xóa video khỏi thư viện?" onClose={() => { if (!busy) setDeleting(null) }}><div className="studio-form"><p>Bản sao được quản lý của <strong>{deleting.original_name}</strong> sẽ bị xóa khỏi thư viện. File gốc trên máy vẫn được giữ nguyên.</p><p className="studio-muted">Thao tác này không thể hoàn tác. Bạn có thể nhập lại từ file gốc.</p>{error && <ErrorNotice message={error} />}<div className="studio-form-actions"><button className="studio-button" disabled={busy} onClick={() => setDeleting(null)}>Giữ lại</button><button className="studio-button danger-solid" disabled={disabled || busy} onClick={() => void remove()}>{busy ? 'Đang xóa…' : 'Xóa video'}</button></div></div></Modal>}
  </>
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
  return <Modal title={media.original_name} onClose={onClose} wide><div className="studio-preview">{error ? <><ErrorNotice message={error} /><button className="studio-button" onClick={() => setAttempt(value => value + 1)}>Thử tải lại</button></> : url ? <video src={url} controls muted playsInline preload="metadata" onError={() => setError('Không phát được video. File có thể đã bị di chuyển hoặc định dạng/codec chưa được trình duyệt hỗ trợ.')} /> : <p role="status">Đang mở video local…</p>}<p className="studio-muted">{formatBytes(media.size_bytes)} · Xem trước tắt tiếng mặc định. Bạn có thể bật tiếng trong trình phát.</p></div></Modal>
}
