import { useMemo, useState, type FormEvent } from 'react'
import { Film, Pencil, Plus, Search, Trash2, UserRound } from 'lucide-react'
import { studioClient, type Media, type Presenter } from '../../services/studio'
import { EmptyState, ErrorNotice, Modal, PresenterAvatar, errorMessage } from './ui'

type Props = {
  presenters: Presenter[]; media: Media[]; loading: boolean; disabled: boolean
  onRefresh: () => Promise<void>; onOpenMedia: (presenterId: string) => void; onNotice: (message: string) => void
}
type StatusFilter = 'all' | 'active' | 'inactive'
const filters: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: 'Tất cả' }, { value: 'active', label: 'Đang hoạt động' }, { value: 'inactive', label: 'Tạm nghỉ' },
]

export function PresentersPanel({ presenters, media, loading, disabled, onRefresh, onOpenMedia, onNotice }: Props) {
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [editing, setEditing] = useState<Presenter | 'new' | null>(null)
  const [deleting, setDeleting] = useState<Presenter | null>(null)
  const [busy, setBusy] = useState(false)
  const [toggling, setToggling] = useState('')
  const [error, setError] = useState('')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [active, setActive] = useState(true)
  const counts = useMemo(() => {
    const result = new Map<string, number>()
    for (const item of media) for (const id of item.presenter_ids) result.set(id, (result.get(id) ?? 0) + 1)
    return result
  }, [media])
  const visible = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase('vi')
    return presenters.filter(presenter => (statusFilter === 'all' || presenter.active === (statusFilter === 'active'))
      && `${presenter.name} ${presenter.description}`.toLocaleLowerCase('vi').includes(needle))
  }, [presenters, query, statusFilter])
  const activeCount = presenters.filter(presenter => presenter.active).length

  const open = (presenter: Presenter | 'new') => {
    setName(presenter === 'new' ? '' : presenter.name)
    setDescription(presenter === 'new' ? '' : presenter.description)
    setActive(presenter === 'new' ? true : presenter.active)
    setError('')
    setEditing(presenter)
  }
  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (busy || disabled || !editing) return
    if (!name.trim()) { setError('Vui lòng nhập tên người giới thiệu.'); return }
    setBusy(true)
    setError('')
    try {
      const input = { name: name.trim(), description: description.trim(), active }
      if (editing === 'new') await studioClient.createPresenter(input)
      else await studioClient.updatePresenter(editing.id, input)
      setEditing(null)
      onNotice(editing === 'new' ? 'Đã thêm người giới thiệu.' : 'Đã lưu thay đổi.')
      await onRefresh()
    } catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy(false) }
  }
  const toggle = async (presenter: Presenter) => {
    if (disabled || toggling) return
    setToggling(presenter.id)
    setError('')
    try {
      await studioClient.updatePresenter(presenter.id, { active: !presenter.active })
      onNotice(presenter.active ? `${presenter.name} đã chuyển sang tạm nghỉ.` : `${presenter.name} đã hoạt động trở lại.`)
      await onRefresh()
    } catch (cause) { setError(errorMessage(cause)) }
    finally { setToggling('') }
  }
  const remove = async () => {
    if (!deleting || busy || disabled) return
    setBusy(true)
    setError('')
    try {
      await studioClient.deletePresenter(deleting.id)
      setDeleting(null)
      onNotice('Đã xóa người giới thiệu.')
      await onRefresh()
    } catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy(false) }
  }

  const createButton = <button className="studio-button primary" disabled={disabled} onClick={() => open('new')}><Plus size={17} />Thêm người giới thiệu</button>
  return <>
    <div className="studio-page-heading"><div><h1>Người giới thiệu</h1><p>Những gương mặt xuất hiện trong video bán hàng của bạn.</p></div>{createButton}</div>
    {error && !editing && !deleting && <ErrorNotice message={error} />}
    <section className="studio-card">
      <div className="studio-toolbar">
        <label className="studio-search"><Search size={17} /><input aria-label="Tìm người giới thiệu" placeholder="Tìm theo tên hoặc ghi chú…" value={query} onChange={event => setQuery(event.target.value)} /></label>
        <div className="studio-segmented" role="group" aria-label="Lọc theo trạng thái">{filters.map(filter => <button key={filter.value} aria-pressed={statusFilter === filter.value} className={statusFilter === filter.value ? 'active' : ''} onClick={() => setStatusFilter(filter.value)}>{filter.label}</button>)}</div>
        <span className="studio-muted">{activeCount}/{presenters.length} đang hoạt động</span>
      </div>
      {loading ? <div className="studio-empty" role="status">Đang tải người giới thiệu…</div>
        : !visible.length ? <EmptyState icon={<UserRound size={30} />}
          title={query || statusFilter !== 'all' ? 'Không có người giới thiệu phù hợp' : 'Thêm người giới thiệu đầu tiên'}
          description={query || statusFilter !== 'all' ? 'Thử đổi từ khóa hoặc bộ lọc trạng thái.' : 'Ghi lại người dẫn trong từng video để dễ chọn clip đúng người khi lên LIVE.'}
          action={query || statusFilter !== 'all' ? <button className="studio-button" onClick={() => { setQuery(''); setStatusFilter('all') }}>Xóa bộ lọc</button> : createButton} />
        : <div className="studio-presenter-grid">{visible.map(presenter => {
          const videoCount = counts.get(presenter.id) ?? 0
          return <article key={presenter.id} className={`studio-presenter-card${presenter.active ? '' : ' inactive'}`}>
            <div className="studio-presenter-top">
              <PresenterAvatar presenter={presenter} size="lg" />
              <div className="studio-actions"><button className="studio-button ghost icon-only" disabled={disabled || busy} title={`Sửa ${presenter.name}`} aria-label={`Sửa ${presenter.name}`} onClick={() => open(presenter)}><Pencil size={16} /></button><button className="studio-button ghost icon-only danger" disabled={disabled || busy} title={`Xóa ${presenter.name}`} aria-label={`Xóa ${presenter.name}`} onClick={() => { setError(''); setDeleting(presenter) }}><Trash2 size={16} /></button></div>
            </div>
            <h3 title={presenter.name}>{presenter.name}</h3>
            <p className="studio-presenter-note">{presenter.description || 'Chưa có ghi chú'}</p>
            <div className="studio-presenter-footer">
              <button className="studio-button ghost studio-video-link" disabled={!videoCount} onClick={() => onOpenMedia(presenter.id)}><Film size={15} />{videoCount} video</button>
              <label className="studio-switch" title={presenter.active ? 'Chuyển sang tạm nghỉ' : 'Bật hoạt động'}>
                <input type="checkbox" role="switch" checked={presenter.active} disabled={disabled || toggling === presenter.id} onChange={() => void toggle(presenter)} aria-label={`${presenter.name} đang hoạt động`} />
                <span aria-hidden="true" /><small>{presenter.active ? 'Hoạt động' : 'Tạm nghỉ'}</small>
              </label>
            </div>
          </article>
        })}</div>}
    </section>
    {editing && <Modal title={editing === 'new' ? 'Thêm người giới thiệu' : 'Chỉnh sửa người giới thiệu'} onClose={() => { if (!busy) setEditing(null) }}><form className="studio-form" onSubmit={event => void save(event)}>
      <div className="studio-presenter-preview"><PresenterAvatar presenter={{ id: editing === 'new' ? 'new' : editing.id, name: name || '?' }} size="lg" /><div><strong>{name.trim() || 'Tên người giới thiệu'}</strong><span>{active ? 'Đang hoạt động' : 'Tạm nghỉ'}</span></div></div>
      <label className="studio-field">Tên hiển thị <span className="studio-required">*</span><input autoFocus data-autofocus required maxLength={200} value={name} disabled={busy} placeholder="Ví dụ: Chị Lan" onChange={event => setName(event.target.value)} /></label>
      <label className="studio-field">Ghi chú<textarea rows={4} maxLength={10000} value={description} disabled={busy} placeholder="Phong cách dẫn, giọng vùng miền, nhóm sản phẩm phù hợp…" onChange={event => setDescription(event.target.value)} /></label>
      <label className="studio-switch studio-switch-field"><input type="checkbox" role="switch" checked={active} disabled={busy} onChange={event => setActive(event.target.checked)} /><span aria-hidden="true" /><small>Đang hoạt động — hiển thị khi gắn video</small></label>
      {error && <ErrorNotice message={error} />}
      {disabled && <ErrorNotice message="Studio Core đang mất kết nối. Kết nối lại trước khi lưu." />}
      <div className="studio-form-actions"><button type="button" className="studio-button" disabled={busy} onClick={() => setEditing(null)}>Hủy</button><button className="studio-button primary" disabled={busy || disabled}>{busy ? 'Đang lưu…' : editing === 'new' ? 'Thêm người giới thiệu' : 'Lưu thay đổi'}</button></div>
    </form></Modal>}
    {deleting && <Modal title="Xóa người giới thiệu?" onClose={() => { if (!busy) setDeleting(null) }}><div className="studio-form"><p>Bạn sắp xóa <strong>{deleting.name}</strong>. Thao tác này không thể hoàn tác.</p><p className="studio-muted">{(counts.get(deleting.id) ?? 0) ? `${counts.get(deleting.id)} video sẽ được giữ nguyên trong thư viện và chỉ bỏ liên kết với người này.` : 'Người này chưa gắn với video nào.'} Nếu chỉ tạm không dùng, hãy chuyển sang tạm nghỉ.</p>{error && <ErrorNotice message={error} />}<div className="studio-form-actions"><button className="studio-button" disabled={busy} onClick={() => setDeleting(null)}>Giữ lại</button><button className="studio-button danger-solid" disabled={busy || disabled} onClick={() => void remove()}>{busy ? 'Đang xóa…' : 'Xóa người giới thiệu'}</button></div></div></Modal>}
  </>
}
