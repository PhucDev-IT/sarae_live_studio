import { useEffect, useState, type ReactNode } from 'react'
import { BrainCircuit, Check, CircleHelp, Database, Film, FolderOpen, HardDrive, Layers3, Monitor, Package, Radio, RefreshCw, ShieldCheck, UsersRound, X } from 'lucide-react'
import { AIPanel } from './AIPanel'
import { LivePanel } from './LivePanel'
import { ProductsPanel } from './ProductsPanel'
import { PresentersPanel } from './PresentersPanel'
import { MediaPanel } from './MediaPanel'
import { useStudio } from './useStudio'
import { formatBytes } from './ui'

const APP_VERSION = '0.3.0'
type Page = 'products' | 'presenters' | 'media' | 'live' | 'ai'
const pageTitles: Record<Page, string> = { products: 'Sản phẩm', presenters: 'Người giới thiệu', media: 'Thư viện video', live: 'Phiên LIVE', ai: 'Trợ lý AI' }
const libraryPages: Page[] = ['products', 'presenters', 'media']

export function StudioShell() {
  const studio = useStudio()
  const [page, setPage] = useState<Page>('products')
  const [productFilter, setProductFilter] = useState('')
  const [presenterFilter, setPresenterFilter] = useState('')
  const [showStorage, setShowStorage] = useState(false)
  const [notice, setNotice] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [mediaBusy, setMediaBusy] = useState(false)
  useEffect(() => { document.title = `Sarae Live Studio · ${APP_VERSION}` }, [])
  useEffect(() => {
    if (!notice) return
    const timer = window.setTimeout(() => setNotice(''), 5000)
    return () => window.clearTimeout(timer)
  }, [notice])
  useEffect(() => {
    if (productFilter && productFilter !== '__unlinked' && !studio.products.some(product => product.id === productFilter)) setProductFilter('')
  }, [studio.products, productFilter])
  useEffect(() => {
    if (presenterFilter && presenterFilter !== '__none' && !studio.presenters.some(presenter => presenter.id === presenterFilter)) setPresenterFilter('')
  }, [studio.presenters, presenterFilter])
  const openMedia = (filters: { product?: string; presenter?: string }) => {
    setProductFilter(filters.product ?? '')
    setPresenterFilter(filters.presenter ?? '')
    setPage('media')
  }
  const navItem = (target: Page, icon: ReactNode, count?: number) => <button className={`studio-nav-item${page === target ? ' active' : ''}`} aria-current={page === target ? 'page' : undefined} onClick={() => setPage(target)}>{icon}<span>{pageTitles[target]}</span>{count !== undefined && <small>{studio.loading ? '–' : count}</small>}</button>
  const reload = async () => { setRefreshing(true); try { await studio.refresh() } finally { setRefreshing(false) } }
  const shared = { products: studio.products, media: studio.media, loading: studio.loading, disabled: !studio.connected, onRefresh: studio.refresh, onNotice: setNotice }
  const missingCount = studio.media.filter(media => media.status === 'missing').length
  return <div className="studio-shell">
    <a className="studio-skip" href="#studio-content">Đến nội dung chính</a>
    <aside className="studio-sidebar">
      <div className="studio-brand"><div className="studio-brand-mark"><Layers3 size={25} strokeWidth={1.7} /></div><div><strong>Sarae</strong><span>LIVE STUDIO</span></div></div>
      <div className="studio-workspace-label"><Monitor size={14} /><span>Không gian trên máy</span><span className="studio-local-dot" /></div>
      <nav aria-label="Điều hướng Studio"><p className="studio-nav-label">THƯ VIỆN CỦA BẠN</p>{navItem('products', <Package size={19} />, studio.products.length)}{navItem('presenters', <UsersRound size={19} />, studio.presenters.length)}{navItem('media', <Film size={19} />, studio.media.length)}
      <p className="studio-nav-label future">KHÔNG GIAN LIVE</p>{navItem('live', <Radio size={19} />)}{navItem('ai', <BrainCircuit size={19} />)}</nav>
      <div className="studio-sidebar-bottom"><div className="studio-local-note"><ShieldCheck size={20} /><strong>Dữ liệu ở trên máy bạn</strong><p>Sản phẩm và video được quản lý trong thư viện local.</p></div><div className="studio-version"><span>Sarae Live Studio</span><span>v{APP_VERSION}</span></div></div>
    </aside>
    <div className="studio-workspace">
      <header className="studio-topbar"><div className="studio-breadcrumb">Studio<span>/</span><strong>{pageTitles[page]}</strong></div><div className="studio-topbar-actions"><span className={`studio-connection ${studio.connected ? 'connected' : studio.loading ? 'connecting' : 'disconnected'}`} role="status"><span />{studio.connected ? 'Core đã kết nối' : studio.loading ? 'Đang kết nối Core…' : 'Core mất kết nối'}</span><button className="studio-button ghost icon-only" title="Thư mục dữ liệu" aria-label="Xem thư mục dữ liệu" aria-expanded={showStorage} onClick={() => setShowStorage(value => !value)}><FolderOpen size={19} /></button><button className="studio-button ghost icon-only" disabled={refreshing || studio.loading || studio.retrying} title="Làm mới dữ liệu" aria-label="Làm mới dữ liệu" onClick={() => void reload()}><RefreshCw size={18} className={refreshing ? 'studio-spin' : ''} /></button></div></header>
      <main id="studio-content" tabIndex={-1} className="studio-content">
        {!studio.connected && !studio.loading && <div className="studio-connection-banner" role="alert"><div><strong>Chưa kết nối được Studio Core</strong><p>{studio.error || 'Kiểm tra bộ máy local rồi thử kết nối lại.'}</p>{studio.status && <small>Danh sách đang hiển thị là dữ liệu từ lần kết nối trước.</small>}</div><button className="studio-button" disabled={studio.retrying} onClick={() => void studio.retry()}><RefreshCw size={16} className={studio.retrying ? 'studio-spin' : ''} />{studio.retrying ? 'Đang kết nối…' : 'Kết nối lại'}</button></div>}
        {showStorage && <section className="studio-storage studio-card"><div className="studio-storage-heading"><Database size={19} /><h2>Nơi lưu dữ liệu</h2><button className="studio-button ghost icon-only" aria-label="Ẩn thông tin lưu trữ" onClick={() => setShowStorage(false)}><X size={17} /></button></div>{studio.status ? <><label>Thư mục dữ liệu<code>{studio.status.data_dir}</code></label><label>Database SQLite<code>{studio.status.database_path}</code></label><p className="studio-muted">Core v{studio.status.version}. Sản phẩm và video đã nhập được lưu tại đây qua các lần mở ứng dụng.</p>{studio.status.version !== APP_VERSION && <p className="studio-version-warning">Phiên bản Core và giao diện khác nhau. Hãy mở lại ứng dụng sau khi cập nhật.</p>}</> : <p className="studio-muted">Kết nối Studio Core để xem vị trí dữ liệu thực tế.</p>}</section>}
        {libraryPages.includes(page) && <div className="studio-overview" aria-label="Tổng quan thư viện"><div className="studio-stat"><span className="studio-stat-icon terracotta"><Package size={21} /></span><div><span>Sản phẩm</span><strong>{studio.loading ? '—' : studio.products.length.toLocaleString('vi-VN')}</strong></div><small>{studio.tiktok?.account ? 'Đồng bộ từ TikTok' : 'Chưa đồng bộ TikTok'}</small></div><div className="studio-stat"><span className="studio-stat-icon plum"><UsersRound size={21} /></span><div><span>Người giới thiệu</span><strong>{studio.loading ? '—' : studio.presenters.filter(presenter => presenter.active).length.toLocaleString('vi-VN')}</strong></div><small>Đang hoạt động</small></div><div className="studio-stat"><span className="studio-stat-icon jade"><Film size={21} /></span><div><span>Video đã nhập</span><strong>{studio.loading ? '—' : studio.media.length.toLocaleString('vi-VN')}</strong></div><small>{missingCount ? `${missingCount} video thiếu file` : 'Trong thư viện của bạn'}</small></div><div className="studio-stat"><span className="studio-stat-icon honey"><HardDrive size={21} /></span><div><span>Dung lượng video</span><strong>{studio.loading ? '—' : formatBytes(studio.media.reduce((total, media) => total + media.size_bytes, 0))}</strong></div><small>Tổng kích thước đã ghi nhận</small></div></div>}
        <div hidden={page !== 'products'}><ProductsPanel {...shared} tiktok={studio.tiktok} onTiktok={studio.setTiktok} disabled={!studio.connected || mediaBusy} onOpenMedia={id => openMedia({ product: id })} /></div>
        <div hidden={page !== 'presenters'}><PresentersPanel presenters={studio.presenters} media={studio.media} loading={studio.loading} disabled={!studio.connected || mediaBusy} onRefresh={studio.refresh} onNotice={setNotice} onOpenMedia={id => openMedia({ presenter: id })} /></div>
        <div hidden={page !== 'media'}><MediaPanel {...shared} visible={page === 'media'} presenters={studio.presenters} onBusyChange={setMediaBusy} nativeDialog={studio.status?.capabilities.native_dialog ?? false} productFilter={productFilter} onProductFilter={setProductFilter} presenterFilter={presenterFilter} onPresenterFilter={setPresenterFilter} /></div>
        <div hidden={page !== 'live'}><LivePanel products={studio.products} media={studio.media} disabled={!studio.connected} visible={page === 'live'} onNotice={setNotice} /></div>
        <div hidden={page !== 'ai'}><AIPanel products={studio.products} disabled={!studio.connected} visible={page === 'ai'} onNotice={setNotice} /></div>
        <footer className="studio-footer"><span><Database size={14} />SQLite · Thư viện trên máy</span><span><CircleHelp size={14} />Studio chỉ điều khiển OBS và chat; bạn tự bật/tắt LIVE trên TikTok.</span></footer>
      </main>
    </div>
    {notice && <div className="studio-toast" role="status"><Check size={18} /><span>{notice}</span><button className="studio-button ghost icon-only" aria-label="Đóng thông báo" onClick={() => setNotice('')}><X size={16} /></button></div>}
  </div>
}
