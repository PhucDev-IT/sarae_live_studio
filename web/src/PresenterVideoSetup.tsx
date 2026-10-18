import { UserRound } from 'lucide-react'
import { apiRequest, getPresenterVideos, putPresenterVideos, type Presenter } from './mediaApi'
import VideoAssignment, { type AssignmentConfig } from './VideoAssignment'

/** Which videos each person appears in; a video can feature several people. */
const config:AssignmentConfig = {
  noun:'người',
  listTitle:'Người giới thiệu',
  icon:UserRound,
  ordered:false,
  linkKey:'presenter_ids',
  loadEntities:async()=>(await apiRequest<Presenter[]>('/presenters?limit=500')).map(p=>({id:p.id, name:p.name, subtitle:p.description, image:p.avatar_url})),
  loadVideos:getPresenterVideos,
  saveVideos:async(agentId, id, mediaIds)=>(await putPresenterVideos(agentId, id, mediaIds)).media_ids,
  catalogHash:'/presenters',
  text:{
    summary:(count, duration)=>`Xuất hiện trong ${count} video${duration ? ` · tổng ${duration}` : ''}`,
    emptyList:'Chưa gắn video nào cho người này',
    emptyListHint:'Chọn các video có người này xuất hiện. Một video có thể có nhiều người.',
    shared:'Cùng xuất hiện',
    footnote:'Gỡ video chỉ bỏ người này khỏi video, không xóa file và không ảnh hưởng những người khác trong video.',
    save:'Lưu',
    remove:'Gỡ người khỏi video (không xóa video)',
  },
}

export default function PresenterVideoSetup(){
  return <VideoAssignment config={config}/>
}
