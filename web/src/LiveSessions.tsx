import { useCallback, useEffect, useMemo, useState, type DragEvent, type FormEvent } from 'react'
import { Alert, Badge, Button, Card, ConfirmModal, EmptyState, Input, Modal, SearchInput, SegmentedControl, useToast } from './ui/core'
import {
  CalendarClock, Check, CheckCircle2, CircleAlert, CircleDashed, Clock3, Copy, GripVertical, History, ListPlus,
  Package, Pencil, Play, Plus, Radio, RotateCcw, Square, Trash2, Video,
} from 'lucide-react'
import { apiRequest, errorText } from './mediaApi'
import {
  EVENT_LABEL, STATUS_META, dateTime, durationText, eventDetail, sessionIdFromHash, sessionsApi, timeOnly,
  type LiveProduct, type LiveSession, type SessionEvent, type SessionStatus,
} from './liveSessionsApi'
import './live-sessions.css'

type LiveStatus = { connected:boolean; running:boolean; session_id?:number|null; product_id?:number|null; pending_product_id?:number|null }
type Filter = 'ALL' | SessionStatus
type Confirm = null | {kind:'finish'|'delete'|'close-stale'|'discard'; session:LiveSession; next?:number|null}

const POLL_MS = 3000
const STEPS:{status:SessionStatus;label:string;hint:string}[] = [
  {status:'DRAFT', label:'Chuẩn bị', hint:'Chọn & sắp xếp sản phẩm'},
  {status:'RUNNING', label:'Đang LIVE', hint:'Điều khiển ở màn LIVE'},
  {status:'ENDED', label:'Kết thúc', hint:'Xem lại lịch sử'},
]

/** Plans LIVE sessions: build the product lineup, check readiness, start, and review history. Live switching happens in #/live. */
export default function LiveSessions(){
  const toast = useToast()
  const [sessions,setSessions] = useState<LiveSession[]>([])
  const [products,setProducts] = useState<LiveProduct[]>([])
  const [status,setStatus] = useState<LiveStatus|null>(null)
  const [events,setEvents] = useState<SessionEvent[]>([])
  const [loaded,setLoaded] = useState(false)
  const [selected,setSelected] = useState<number|null>(sessionIdFromHash)
  /** Unsaved lineup of the selected session; null when it matches the server. */
  const [draft,setDraft] = useState<number[]|null>(null)
  const [filter,setFilter] = useState<Filter>('ALL')
  const [query,setQuery] = useState('')
  const [busy,setBusy] = useState(false)
  const [createOpen,setCreateOpen] = useState(false)
  const [renameOpen,setRenameOpen] = useState(false)
  const [nameInput,setNameInput] = useState('')
  const [pickerOpen,setPickerOpen] = useState(false)
  const [confirm,setConfirm] = useState<Confirm>(null)
  const [startOpen,setStartOpen] = useState(false)
  /** Product chosen to open the LIVE with; any product of the session that has videos. */
  const [opener,setOpener] = useState<number|null>(null)
  const [dragIndex,setDragIndex] = useState<number|null>(null)

  const loadSessions = useCallback(async ()=>{setSessions(await sessionsApi.list())},[])
  const loadAll = useCallback(async ()=>{
    const [s, p, st] = await Promise.all([sessionsApi.list(), sessionsApi.products(), apiRequest<LiveStatus>('/live/status')])
    setSessions(s);setProducts(p);setStatus(st);setLoaded(true)
  },[])
  useEffect(()=>{void loadAll().catch(e=>{setLoaded(true);toast.push({tone:'danger',title:'Không tải được phiên LIVE',description:errorText(e, '')})})},[loadAll,toast])
  useEffect(()=>{
    const t = window.setInterval(()=>void apiRequest<LiveStatus>('/live/status').then(setStatus).catch(()=>{}), POLL_MS)
    return ()=>window.clearInterval(t)
  },[])
  // The running session's current product changes from the LIVE console; refresh when playback moves.
  useEffect(()=>{if (loaded) void loadSessions().catch(()=>{})},[loaded,loadSessions,status?.running,status?.session_id,status?.product_id,status?.pending_product_id])
  // Default selection: the running session, else the newest one.
  useEffect(()=>{
    if (selected != null && sessions.some(s=>s.id === selected)) return
    if (sessions.length) setSelected((sessions.find(s=>s.status === 'RUNNING') ?? sessions[0]).id)
  },[sessions,selected])

  const chosen = sessions.find(s=>s.id === selected)
  const chosenKey = chosen ? `${chosen.id}:${chosen.status}:${chosen.current_product_id}:${chosen.product_ids.join(',')}` : ''
  useEffect(()=>{
    if (!chosen) {setEvents([]);return}
    void sessionsApi.events(chosen.id).then(setEvents).catch(()=>setEvents([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[chosenKey])

  const productById = useMemo(()=>new Map(products.map(p=>[p.id,p])),[products])
  const lineup = draft ?? chosen?.product_ids ?? []
  const dirty = draft != null
  const editable = !!chosen && chosen.status !== 'ENDED'
  const owner = chosen?.status === 'RUNNING' && !!status?.running && status?.session_id === chosen.id
  const stale = sessions.filter(s=>s.status === 'RUNNING' && !(status?.running && status.session_id === s.id))
  const running = sessions.find(s=>s.status === 'RUNNING' && status?.running && status.session_id === s.id)
  const counts = {ALL:sessions.length, DRAFT:0, RUNNING:0, ENDED:0} as Record<Filter,number>
  sessions.forEach(s=>{counts[s.status]++})
  const visible = sessions.filter(s=>(filter === 'ALL' || s.status === filter) && s.name.toLocaleLowerCase('vi').includes(query.trim().toLocaleLowerCase('vi')))

  const canResume = !!status?.connected && !status?.running
  const resumeHint = status?.running ? 'OBS đang phát phiên khác' : !status?.connected ? 'Cần kết nối OBS trước (màn Điều khiển LIVE)' : undefined
  const resume = (session:LiveSession) => void run(async ()=>{await sessionsApi.resume(session.id);window.location.hash = '/live'}, `Đã tiếp tục “${session.name}”`)
  const select = (id:number) => {
    if (id === selected) return
    if (dirty && chosen) {setConfirm({kind:'discard', session:chosen, next:id});return}
    setSelected(id);setDraft(null)
  }
  const run = async (action:()=>Promise<unknown>, success:string) => {
    setBusy(true)
    try{await action();toast.push({tone:'success',title:success});await loadAll()}
    catch(e){toast.push({tone:'danger',title:'Không thực hiện được',description:errorText(e, '')})}
    finally{setBusy(false)}
  }
  const edit = (next:number[]) => setDraft(chosen && next.join(',') === chosen.product_ids.join(',') ? null : next)
  const move = (from:number, to:number) => {
    if (to < 0 || to >= lineup.length || from === to) return
    const next = [...lineup];const [item] = next.splice(from, 1);next.splice(to, 0, item);edit(next)
  }
  const onDrop = (event:DragEvent, to:number) => {event.preventDefault();if (dragIndex != null) move(dragIndex, to);setDragIndex(null)}

  const create = (event:FormEvent) => {
    event.preventDefault()
    const name = nameInput.trim()
    if (!name) return
    void run(async ()=>{
      const created = await sessionsApi.create(name)
      setCreateOpen(false);setSelected(created.id);setDraft(null);setFilter('ALL');setPickerOpen(true)
    }, 'Đã tạo phiên — thêm sản phẩm để chuẩn bị')
  }
  const rename = (event:FormEvent) => {
    event.preventDefault()
    if (!chosen || !nameInput.trim()) return
    void run(async ()=>{await sessionsApi.rename(chosen.id, nameInput.trim());setRenameOpen(false)}, 'Đã đổi tên phiên')
  }
  const save = () => chosen && draft && void run(async ()=>{await sessionsApi.saveProducts(chosen.id, draft);setDraft(null)}, 'Đã lưu danh sách sản phẩm')
  const confirmAction = () => {
    if (!confirm) return
    const {kind, session} = confirm
    setConfirm(null)
    if (kind === 'discard') {setSelected(confirm.next ?? null);setDraft(null);return}
    if (kind === 'finish' || kind === 'close-stale') void run(()=>sessionsApi.finish(session.id), kind === 'finish' ? 'Đã kết thúc phiên LIVE' : 'Đã đóng phiên bị treo')
    if (kind === 'delete') void run(async ()=>{await sessionsApi.remove(session.id);setSelected(null);setDraft(null)}, 'Đã xóa phiên')
  }

  // Readiness: what must be true before "Bắt đầu LIVE" (blocking) and what is only advised.
  const missingVideo = lineup.filter(id=>!productById.get(id)?.video_count)
  const readyIds = lineup.filter(id=>productById.get(id)?.video_count)
  const startId = opener != null && readyIds.includes(opener) ? opener : readyIds[0]
  const checks = [
    {ok:lineup.length > 0, label:'Có ít nhất 1 sản phẩm trong danh sách', blocking:true},
    {ok:readyIds.length > 0, label:'Có ít nhất 1 sản phẩm đã có video để phát', blocking:true, href:'#/product-videos'},
    {ok:!dirty, label:'Đã lưu thay đổi danh sách', blocking:true},
    {ok:!!status?.connected, label:'OBS đã kết nối', blocking:true, href:'#/live'},
    {ok:!status?.running, label:'Không có phiên nào khác đang phát', blocking:true},
    {ok:missingVideo.length === 0, label:missingVideo.length ? `${missingVideo.length} sản phẩm chưa có video (chưa đưa lên LIVE được)` : 'Mọi sản phẩm đều đã có video', blocking:false, href:'#/product-videos'},
  ]
  const canStart = chosen?.status === 'DRAFT' && checks.every(c=>c.ok || !c.blocking)

  const switches = events.filter(e=>e.event_type === 'PRODUCT_SWITCHED').length

  return <div className="ls-page">
    {running && <div className="ls-live-banner" role="status">
      <span className="ls-live-dot"><Radio size={16}/>ĐANG LIVE</span>
      <div className="ls-live-text">
        <strong>{running.name}</strong>
        <small>Đang phát: {productById.get(status?.product_id ?? -1)?.name ?? '—'} · {durationText(running.started_at, null)}</small>
      </div>
      <Button size="sm" variant="outline" onClick={()=>select(running.id)}>Xem phiên</Button>
      <Button size="sm" leftIcon={<Radio size={15}/>} onClick={()=>{window.location.hash = '/live'}}>Mở Điều khiển LIVE</Button>
    </div>}
    {stale.length > 0 && <Alert tone="warning" title={`${stale.length} phiên LIVE bị ngắt`}>
      <span>Backend đã khởi động lại nên OBS không còn phát. Tiếp tục để phát lại sản phẩm đang LIVE (giữ nguyên lịch sử), hoặc đóng nếu buổi LIVE đã xong.</span>
      {stale.map(s=><div key={s.id} className="ls-inline">
        <strong>{s.name}</strong>
        <Button size="sm" leftIcon={<Play size={14}/>} disabled={busy || !canResume} title={resumeHint} onClick={()=>resume(s)}>Tiếp tục phiên</Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={()=>setConfirm({kind:'close-stale', session:s})}>Đóng phiên</Button>
      </div>)}
    </Alert>}

    <div className="ls-layout">
      {/* Session list */}
      <Card className="ls-list" padding={false} title={<span className="ls-card-title"><CalendarClock size={17}/>Danh sách phiên</span>}
        actions={<Button size="sm" leftIcon={<Plus size={15}/>} onClick={()=>{setNameInput('');setCreateOpen(true)}}>Tạo phiên</Button>}>
        <div className="ls-list-tools">
          <SegmentedControl<Filter> fullWidth value={filter} onChange={setFilter} items={[
            {value:'ALL', label:<>Tất cả<span className="ls-count">{counts.ALL}</span></>}, {value:'DRAFT', label:<>Chuẩn bị<span className="ls-count">{counts.DRAFT}</span></>},
            {value:'RUNNING', label:<>LIVE<span className="ls-count">{counts.RUNNING}</span></>}, {value:'ENDED', label:<>Xong<span className="ls-count">{counts.ENDED}</span></>},
          ]}/>
          <SearchInput value={query} onChange={e=>setQuery(e.target.value)} placeholder="Tìm theo tên phiên…" aria-label="Tìm phiên"/>
        </div>
        <ul className="ls-sessions">
          {visible.map(s=>{
            const ready = s.product_ids.filter(id=>productById.get(id)?.video_count).length
            return <li key={s.id}><button type="button" className={'ls-session' + (s.id === selected ? ' is-selected' : '') + (s.status === 'RUNNING' ? ' is-live' : '')} onClick={()=>select(s.id)}>
              <span className="ls-session-top"><strong>{s.name}</strong><Badge tone={STATUS_META[s.status].tone} dot={s.status === 'RUNNING'}>{STATUS_META[s.status].label}</Badge></span>
              <span className="ls-session-meta">
                <span><Package size={13}/>{s.product_ids.length} sản phẩm</span>
                {s.status === 'DRAFT' && s.product_ids.length > 0 && <span className={ready === s.product_ids.length ? 'is-ok' : 'is-warn'}><Video size={13}/>{ready}/{s.product_ids.length} có video</span>}
                <span><Clock3 size={13}/>{dateTime(s.status === 'DRAFT' ? s.created_at : s.started_at ?? s.created_at)}</span>
              </span>
            </button></li>
          })}
          {loaded && !visible.length && <li className="ls-list-empty">{sessions.length ? 'Không có phiên khớp bộ lọc.' : 'Chưa có phiên LIVE nào.'}</li>}
        </ul>
      </Card>

      {/* Session detail */}
      {!chosen ? <Card className="ls-detail-empty">
        <EmptyState icon={<CalendarClock size={30}/>} title={loaded ? 'Chưa chọn phiên LIVE' : 'Đang tải…'}
          description="Một phiên LIVE là danh sách sản phẩm sẽ bán, theo đúng thứ tự giới thiệu. Tạo phiên, thêm sản phẩm rồi bắt đầu phát."
          actionLabel={loaded ? 'Tạo phiên LIVE' : undefined} onAction={()=>{setNameInput('');setCreateOpen(true)}}/>
      </Card> : <div className="ls-detail">
        <Card className="ls-head" padding={false}>
          <div className="ls-head-main">
            <div className="ls-head-title">
              <span className="ls-head-id">PHIÊN #{chosen.id}</span>
              <h2>{chosen.name}
                {chosen.status !== 'ENDED' && <button type="button" className="ls-icon-btn" title="Đổi tên" aria-label="Đổi tên phiên" onClick={()=>{setNameInput(chosen.name);setRenameOpen(true)}}><Pencil size={15}/></button>}
              </h2>
              <div className="ls-head-meta">
                <Badge tone={STATUS_META[chosen.status].tone} dot={chosen.status === 'RUNNING'}>{STATUS_META[chosen.status].label}</Badge>
                <span>Tạo {dateTime(chosen.created_at)}</span>
                {chosen.started_at && <span>Bắt đầu {dateTime(chosen.started_at)}</span>}
                {chosen.ended_at && <span>Kết thúc {dateTime(chosen.ended_at)}</span>}
              </div>
            </div>
            <div className="ls-head-actions">
              {chosen.status === 'DRAFT' && <>
                <Button variant="ghost" leftIcon={<Trash2 size={15}/>} disabled={busy} onClick={()=>setConfirm({kind:'delete', session:chosen})}>Xóa</Button>
                <Button variant="outline" leftIcon={<Copy size={15}/>} disabled={busy} onClick={()=>void run(async ()=>{const c = await sessionsApi.duplicate(chosen.id);setSelected(c.id);setDraft(null)}, 'Đã nhân bản phiên')}>Nhân bản</Button>
                <Button leftIcon={<Play size={16}/>} disabled={busy || !canStart} title={canStart ? undefined : 'Hoàn tất các mục kiểm tra bên dưới'} onClick={()=>{setOpener(null);setStartOpen(true)}}>Bắt đầu LIVE</Button>
              </>}
              {chosen.status === 'RUNNING' && (owner ? <>
                <Button variant="danger" leftIcon={<Square size={15}/>} disabled={busy} onClick={()=>setConfirm({kind:'finish', session:chosen})}>Kết thúc phiên</Button>
                <Button leftIcon={<Radio size={16}/>} onClick={()=>{window.location.hash = '/live'}}>Mở Điều khiển LIVE</Button>
              </> : <>
                <Button variant="outline" disabled={busy} onClick={()=>setConfirm({kind:'close-stale', session:chosen})}>Đóng phiên</Button>
                <Button leftIcon={<Play size={16}/>} disabled={busy || !canResume} title={resumeHint} onClick={()=>resume(chosen)}>Tiếp tục phiên</Button>
              </>)}
              {chosen.status === 'ENDED' && <>
                <Button variant="ghost" leftIcon={<Trash2 size={15}/>} disabled={busy} onClick={()=>setConfirm({kind:'delete', session:chosen})}>Xóa</Button>
                <Button leftIcon={<RotateCcw size={15}/>} disabled={busy} onClick={()=>void run(async ()=>{const c = await sessionsApi.duplicate(chosen.id);setSelected(c.id);setDraft(null)}, 'Đã tạo phiên mới từ danh sách cũ')}>LIVE lại danh sách này</Button>
              </>}
            </div>
          </div>
          <ol className="ls-steps" aria-label="Tiến trình phiên">
            {STEPS.map((step,i)=>{
              const at = STEPS.findIndex(s=>s.status === chosen.status)
              const state = i < at || chosen.status === 'ENDED' ? 'done' : i === at ? 'current' : 'todo'
              return <li key={step.status} className={'is-' + state}>
                <span className="ls-step-dot">{state === 'done' ? <Check size={14}/> : i + 1}</span>
                <span><strong>{step.label}</strong><small>{step.hint}</small></span>
              </li>
            })}
          </ol>
        </Card>

        <div className="ls-detail-grid">
          {/* Product lineup */}
          <Card className="ls-lineup" padding={false} title={<span className="ls-card-title"><Package size={17}/>Danh sách sản phẩm</span>}
            description={chosen.status === 'ENDED' ? `${lineup.length} sản phẩm đã dùng trong phiên` : 'Sản phẩm chuẩn bị sẵn cho phiên · khi LIVE có thể đưa bất kỳ sản phẩm nào lên, không theo thứ tự'}
            actions={editable ? <Button size="sm" variant="outline" leftIcon={<ListPlus size={15}/>} disabled={busy} onClick={()=>setPickerOpen(true)}>Thêm sản phẩm</Button> : undefined}>
            {!lineup.length
              ? <div className="ls-lineup-empty"><EmptyState icon={<Package size={28}/>} title="Chưa có sản phẩm" description="Thêm những sản phẩm dự định bán trong phiên này, theo thứ tự sẽ giới thiệu."
                actionLabel={editable ? 'Thêm sản phẩm' : undefined} onAction={()=>setPickerOpen(true)}/></div>
              : <ol className="ls-rows">{lineup.map((pid,i)=>{
                const p = productById.get(pid)
                const isLive = chosen.status === 'RUNNING' && pid === (owner ? status?.product_id : chosen.current_product_id)
                const isPending = owner && pid === status?.pending_product_id
                return <li key={pid} className={'ls-row' + (isLive ? ' is-live' : '') + (dragIndex === i ? ' is-dragging' : '')}
                  draggable={editable} onDragStart={()=>setDragIndex(i)} onDragEnd={()=>setDragIndex(null)} onDragOver={e=>{if (editable) e.preventDefault()}} onDrop={e=>onDrop(e, i)}>
                  {editable && <span className="ls-grip" aria-hidden="true"><GripVertical size={16}/></span>}
                  <ProductImage product={p}/>
                  <span className="ls-row-text">
                    <strong>{p?.name ?? `Sản phẩm #${pid}`}</strong>
                    <small>{p?.sku || '—'}</small>
                  </span>
                  <span className="ls-row-tags">
                    {isLive && <Badge tone="danger" dot>Đang phát</Badge>}
                    {isPending && <Badge tone="warning">Chờ chuyển</Badge>}
                    {p?.video_count ? <Badge tone="success"><Video size={12}/>{p.video_count} video</Badge>
                      : <a className="ls-novideo" href="#/product-videos" title="Gán video cho sản phẩm"><CircleAlert size={13}/>Chưa có video</a>}
                  </span>
                  {editable && <span className="ls-row-actions">
                    <button type="button" className="ls-icon-btn is-danger" title={isLive ? 'Không thể gỡ sản phẩm đang phát' : 'Gỡ khỏi phiên'} aria-label="Gỡ khỏi phiên"
                      disabled={busy || isLive || (chosen.status === 'RUNNING' && lineup.length === 1)} onClick={()=>edit(lineup.filter(id=>id !== pid))}><Trash2 size={15}/></button>
                  </span>}
                </li>
              })}</ol>}
            {dirty && <div className="ls-savebar">
              <span><CircleAlert size={15}/>Danh sách có thay đổi chưa lưu</span>
              <Button size="sm" variant="ghost" disabled={busy} onClick={()=>setDraft(null)}>Hoàn tác</Button>
              <Button size="sm" loading={busy} onClick={save}>Lưu danh sách</Button>
            </div>}
          </Card>

          <div className="ls-side">
            {chosen.status === 'DRAFT' && <Card className="ls-checks" title={<span className="ls-card-title"><CheckCircle2 size={17}/>Sẵn sàng LIVE?</span>}
              description={canStart ? 'Mọi điều kiện bắt buộc đã đạt.' : 'Hoàn tất các mục bắt buộc để bắt đầu.'}>
              <ul className="ls-checklist">{checks.map(c=><li key={c.label} className={c.ok ? 'is-ok' : c.blocking ? 'is-bad' : 'is-warn'}>
                {c.ok ? <CheckCircle2 size={16}/> : c.blocking ? <CircleDashed size={16}/> : <CircleAlert size={16}/>}
                <span>{c.label}{!c.ok && c.href && <> · <a href={c.href}>{c.href === '#/live' ? 'Kết nối' : 'Gán video'}</a></>}</span>
              </li>)}</ul>
            </Card>}
            {chosen.status !== 'DRAFT' && <Card className="ls-summary" title={<span className="ls-card-title"><Clock3 size={17}/>Tóm tắt</span>}>
              <dl className="ls-stats">
                <div><dt>Thời lượng</dt><dd>{durationText(chosen.started_at, chosen.ended_at)}</dd></div>
                <div><dt>Sản phẩm</dt><dd>{lineup.length}</dd></div>
                <div><dt>Lần chuyển SP</dt><dd>{switches}</dd></div>
                <div><dt>{chosen.status === 'RUNNING' ? 'Đang phát' : 'SP cuối'}</dt><dd className="ls-ellipsis">{productById.get((owner ? status?.product_id : chosen.current_product_id) ?? -1)?.name ?? '—'}</dd></div>
              </dl>
            </Card>}
            <Card className="ls-history" padding={false} title={<span className="ls-card-title"><History size={17}/>Lịch sử phiên</span>} description={`${events.length} sự kiện`}>
              {!events.length ? <p className="ls-muted ls-pad">Chưa có sự kiện.</p>
                : <ol className="ls-timeline">{events.slice().reverse().map(ev=><li key={ev.id} className={'ev-' + ev.event_type}>
                  <span className="ls-tl-dot"/>
                  <div>
                    <strong>{EVENT_LABEL[ev.event_type] ?? ev.event_type}{ev.product_id != null && <em> · {productById.get(ev.product_id)?.name ?? '#' + ev.product_id}</em>}</strong>
                    {ev.detail && <small>{eventDetail(ev.detail)}</small>}
                  </div>
                  <time title={dateTime(ev.created_at)}>{timeOnly(ev.created_at)}</time>
                </li>)}</ol>}
            </Card>
          </div>
        </div>
      </div>}
    </div>

    <Modal open={createOpen} title="Tạo phiên LIVE" size="sm" onClose={()=>!busy && setCreateOpen(false)} footer={<>
      <Button variant="ghost" disabled={busy} onClick={()=>setCreateOpen(false)}>Hủy</Button>
      <Button type="submit" form="ls-create-form" loading={busy} disabled={!nameInput.trim()}>Tạo & thêm sản phẩm</Button>
    </>}>
      <form id="ls-create-form" onSubmit={create}>
        <Input label="Tên phiên" autoFocus required maxLength={200} value={nameInput} onChange={e=>setNameInput(e.target.value)} placeholder="VD: LIVE thời trang tối 20/10" hint="Bước tiếp theo: chọn sản phẩm sẽ bán và sắp xếp thứ tự."/>
      </form>
    </Modal>

    <Modal open={renameOpen} title="Đổi tên phiên" size="sm" onClose={()=>!busy && setRenameOpen(false)} footer={<>
      <Button variant="ghost" disabled={busy} onClick={()=>setRenameOpen(false)}>Hủy</Button>
      <Button type="submit" form="ls-rename-form" loading={busy} disabled={!nameInput.trim()}>Lưu</Button>
    </>}>
      <form id="ls-rename-form" onSubmit={rename}><Input label="Tên phiên" autoFocus required maxLength={200} value={nameInput} onChange={e=>setNameInput(e.target.value)}/></form>
    </Modal>

    {pickerOpen && chosen && <ProductPicker products={products} exclude={lineup} onClose={()=>setPickerOpen(false)}
      onAdd={ids=>{edit([...lineup, ...ids]);setPickerOpen(false)}}/>}

    <ConfirmModal open={!!confirm} loading={busy} onClose={()=>setConfirm(null)} onConfirm={confirmAction}
      tone={confirm?.kind === 'discard' ? 'primary' : 'danger'}
      {...(confirm ? confirmText(confirm) : {})}/>

    {startOpen && chosen && <Modal open title={`Bắt đầu “${chosen.name}”`} size="md" onClose={()=>{if (!busy) setStartOpen(false)}} footer={<>
      <Button variant="ghost" disabled={busy} onClick={()=>setStartOpen(false)}>Hủy</Button>
      <Button leftIcon={<Play size={16}/>} loading={busy} disabled={startId == null}
        onClick={()=>void run(async ()=>{await sessionsApi.start(chosen.id, startId);setStartOpen(false);window.location.hash = '/live'}, 'Đã bắt đầu LIVE')}>Bắt đầu LIVE</Button>
    </>}>
      <div className="ls-picker">
        <p className="ls-muted">Chọn sản phẩm phát đầu tiên. Trong lúc LIVE bạn chuyển sang sản phẩm nào cũng được, kể cả sản phẩm chưa có trong danh sách.</p>
        <ul className="ls-picker-list">{lineup.map(pid=>{
          const p = productById.get(pid)
          return <li key={pid}><button type="button" className={startId === pid ? 'is-picked' : ''} disabled={!p?.video_count} aria-pressed={startId === pid} onClick={()=>setOpener(pid)}>
            <span className="ls-check ls-radio">{startId === pid && <Check size={13}/>}</span>
            <ProductImage product={p}/>
            <span className="ls-row-text"><strong>{p?.name ?? `Sản phẩm #${pid}`}</strong><small>{p?.sku || '—'}</small></span>
            {p?.video_count ? <Badge tone="success">{p.video_count} video</Badge> : <Badge tone="warning">Chưa có video</Badge>}
          </button></li>
        })}</ul>
        <Alert tone="info">Ghim sản phẩm này trên TikTok LIVE. Sau khi bắt đầu bạn sẽ được chuyển sang màn Điều khiển LIVE.</Alert>
      </div>
    </Modal>}
  </div>
}

function confirmText({kind, session}:NonNullable<Confirm>){
  switch (kind) {
    case 'finish': return {title:'Kết thúc phiên LIVE?', description:`OBS sẽ dừng phát và “${session.name}” được lưu vào lịch sử. Không thể tiếp tục phiên sau khi kết thúc.`, confirmLabel:'Kết thúc phiên'}
    case 'close-stale': return {title:'Đóng phiên bị ngắt?', description:`“${session.name}” không còn phát trên OBS. Phiên sẽ được đánh dấu Đã kết thúc; OBS không bị ảnh hưởng.`, confirmLabel:'Đóng phiên'}
    case 'delete': return {title:'Xóa phiên?', description:`Xóa “${session.name}” cùng danh sách sản phẩm và lịch sử. Không thể hoàn tác.`, confirmLabel:'Xóa phiên'}
    case 'discard': return {title:'Bỏ thay đổi chưa lưu?', description:`Danh sách sản phẩm của “${session.name}” có thay đổi chưa lưu.`, confirmLabel:'Bỏ thay đổi'}
  }
}

function ProductImage({product}:{product?:LiveProduct}){
  const [failed,setFailed] = useState(false)
  return <span className="ls-thumb">{product?.image_url && !failed ? <img src={product.image_url} alt="" loading="lazy" onError={()=>setFailed(true)}/> : <Package size={18}/>}</span>
}

/** Multi-select dialog that appends products to the lineup in the order they are ticked. */
function ProductPicker({products,exclude,onClose,onAdd}:{products:LiveProduct[];exclude:number[];onClose:()=>void;onAdd:(ids:number[])=>void}){
  const [query,setQuery] = useState('')
  const [picked,setPicked] = useState<number[]>([])
  const q = query.trim().toLocaleLowerCase('vi')
  const options = products.filter(p=>!exclude.includes(p.id) && (p.name + ' ' + p.sku).toLocaleLowerCase('vi').includes(q))
  const toggle = (id:number) => setPicked(prev=>prev.includes(id) ? prev.filter(x=>x !== id) : [...prev, id])
  return <Modal open title="Thêm sản phẩm vào phiên" size="md" onClose={onClose} footer={<>
    <span className="ls-picker-count">Đã chọn {picked.length}</span>
    <Button variant="ghost" onClick={onClose}>Hủy</Button>
    <Button disabled={!picked.length} onClick={()=>onAdd(picked)}>Thêm {picked.length || ''} sản phẩm</Button>
  </>}>
    <div className="ls-picker">
      <SearchInput autoFocus value={query} onChange={e=>setQuery(e.target.value)} placeholder="Tìm theo tên hoặc SKU…" aria-label="Tìm sản phẩm"/>
      <ul className="ls-picker-list">{options.map(p=>{
        const order = picked.indexOf(p.id)
        return <li key={p.id}><button type="button" className={order >= 0 ? 'is-picked' : ''} onClick={()=>toggle(p.id)} aria-pressed={order >= 0}>
          <span className="ls-check">{order >= 0 ? order + 1 : ''}</span>
          <ProductImage product={p}/>
          <span className="ls-row-text"><strong>{p.name}</strong><small>{p.sku || '—'}</small></span>
          {p.video_count ? <Badge tone="success">{p.video_count} video</Badge> : <Badge tone="warning">Chưa có video</Badge>}
        </button></li>
      })}{!options.length && <li className="ls-list-empty">{products.length === exclude.length ? 'Tất cả sản phẩm đã có trong phiên.' : 'Không có sản phẩm khớp.'}</li>}</ul>
      <p className="ls-muted">Số trên mỗi ô là thứ tự sẽ thêm vào cuối danh sách. Sản phẩm chưa có video vẫn thêm được nhưng không phát được cho tới khi gán video.</p>
    </div>
  </Modal>
}
