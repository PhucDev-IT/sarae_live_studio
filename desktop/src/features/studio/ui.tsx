import { useEffect, useId, useRef, type ReactNode } from 'react'
import { AlertCircle, X } from 'lucide-react'

export function errorMessage(error: unknown): string {
  if (error && typeof error === 'object' && 'code' in error) {
    const code = String(error.code)
    const messages: Record<string, string> = {
      STORAGE_ERROR: 'Không truy cập được dữ liệu local. Kiểm tra dung lượng ổ đĩa và quyền truy cập thư mục dữ liệu.',
      UNSAFE_PATH: 'Đường dẫn thư viện không hợp lệ hoặc nằm ngoài thư mục dữ liệu. Hãy kiểm tra lại thư viện local.',
      MEDIA_MISSING: 'Không tìm thấy video trong thư viện hoặc file đã thay đổi. Hãy nhập lại từ file gốc.',
      INVALID_MEDIA: 'Không nhập được video. File có thể bị hỏng, đã thay đổi hoặc có định dạng không được hỗ trợ.',
      PROBE_UNAVAILABLE: 'Chưa có FFprobe để kiểm tra video. Hãy cài FFmpeg hoặc dùng bản Studio đã đóng gói đầy đủ.',
      NOT_FOUND: 'Sản phẩm, người giới thiệu hoặc video này không còn tồn tại. Hãy làm mới danh sách.',
      VALIDATION_ERROR: 'Thông tin chưa hợp lệ. Kiểm tra tên (tối đa 200 ký tự), mô tả và giá bán.',
      CORE_NOT_READY: 'Studio Core đang khởi động. Ứng dụng sẽ tự kết nối khi sẵn sàng.',
      CORE_UNAVAILABLE: 'Chưa kết nối được Studio Core. Hãy thử kết nối lại; với chế độ trình duyệt, chạy npm run studio.',
      CORE_ALREADY_RUNNING: 'Thư mục dữ liệu này đang được một Studio khác sử dụng. Hãy đóng cửa sổ còn lại rồi thử lại.',
      REQUEST_TIMEOUT: 'Studio Core phản hồi quá lâu. Kiểm tra thư viện trước khi thử lại để tránh nhập trùng.',
      IMPORT_TIMEOUT: 'Core có thể vẫn đang nhập video. Đang kiểm tra lại thư viện; hãy đợi kết quả trước khi nhập lại.',
      UPLOAD_INTERRUPTED: 'Video chưa được gửi đầy đủ. Hãy chọn file và nhập lại.',
      TIKTOK_LOGIN_REQUIRED: 'Phiên TikTok đã hết hạn. Hãy đăng nhập lại trong Chrome của Studio.',
      TIKTOK_NOT_CONNECTED: 'Chưa kết nối TikTok LIVE Console. Hãy bấm Kết nối trước khi đồng bộ.',
      TIKTOK_TIMEOUT: 'TikTok LIVE Console phản hồi quá lâu. Kiểm tra mạng rồi thử lại.',
      TIKTOK_SNAPSHOT_INVALID: 'Danh sách sản phẩm TikTok không hợp lệ nên chưa có gì thay đổi. Hãy tải lại LIVE Console rồi đồng bộ lại.',
      TIKTOK_ACCOUNT_UNVERIFIED: 'Chưa xác minh được tài khoản TikTok đang đăng nhập. Hãy mở lại LIVE Console rồi thử lại.',
      CORE_TIMEOUT: 'Studio Core phản hồi quá lâu. Hãy làm mới để xem kết quả trước khi thử lại.',
      AI_KEY_MISSING: 'Chưa có API key cho nhà cung cấp đã chọn. Dán key trong Trợ lý AI rồi thử lại.',
      AI_DISABLED: 'AI đang tắt. Bật AI trong trang Trợ lý AI rồi lưu cài đặt.',
    }
    if (code === 'STORAGE_ERROR' && 'message' in error && String(error.message).includes('newer version')) return 'Dữ liệu này cần phiên bản Studio mới hơn. Hãy cập nhật ứng dụng.'
    if (code === 'VALIDATION_ERROR' && 'details' in error && error.details && typeof error.details === 'object' && 'field' in error.details) {
      const field = String(error.details.field)
      const fieldMessages: Record<string, string> = {
        model: 'Model ID chưa hợp lệ. Chỉ dùng chữ, số và . _ - : (ví dụ gemini-3.5-flash-lite).',
        base_url: 'Địa chỉ API chưa hợp lệ. Dịch vụ cloud phải dùng https://; Seller Brain phải chạy trên máy này (127.0.0.1).',
        api_key: 'API key có vẻ chưa đúng. Hãy dán lại key, không kèm khoảng trắng.',
        max_output_tokens: 'Độ dài trả lời tối đa phải từ 60 đến 3000 token.',
        temperature: 'Độ sáng tạo phải từ 0 đến 1,5.',
        sizes: 'Bảng size chưa hợp lệ. Kiểm tra tên size và các khoảng cân nặng/chiều cao.',
        colors: 'Danh sách màu chưa hợp lệ (tối đa 20 màu, mỗi màu tối đa 30 ký tự).',
        extras: 'Tên thông tin thêm chỉ gồm chữ thường không dấu, số và _.',
        comment: 'Bình luận phải có từ 1 đến 1000 ký tự.',
        title: 'Tên kịch bản phải có từ 1 đến 200 ký tự.',
        body: 'Nội dung kịch bản không được trống và tối đa 20.000 ký tự.',
        notes: 'Ghi chú tối đa 1000 ký tự.',
      }
      if (fieldMessages[field]) return fieldMessages[field]
    }
    if (messages[code]) return messages[code]
  }
  if (error instanceof Error) return error.message
  if (error && typeof error === 'object' && 'message' in error) return String(error.message)
  return typeof error === 'string' ? error : 'Có lỗi xảy ra. Vui lòng thử lại.'
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const unit = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), 3)
  return `${new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 1 }).format(bytes / 1024 ** unit)} ${['B', 'KB', 'MB', 'GB'][unit]}`
}

const avatarTones = ['terracotta', 'jade', 'honey', 'plum', 'sky'] as const

export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (!words.length) return '?'
  const letters = words.length === 1 ? [words[0]] : [words[0], words[words.length - 1]]
  return letters.map(word => Array.from(word)[0] ?? '').join('').toLocaleUpperCase('vi')
}

export function PresenterAvatar({ presenter, size = 'sm' }: { presenter: { id: string; name: string; active?: boolean }; size?: 'sm' | 'lg' }) {
  let hash = 0
  for (const char of presenter.id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0
  const tone = avatarTones[hash % avatarTones.length]
  return <span className={`studio-avatar ${tone} ${size}${presenter.active === false ? ' inactive' : ''}`} aria-hidden="true">{initials(presenter.name)}</span>
}

export function ErrorNotice({ message }: { message: string }) {
  return <div className="studio-error" role="alert"><AlertCircle size={18} /><span>{message}</span></div>
}

export function EmptyState({ icon, title, description, action }: { icon: ReactNode; title: string; description: string; action?: ReactNode }) {
  return <div className="studio-empty"><div className="studio-empty-icon">{icon}</div><h3>{title}</h3><p>{description}</p>{action}</div>
}

export function Modal({ title, onClose, children, wide = false }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  const close = useRef(onClose)
  close.current = onClose
  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null
    const dialog = ref.current
    dialog?.showModal()
    // showModal() moves focus after React's autoFocus ran; restore it on the intended field.
    dialog?.querySelector<HTMLElement>('[data-autofocus]')?.focus()
    return () => { dialog?.close(); previouslyFocused?.focus() }
  }, [])
  return <dialog ref={ref} aria-labelledby={titleId} className={`studio-modal${wide ? ' wide' : ''}`} onCancel={event => { event.preventDefault(); close.current() }}>
    <div className="studio-modal-header"><h2 id={titleId}>{title}</h2><button className="studio-button ghost icon-only" aria-label="Đóng hộp thoại" onClick={onClose}><X size={19} /></button></div>
    {children}
  </dialog>
}
