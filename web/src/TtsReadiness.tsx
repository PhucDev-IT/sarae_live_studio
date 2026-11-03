import {Alert,Badge} from './ui/core'
import {voiceCodeText,type TtsReadiness} from './voiceApi'

export function TtsReadinessView({report,active=false}:{report:TtsReadiness|null;active?:boolean}){
 if(!report)return <p className="voice-muted">Chưa có kết quả kiểm tra TTS. Mở trang không tự đồng bộ giọng hoặc warmup GPU.</p>
 if(!report.applicable)return <p className="voice-muted">Giọng hiện tại dùng Edge TTS; không cần GPU G-OmniVoice.</p>
 return <div className="voice-readiness">
  <Badge tone={report.ready?'success':'warning'}>{report.ready?'TTS sẵn sàng tại thời điểm kiểm tra':'TTS chưa sẵn sàng'}</Badge>
  <dl><div><dt>Dịch vụ</dt><dd>{report.service?.online?'Online':'Offline'}</dd></div><div><dt>Model</dt><dd>{({ready:'Sẵn sàng',loading:'Đang tải model',error:'Lỗi model/GPU'} as Record<string,string>)[report.service?.state||'']||'Chưa xác định'}</dd></div>
   <div><dt>Giọng mẫu</dt><dd>{report.voice?`${report.voice.name} · ${report.voice.up_to_date?'đã đồng bộ':'chưa đồng bộ'} · r${report.voice.revision}`:'Chưa chọn giọng hợp lệ'}</dd></div>
   <div><dt>Warmup</dt><dd>{active&&report.warmup?`Đã warmup (${report.warmup.ms} ms)`:'Chưa xác nhận ở lần kiểm tra này'}</dd></div>
   <div><dt>Windows Agent</dt><dd>{report.agent?.supports_omnivoice?'Hỗ trợ OmniVoice':report.agent?.agent_id?'Cần cập nhật Agent':'Chưa chọn máy phát'}</dd></div>
   <div><dt>GPU</dt><dd>{report.gpu?report.gpu.busy?'Đang bận':'Không có job đang chạy':'Chưa có thông tin'}</dd></div></dl>
  {report.warnings.map(w=><Alert key={w} tone="warning">{voiceCodeText(w)}</Alert>)}
  {report.service?.error?.code&&<Alert tone="danger">{voiceCodeText(report.service.error.code)}</Alert>}
  {report.gpu?.note&&<p>{report.gpu.note}</p>}
  {!report.ready&&<p className="voice-warning">Auto voice có thể không phát được. Provider và chế độ tự động vẫn theo cấu hình đã lưu; hãy xử lý cảnh báo hoặc chủ động chọn chính sách phù hợp trước khi bắt đầu.</p>}
  <small>{active?'Kiểm tra chủ động: đã yêu cầu sync/warmup.':'Trạng thái thụ động: không sync hoặc warmup.'} Kết quả có thể thay đổi khi GPU nhận job mới.</small>
 </div>
}
