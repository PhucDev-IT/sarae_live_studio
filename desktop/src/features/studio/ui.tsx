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
      NOT_FOUND: 'Sản phẩm hoặc video này không còn tồn tại. Hãy làm mới danh sách.',
      VALIDATION_ERROR: 'Thông tin chưa hợp lệ. Kiểm tra tên sản phẩm (tối đa 200 ký tự), mô tả và giá bán.',
      CORE_NOT_READY: 'Studio Core đang khởi động. Ứng dụng sẽ tự kết nối khi sẵn sàng.',
      CORE_UNAVAILABLE: 'Chưa kết nối được Studio Core. Hãy thử kết nối lại; với chế độ trình duyệt, chạy npm run studio.',
      CORE_ALREADY_RUNNING: 'Thư mục dữ liệu này đang được một Studio khác sử dụng. Hãy đóng cửa sổ còn lại rồi thử lại.',
      REQUEST_TIMEOUT: 'Studio Core phản hồi quá lâu. Kiểm tra thư viện trước khi thử lại để tránh nhập trùng.',
      IMPORT_TIMEOUT: 'Core có thể vẫn đang nhập video. Đang kiểm tra lại thư viện; hãy đợi kết quả trước khi nhập lại.',
      UPLOAD_INTERRUPTED: 'Video chưa được gửi đầy đủ. Hãy chọn file và nhập lại.',
    }
    if (code === 'STORAGE_ERROR' && 'message' in error && String(error.message).includes('newer version')) return 'Dữ liệu này cần phiên bản Studio mới hơn. Hãy cập nhật ứng dụng.'
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
    return () => { dialog?.close(); previouslyFocused?.focus() }
  }, [])
  return <dialog ref={ref} aria-labelledby={titleId} className={`studio-modal${wide ? ' wide' : ''}`} onCancel={event => { event.preventDefault(); close.current() }}>
    <div className="studio-modal-header"><h2 id={titleId}>{title}</h2><button className="studio-button ghost icon-only" aria-label="Đóng hộp thoại" onClick={onClose}><X size={19} /></button></div>
    {children}
  </dialog>
}
