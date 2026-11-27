import type { LiveStatus } from '../../services/studio'

export function pinPresentation(status: LiveStatus): { label: string; detail: string; tone: string } {
  const pins = status.pins
  const name = (id: string | null) => id ? pins.product_names[id] || 'sản phẩm' : 'sản phẩm'
  if (!pins.real) return { label: 'Chạy thử · không ghim thật', detail: pins.desired_product_id ? `Sẽ ghim ${name(pins.desired_product_id)}` : 'Bật “Ghim thật trên TikTok” để thao tác trên giỏ LIVE.', tone: 'warning' }
  if (!pins.chrome_connected) return { label: 'Chưa kết nối Chrome', detail: 'Vào Sản phẩm → Kết nối TikTok LIVE Console.', tone: 'warning' }
  const live = status.console_live?.state ?? (pins.live === true ? 'LIVE' : pins.live === false ? 'OFFLINE' : 'UNKNOWN')
  if (live !== 'LIVE') return { label: live === 'OFFLINE' ? 'TikTok chưa LIVE · chưa ghim' : 'Chờ xác nhận LIVE để ghim', detail: 'Chỉ ghim khi LIVE Console xác nhận đang phát trực tiếp.', tone: 'warning' }
  if (pins.in_flight) return { label: pins.in_flight.action === 'PIN_PRODUCT' ? 'Đang ghim…' : 'Đang bỏ ghim…', detail: name(pins.in_flight.product_id), tone: 'pending' }
  if (pins.last_error) return { label: 'Ghim chưa được xác nhận', detail: pins.last_error, tone: 'warning' }
  if (!pins.actual_known) return { label: 'Chưa xác nhận trạng thái ghim', detail: 'Đang đọc lại card đang ghim từ TikTok.', tone: 'pending' }
  if (pins.actual_product_id) return { label: 'Đã ghim trên TikTok', detail: name(pins.actual_product_id), tone: 'success' }
  if (pins.actual_tiktok_product_id) return { label: 'Đang ghim sản phẩm ngoài danh mục', detail: `TikTok #${pins.actual_tiktok_product_id}`, tone: 'warning' }
  return { label: 'Chưa có sản phẩm đang ghim', detail: pins.desired_product_id ? `Đang chờ ghim ${name(pins.desired_product_id)}` : 'TikTok hiện chưa ghim sản phẩm nào.', tone: 'neutral' }
}

export function broadcastPresentation(status: LiveStatus) {
  const broadcast = status.broadcast
  const console = status.console_live
  if (console?.state === 'LIVE') return { label: 'TikTok đang LIVE', tone: 'success', source: 'Xác nhận từ LIVE Console', age: console.age_seconds }
  if (broadcast?.state === 'live') return { label: 'TikTok đang LIVE', tone: 'success', source: console?.state === 'OFFLINE' ? 'Kênh công khai đang LIVE · Console chưa xác nhận' : 'Xác nhận từ kênh TikTok công khai', age: broadcast.age_seconds }
  if (console?.state === 'OFFLINE' || broadcast?.state === 'offline') return { label: 'TikTok chưa LIVE', tone: 'neutral', source: console?.state === 'OFFLINE' ? 'LIVE Console chưa phát trực tiếp' : 'Kênh TikTok hiện không phát trực tiếp', age: console?.state === 'OFFLINE' ? console.age_seconds : broadcast?.age_seconds }
  return { label: broadcast?.state === 'checking' ? 'Đang kiểm tra TikTok LIVE…' : 'Chưa xác nhận TikTok LIVE', tone: 'warning', source: broadcast?.error || (broadcast?.username ? `Đang kiểm tra @${broadcast.username}` : 'Kết nối và đồng bộ tài khoản TikTok trước.'), age: broadcast?.age_seconds }
}
