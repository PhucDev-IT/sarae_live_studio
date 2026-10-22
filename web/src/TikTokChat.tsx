import { MessagesSquare } from 'lucide-react'
import { Card, EmptyState } from './ui/core'

/** Old TikTok chat page: comments and AI replies now live inside each LIVE session (SessionLiveChat). */
export default function TikTokChat(){
  return <Card>
    <EmptyState icon={<MessagesSquare size={30}/>} title="Bình luận TikTok giờ nằm trong từng Phiên LIVE"
      description="Không cần nhập username nữa. Khi bạn bắt đầu một phiên LIVE, Sarae tự đọc bình luận của tài khoản TikTok của phiên đó và gợi ý câu trả lời. Mở phiên đang phát để xem bình luận và AI trả lời."
      actionLabel="Mở Phiên LIVE" onAction={()=>{window.location.hash = '#/sessions'}}/>
  </Card>
}
