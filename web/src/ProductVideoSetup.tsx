import { Package } from 'lucide-react'
import { apiRequest, getProductVideos, putProductVideos, type Product } from './mediaApi'
import VideoAssignment, { type AssignmentConfig } from './VideoAssignment'

/** Ordered LIVE playlist per product; a video can be used by many products. */
const config:AssignmentConfig = {
  noun:'sản phẩm',
  listTitle:'Sản phẩm',
  icon:Package,
  ordered:true,
  linkKey:'product_ids',
  loadEntities:async()=>(await apiRequest<Product[]>('/products?limit=500')).map(p=>({id:p.id, name:p.name, subtitle:p.sku, image:p.image_url})),
  loadVideos:getProductVideos,
  saveVideos:async(id, mediaIds)=>(await putProductVideos(id, mediaIds)).media_ids,
  catalogHash:'/products',
  text:{
    summary:(count, duration)=>`${count} video · ${duration ? `${duration} mỗi vòng` : 'chưa rõ thời lượng'} · phát lặp theo thứ tự`,
    emptyList:'Chưa có video — sản phẩm chưa thể bắt đầu LIVE',
    emptyListHint:'Chọn một hoặc nhiều video từ thư viện. Một video có thể dùng cho nhiều sản phẩm.',
    shared:'Cũng dùng cho',
    footnote:'Kéo thả hoặc dùng mũi tên để đổi thứ tự. Gỡ video chỉ bỏ liên kết với sản phẩm này, không xóa file và không ảnh hưởng sản phẩm khác.',
    save:'Lưu thứ tự',
    remove:'Gỡ khỏi sản phẩm (không xóa video)',
  },
}

export default function ProductVideoSetup(){
  return <VideoAssignment config={config}/>
}
