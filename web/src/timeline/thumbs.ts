import { assetUrl } from '../mediaApi'
import type { LiveProduct } from '../liveSessionsApi'
import type { Overlay } from '../overlay/overlayModel'

/** The picture of an overlay in lists and on the timeline: its product's photo (its own product, else the product of a
 * product block), else its first image asset; undefined when it has none. */
export function overlayThumb(o:Overlay, product:(id:number)=>LiveProduct|undefined, assetUrlOf:(id:string)=>string|undefined){
  const own = o.elements.map(e=>e.kind === 'product_field' || e.kind === 'image' ? e.product_id : null).find(id=>id != null)
  const pid = o.product_id ?? own
  const fromProduct = pid != null ? product(pid)?.image_url : undefined
  if (fromProduct) return assetUrl(fromProduct) ?? undefined
  const asset = o.elements.find(e=>e.kind === 'image' && e.source === 'asset' && e.asset_id)
  return asset && asset.kind === 'image' && asset.asset_id ? assetUrlOf(asset.asset_id) : undefined
}
