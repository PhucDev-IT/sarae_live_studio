import { useEffect, useState } from 'react'
import { Button, Input, ThemeProvider, ToastProvider } from './ui/core'
import PresentersPage from './PresentersPage'
import ProductsPage from './ProductsPage'
import MediaPage from './MediaPage'
import LiveWorkspace from './LiveWorkspace'
import LiveControl from './LiveControl'
import LiveSessions from './LiveSessions'
import TikTokChat from './TikTokChat'
import AISettings from './AISettings'
import VerificationMonitor from './VerificationMonitor'
import ProductVideoSetup from './ProductVideoSetup'
import PresenterVideoSetup from './PresenterVideoSetup'
import { Activity, ArrowRight, BarChart3, Bell, Bot, ChevronLeft, ChevronRight, CircleHelp, Clapperboard, Cloud, Contact, Film, HardDrive, LayoutDashboard, ListVideo, Menu, MonitorPlay, Moon, Package, Play, Plus, Radio, Search, Settings2, Sun, Users, Video, Wifi, WifiOff, X } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

type Page = 'verification-monitor' | 'dashboard' | 'media' | 'products' | 'presenters' | 'presenter-videos' | 'product-videos' | 'playlists' | 'sessions' | 'live' | 'tiktok-chat' | 'ai' | 'settings'
type Health = 'checking' | 'online' | 'offline'

const navigation: { title: string; items: { id: Page; title: string; icon: LucideIcon }[] }[] = [
  { title: 'WORKSPACE', items: [{ id: 'dashboard', title: 'Tổng quan', icon: LayoutDashboard }, { id: 'media', title: 'Thư viện video', icon: Film }, { id: 'products', title: 'Sản phẩm', icon: Package }, { id: 'presenters', title: 'Người giới thiệu', icon: Users }, { id: 'presenter-videos', title: 'Video theo người', icon: Contact }] },
  { title: 'BROADCAST', items: [{ id: 'product-videos', title: 'Video theo sản phẩm', icon: ListVideo }, { id: 'playlists', title: 'Playlist', icon: ListVideo }, { id: 'sessions', title: 'Phiên LIVE', icon: Activity }, { id: 'live', title: 'Điều khiển LIVE', icon: Radio }, { id: 'tiktok-chat', title: 'TikTok Chat', icon: Bell }, { id: 'verification-monitor', title: 'CAPTCHA Monitor', icon: Activity }, { id: 'ai', title: 'AI Assistant', icon: Bot }] },
  { title: 'SYSTEM', items: [{ id: 'settings', title: 'Cài đặt', icon: Settings2 }] },
]
const titles: Record<Page, string> = { 'verification-monitor': 'CAPTCHA Monitor', dashboard: 'Tổng quan', media: 'Thư viện video', products: 'Sản phẩm', presenters: 'Người giới thiệu', 'presenter-videos': 'Video theo người', 'product-videos': 'Video theo sản phẩm', playlists: 'Playlist', sessions: 'Phiên LIVE', live: 'Điều khiển LIVE', 'tiktok-chat': 'TikTok Chat', ai: 'AI Assistant', settings: 'Cài đặt' }
const descriptions: Record<Page, string> = {
  'verification-monitor': 'Phát hiện yêu cầu xác minh TikTok LIVE Studio, theo dõi sự kiện và nhận cảnh báo Telegram.',
  dashboard: 'Quản lý nội dung, trạng thái hệ thống và phiên phát từ một nơi.',
  media: 'Tổ chức các short video, phân loại nội dung và liên kết sản phẩm.',
  products: 'Những sản phẩm bán trên LIVE: ảnh, mã, giá, mô tả cho AI và trạng thái sẵn sàng video.',
  presenters: 'Người mẫu / người dẫn xuất hiện trong video: ảnh, mô tả và số video đã gắn.',
  'presenter-videos': 'Xác định những ai xuất hiện trong từng video. Một video có thể có nhiều người.',
  'product-videos': 'Chọn và sắp xếp video chuẩn bị LIVE theo từng sản phẩm.',
  playlists: 'Sắp xếp và điều phối thứ tự phát nội dung.',
  sessions: 'Lên kế hoạch từng buổi LIVE: chọn sản phẩm sẽ bán, kiểm tra sẵn sàng và xem lại lịch sử.',
  live: 'Vận hành phiên LIVE đang chạy: đưa bất kỳ sản phẩm nào lên LIVE, điều khiển video trên OBS.',
  'tiktok-chat': 'Kết nối đến LIVE công khai bằng username và theo dõi bình luận realtime.',
  ai: 'Chuyển nhà cung cấp, cấu hình model và thử phân tích bình luận bằng AI thực tế.',
  settings: 'Thông tin kết nối và cấu hình hệ thống.',
}
const apiBase = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') || 'http://127.0.0.1:8000'

function useHealth() {
  const [health, setHealth] = useState<Health>('checking')
  const [checkedAt, setCheckedAt] = useState<Date | null>(null)
  useEffect(() => {
    let active = true
    const ping = async () => {
      const controller = new AbortController()
      const timeout = window.setTimeout(() => controller.abort(), 4000)
      try {
        const response = await fetch(`${apiBase}/api/health`, { signal: controller.signal })
        const data = await response.json() as { status?: string }
        if (active) setHealth(response.ok && data.status === 'UP' ? 'online' : 'offline')
      } catch {
        if (active) setHealth('offline')
      } finally {
        clearTimeout(timeout)
        if (active) setCheckedAt(new Date())
      }
    }
    void ping()
    const interval = window.setInterval(() => void ping(), 15000)
    return () => { active = false; clearInterval(interval) }
  }, [])
  return { health, checkedAt }
}

function getInitialPage(): Page {
  const key = window.location.hash.replace('#/', '').split('?')[0] as Page
  return key in titles ? key : 'dashboard'
}

function App() {
  const [page, setPage] = useState<Page>(getInitialPage)
  const [collapsed, setCollapsed] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [dark, setDark] = useState(() => localStorage.getItem('sarae-theme') === 'dark')
  const { health, checkedAt } = useHealth()
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
    localStorage.setItem('sarae-theme', dark ? 'dark' : 'light')
  }, [dark])
  useEffect(() => {
    const sync = () => setPage(getInitialPage())
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  }, [])
  const navigate = (next: Page) => {
    setPage(next)
    window.location.hash = `/${next}`
    setDrawerOpen(false)
  }
  const renderNav = () => (
    <>
      <div className="brand">
        <div className="brand-icon"><Clapperboard size={21} strokeWidth={2.2}/></div>
        {!collapsed && <div className="brand-label"><strong>SARAE</strong><span>LIVE STUDIO</span></div>}
        <button className="sidebar-toggle desktop-only" onClick={() => setCollapsed(!collapsed)} aria-label={collapsed ? 'Mở rộng sidebar' : 'Thu gọn sidebar'}>{collapsed ? <ChevronRight size={17}/> : <ChevronLeft size={17}/>}</button>
      </div>
      <div className="nav-scroller">
        {navigation.map(group => <div className="nav-group" key={group.title}>
          {!collapsed && <div className="nav-heading">{group.title}</div>}
          {group.items.map(item => <button key={item.id} className={`nav-item ${page === item.id ? 'active' : ''}`} onClick={() => navigate(item.id)} title={collapsed ? item.title : undefined}>
            <item.icon size={19} strokeWidth={1.85}/>
            {!collapsed && <span>{item.title}</span>}
            {!collapsed && page === item.id && <span className="active-indicator"/>}
          </button>)}
        </div>)}
      </div>
      <div className="sidebar-footer">
        <div className="sidebar-divider"/>
        <button className="nav-item footer-help" onClick={() => navigate('settings')} title="Trạng thái hệ thống"><CircleHelp size={19}/>{!collapsed && <span>Hướng dẫn & kết nối</span>}</button>
        {!collapsed && <div className="sidebar-version">INTERNAL WORKSPACE · V0.1</div>}
      </div>
    </>
  )
  return (
    <ThemeProvider value={{ mode: dark ? 'dark' : 'light', primary: dark ? '#a898ff' : '#6553d7', density: 'comfortable', radius: 'soft', fontSize: 'normal' }}><ToastProvider><div className={`shell ${collapsed ? 'is-collapsed' : ''}`}>
      <aside className="sidebar">{renderNav()}</aside>
      {drawerOpen && <div className="mobile-overlay" onClick={() => setDrawerOpen(false)}><aside className="mobile-sidebar" onClick={e => e.stopPropagation()}><div className="drawer-heading"><strong>Sarae Live Studio</strong><button className="icon-button" onClick={() => setDrawerOpen(false)} aria-label="Đóng menu"><X size={20}/></button></div>{renderNav()}</aside></div>}
      <div className="workspace">
        <header className="topbar">
          <div className="topbar-left">
            <button className="icon-button mobile-menu" onClick={() => setDrawerOpen(true)} aria-label="Mở menu"><Menu size={21}/></button>
            <span className="breadcrumb-root">Workspace</span><ChevronRight size={15} className="breadcrumb-chevron"/><span className="breadcrumb-active">{titles[page]}</span>
          </div>
          <div className="topbar-actions">
            <span className={`connection ${health}`}><span className="status-dot"/><span>{health === 'online' ? 'Backend online' : health === 'checking' ? 'Đang kết nối' : 'Backend offline'}</span></span>
            <span className="topbar-separator"/>
            <button className="icon-button" onClick={() => setDark(!dark)} aria-label={dark ? 'Chế độ sáng' : 'Chế độ tối'}>{dark ? <Sun size={19}/> : <Moon size={19}/>}</button>
            <button className="icon-button notification-button" onClick={() => navigate('settings')} aria-label="Trạng thái"><Bell size={19}/></button>
            <span className="avatar" title="Workspace nội bộ">S</span>
          </div>
        </header>
        <main className="main">
          <div className="page-content">
            <div className="page-heading">
              <div><div className="eyebrow">SARAE WORKSPACE <span className="eyebrow-line"/></div><h1>{titles[page]}</h1><p>{descriptions[page]}</p></div>
              {page === 'dashboard' && <Button className="primary-button" leftIcon={<Plus size={17}/>} onClick={() => navigate('media')}>Quản lý video</Button>}
            </div>
            {page === 'dashboard' ? <Dashboard health={health} navigate={navigate}/> : page === 'settings' ? <Settings health={health} checkedAt={checkedAt}/> : page === 'products' ? <ProductsPage/> : page === 'presenters' ? <PresentersPage/> : page === 'media' ? <MediaPage/> : page === 'product-videos' ? <ProductVideoSetup/> : page === 'presenter-videos' ? <PresenterVideoSetup/> : page === 'sessions' ? <LiveSessions/> : page === 'playlists' ? <LiveWorkspace/> : page === 'live' ? <LiveControl/> : page === 'tiktok-chat' ? <TikTokChat/> : page === 'ai' ? <AISettings/> : page === 'verification-monitor' ? <VerificationMonitor/> : <ModulePage page={page} navigate={navigate}/>}
          </div>
          <footer className="footer"><span>© 2026 Sarae Live Studio · Internal Workspace</span><span>Web Admin Foundation</span></footer>
        </main>
      </div>
    </div></ToastProvider></ThemeProvider>
  )
}

function Dashboard({ health, navigate }: { health: Health; navigate: (page: Page) => void }) {
  const stats = [
    { label: 'VIDEO ĐÃ IMPORT', value: '—', note: 'Chờ API Media Library', icon: Video, tone: 'violet' },
    { label: 'SẢN PHẨM', value: '—', note: 'Chờ module Products', icon: Package, tone: 'peach' },
    { label: 'PLAYLIST', value: '—', note: 'Chờ Playlist Engine', icon: ListVideo, tone: 'blue' },
    { label: 'PHIÊN PHÁT', value: '—', note: 'Chưa kết nối OBS', icon: Activity, tone: 'mint' },
  ]
  return <>
    <section className="hero-panel">
      <div className="hero-copy"><div className="hero-pill"><span className="hero-pill-dot"/> WORKSPACE ĐANG THIẾT LẬP</div><h2>Studio của bạn.<br/><em>Mọi thứ trong tầm kiểm soát.</em></h2><p>Quản lý video, kết nối sản phẩm và chuẩn bị nội dung cho phiên phát — tất cả trong một không gian làm việc.</p><button onClick={() => navigate('media')} className="hero-action">Khám phá thư viện <ArrowRight size={17}/></button></div>
      <div className="hero-art" aria-hidden="true"><div className="art-circle art-circle-one"/><div className="art-circle art-circle-two"/><div className="art-video"><div className="art-video-top"><div className="art-camera"><Video size={17}/></div><span>STUDIO PREVIEW</span><span className="art-red-dot"/></div><div className="art-play"><Play size={28} fill="currentColor"/></div><div className="art-video-bottom"><div className="art-progress"/><div className="art-mini-lines"><span/><span/></div></div></div><div className="art-floating"><span className="art-floating-icon"><Clapperboard size={19}/></span><div><strong>Content Library</strong><small>Ready to organize</small></div><span className="art-check">✓</span></div></div>
    </section>
    <section className="stats-grid">{stats.map(stat => <article className="stat-card" key={stat.label}><div className="stat-top"><div className={`stat-icon ${stat.tone}`}><stat.icon size={20}/></div><span className="stat-spark"><BarChart3 size={17}/></span></div><div className="stat-value">{stat.value}</div><div className="stat-label">{stat.label}</div><div className="stat-note">{stat.note}</div></article>)}</section>
    <div className="dashboard-grid">
      <section className="panel quick-panel"><div className="panel-heading"><div><h3>Bắt đầu làm việc</h3><p>Các khu vực quản lý nội dung</p></div><span className="muted-label">QUICK ACCESS</span></div><div className="quick-grid">
        <button onClick={() => navigate('media')}><span className="quick-icon violet"><Film size={22}/></span><strong>Thư viện video</strong><span>Import, phân loại và quản lý short video.</span><ArrowRight size={17} className="quick-arrow"/></button>
        <button onClick={() => navigate('products')}><span className="quick-icon peach"><Package size={22}/></span><strong>Sản phẩm</strong><span>Liên kết sản phẩm với nội dung quảng bá.</span><ArrowRight size={17} className="quick-arrow"/></button>
        <button onClick={() => navigate('playlists')}><span className="quick-icon blue"><ListVideo size={22}/></span><strong>Playlist</strong><span>Sắp xếp thứ tự phát và điều phối nội dung.</span><ArrowRight size={17} className="quick-arrow"/></button>
      </div></section>
      <section className="panel status-panel"><div className="panel-heading"><div><h3>Trạng thái hệ thống</h3><p>Các dịch vụ trong workspace</p></div><span className="muted-label">SYSTEM</span></div>
        <div className="service-row"><span className="service-icon"><Cloud size={19}/></span><div><strong>Backend API</strong><small>{apiBase}</small></div><span className={`service-badge ${health}`}>{health === 'online' ? 'Online' : health === 'checking' ? 'Checking' : 'Offline'}</span></div>
        <div className="service-row"><span className="service-icon"><HardDrive size={19}/></span><div><strong>Media Storage</strong><small>Chưa cấu hình</small></div><span className="service-badge pending">Pending</span></div>
        <div className="service-row"><span className="service-icon"><MonitorPlay size={19}/></span><div><strong>Desktop Agent</strong><small>Giai đoạn tiếp theo</small></div><span className="service-badge pending">Pending</span></div>
        <button className="text-link" onClick={() => navigate('settings')}>Xem cấu hình hệ thống <ArrowRight size={16}/></button>
      </section>
    </div>
  </>
}

function ModulePage({ page, navigate }: { page: Page; navigate: (page: Page) => void }) {
  const content: Record<Exclude<Page, 'dashboard' | 'settings'>, { icon: LucideIcon; title: string; summary: string; tasks: string[] }> = {
    media: { icon: Film, title: 'Thư viện video sắp ra mắt', summary: 'Tại đây bạn sẽ import MP4 hàng loạt, chọn nơi lưu trữ và gắn sản phẩm cho từng video.', tasks: ['Import video / Link local folder', 'Metadata & ảnh đại diện', 'Phân loại theo sản phẩm, người mẫu'] },
    products: { icon: Package, title: 'Quản lý sản phẩm', summary: 'Dữ liệu sản phẩm sẽ được quản lý tập trung trên Backend và liên kết với video.', tasks: ['Sản phẩm và biến thể', 'Thông tin giới thiệu', 'Liên kết với video'] },
    presenters: { icon: Users, title: 'Người giới thiệu', summary: 'Quản lý người mẫu hoặc người giới thiệu xuất hiện trong từng video.', tasks: ['Hồ sơ người giới thiệu', 'Danh sách video liên quan', 'Thông tin mô tả'] },
    'presenter-videos': { icon: Contact, title: 'Video theo người', summary: 'Gắn những người xuất hiện cho từng video.', tasks: ['Chọn người', 'Chọn video'] },
    'product-videos': { icon: ListVideo, title: 'Video theo sản phẩm', summary: 'Chọn và sắp xếp video phát LIVE theo sản phẩm.', tasks: ['Chọn sản phẩm', 'Chọn video', 'Sắp thứ tự'] },
    sessions: { icon: Activity, title: 'Phiên LIVE', summary: 'Chuẩn bị danh sách và lưu lịch sử phiên.', tasks: ['Tạo phiên', 'Điều khiển sản phẩm', 'Lịch sử'] },
    playlists: { icon: ListVideo, title: 'Playlist Engine', summary: 'Thiết lập thứ tự phát, điều kiện lựa chọn và ưu tiên video.', tasks: ['Playlist tuần tự / ngẫu nhiên', 'Quy tắc tránh lặp', 'Ưu tiên theo sản phẩm'] },
    live: { icon: Radio, title: 'LIVE Control', summary: 'Điều khiển và theo dõi phiên phát khi Desktop Agent và OBS được tích hợp.', tasks: ['Trạng thái phát', 'Video và sản phẩm hiện tại', 'Kết nối Desktop Agent'] },
    'tiktok-chat': { icon: Bell, title: 'TikTok Chat', summary: 'Đọc bình luận LIVE công khai.', tasks: ['Nhập username', 'Kết nối', 'Xem chat realtime'] },
    'verification-monitor': { icon: Activity, title: 'CAPTCHA Monitor', summary: 'Phát hiện yêu cầu xác minh trên TikTok LIVE Studio và cảnh báo qua Telegram.', tasks: ['Chụp màn hình', 'So khớp mẫu', 'Cảnh báo Telegram'] },
    ai: { icon: Bot, title: 'AI Assistant', summary: 'Thiết lập ngữ cảnh sản phẩm, quy tắc phản hồi và giọng nói ở giai đoạn AI.', tasks: ['Quy tắc phản hồi', 'Ngữ cảnh sản phẩm', 'Cấu hình TTS'] },
  }
  const config = content[page as Exclude<Page, 'dashboard' | 'settings'>]
  const Icon = config.icon
  return <section className="module-panel panel"><div className="module-art"><span className="module-icon"><Icon size={31}/></span><div className="module-orbit orbit-a"/><div className="module-orbit orbit-b"/></div><div className="module-content"><div className="module-kicker">MODULE ROADMAP</div><h2>{config.title}</h2><p>{config.summary}</p><div className="module-tags">{config.tasks.map(task => <span key={task}>{task}</span>)}</div><Button variant="outline" className="secondary-button" leftIcon={<ChevronLeft size={17}/>} onClick={() => navigate('dashboard')}>Quay lại tổng quan</Button></div></section>
}

function Settings({ health, checkedAt }: { health: Health; checkedAt: Date | null }) {
  const [term, setTerm] = useState('')
  return <div className="settings-grid"><section className="panel settings-panel"><div className="panel-heading"><div><h3>Kết nối Backend</h3><p>Trạng thái thực tế từ FastAPI</p></div><Wifi size={20}/></div><div className="setting-row"><span>API endpoint</span><code>{apiBase}</code></div><div className="setting-row"><span>Health check</span><span className={`service-badge ${health}`}>{health === 'online' ? 'Online' : health === 'checking' ? 'Checking' : 'Offline'}</span></div><div className="setting-row"><span>Kiểm tra gần nhất</span><strong>{checkedAt ? checkedAt.toLocaleTimeString('vi-VN') : '—'}</strong></div><div className="setting-note">{health === 'offline' ? <WifiOff size={18}/> : <Wifi size={18}/>} <span>{health === 'offline' ? 'Khởi động FastAPI trên máy local để kết nối Web Admin.' : 'Trạng thái được kiểm tra tự động mỗi 15 giây.'}</span></div></section>
    <section className="panel settings-panel"><div className="panel-heading"><div><h3>Thông tin workspace</h3><p>Nền tảng quản lý nội bộ</p></div><Settings2 size={20}/></div><div className="setting-row"><span>Ứng dụng</span><strong>Sarae Live Studio</strong></div><div className="setting-row"><span>Phiên bản UI</span><strong>0.1.0</strong></div><div className="setting-row"><span>Môi trường</span><strong>Local Development</strong></div><div className="core-field"><Input label="Ghi chú" prefix={<Search size={16}/>} value={term} onChange={e => setTerm(e.target.value)} placeholder="Ghi chú cá nhân (chưa lưu)"/></div><p className="settings-footnote">Cấu hình API thay đổi bằng biến môi trường VITE_API_URL. Ghi chú phía trên không được lưu.</p></section></div>
}

export default App
