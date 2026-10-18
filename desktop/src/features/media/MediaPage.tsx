import { useCallback, useEffect, useState } from 'react'
import { open } from '@tauri-apps/plugin-dialog'
import { AlertCircle, FolderOpen, FolderPlus, Play, RefreshCw, Search, Trash2, Video, XCircle } from 'lucide-react'
import { useApp } from '../../app/store'
import { ActionButton, Badge, Button, Card, Empty, Input, Progress, formatBytes, formatDateTime } from '../../app/ui'
import { call } from '../../services/core'
import type { MediaItem } from '../../types/ipc'

const PAGE = 100

export function MediaPage() {
  const { state, run, refreshFolders, refresh } = useApp()
  const [items, setItems] = useState<MediaItem[]>([])
  const [total, setTotal] = useState(0)
  const [query, setQuery] = useState('')
  const [offset, setOffset] = useState(0)
  const scanning = !!state.scan || !!state.status?.media.scan

  const load = useCallback(async () => {
    const result = await run(() => call('media.list', { offset, limit: PAGE, query }))
    if (result) {
      setItems(result.items)
      setTotal(result.total)
    }
  }, [offset, query, run])

  // Folders can change outside this page (Cloud media_sync, another window): reload on open.
  useEffect(() => {
    void refreshFolders()
  }, [refreshFolders])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 200)
    return () => window.clearTimeout(timer)
  }, [load, state.lastScan])

  const addFolder = async () => {
    const picked = await open({ directory: true, multiple: true, title: 'Chọn thư mục chứa video MP4' })
    const paths = Array.isArray(picked) ? picked : picked ? [picked] : []
    for (const path of paths) {
      await run(() => call('media.folders.add', { path }), `Đã thêm ${path}`)
    }
    if (paths.length) {
      await refreshFolders()
      await run(() => call('media.scan'))
    }
  }

  return (
    <div className="stack">
      <Card
        title="Thư mục được phép"
        icon={<FolderOpen size={16} />}
        actions={<>
          <ActionButton icon={<FolderPlus size={16} />} onAction={addFolder}>Thêm thư mục</ActionButton>
          {scanning ? (
            <ActionButton variant="ghost" icon={<XCircle size={16} />} onAction={() => run(() => call('media.scan_cancel'))}>Huỷ quét</ActionButton>
          ) : (
            <ActionButton variant="primary" icon={<RefreshCw size={16} />} disabled={!state.folders.length} onAction={() => run(() => call('media.scan'))}>Quét lại</ActionButton>
          )}
        </>}
      >
        <p className="muted small">Agent chỉ phát file .mp4 nằm trong các thư mục này. Chỉ thông tin (tên, dung lượng, thời gian sửa) được đồng bộ lên Cloud — video không bao giờ bị tải lên. Bỏ thư mục không xoá file trên máy.</p>
        {scanning && (
          <div className="scan-box">
            <Progress indeterminate />
            <span>Đang quét… {state.scan ? `${state.scan.scanned_files.toLocaleString('vi-VN')} file, tìm thấy ${state.scan.found.toLocaleString('vi-VN')} MP4` : ''}</span>
          </div>
        )}
        {state.lastScan && !scanning && (
          <div className={`scan-result ${state.lastScan.state === 'succeeded' ? '' : 'is-warn'}`}>
            {state.lastScan.state === 'succeeded'
              ? <>Quét xong {state.lastScan.total} video trong {(state.lastScan.duration_ms / 1000).toFixed(1)}s · thêm {state.lastScan.added} · không còn {state.lastScan.removed}{state.lastScan.truncated ? ' · đã đạt giới hạn 10.000 video' : ''}</>
              : state.lastScan.state === 'cancelled' ? 'Đã huỷ quét, giữ nguyên thư viện cũ.' : `Quét lỗi: ${state.lastScan.error?.message}`}
            {state.lastScan.errors?.map((e) => <small key={e}><AlertCircle size={12} /> {e}</small>)}
          </div>
        )}
        {state.folders.length === 0 ? (
          <Empty icon={<FolderPlus size={28} />} title="Chưa có thư mục video">Thêm thư mục chứa MP4 để Agent quét và đồng bộ danh mục lên Cloud.</Empty>
        ) : (
          <ul className="list">
            {state.folders.map((folder) => (
              <li key={folder.path}>
                <div>
                  <strong className="selectable">{folder.path}</strong>
                  <small className="muted">{folder.video_count.toLocaleString('vi-VN')} video</small>
                </div>
                {!folder.exists && <Badge tone="warn">Không truy cập được</Badge>}
                <ActionButton
                  variant="ghost"
                  icon={<Trash2 size={16} />}
                  onAction={async () => {
                    if (!window.confirm(`Bỏ thư mục khỏi Agent?\n${folder.path}\n\nFile video trên máy KHÔNG bị xoá.`)) return
                    await run(() => call('media.folders.remove', { path: folder.path }), 'Đã bỏ thư mục')
                    await Promise.all([refreshFolders(), refresh(), load()])
                  }}
                >Bỏ</ActionButton>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card
        title={`Video (${total.toLocaleString('vi-VN')})`}
        icon={<Video size={16} />}
        actions={<div className="search"><Search size={14} /><Input value={query} placeholder="Tìm theo tên file" onChange={(e) => { setOffset(0); setQuery(e.target.value) }} /></div>}
      >
        {items.length === 0 ? (
          <Empty icon={<Video size={28} />} title={query ? 'Không có video khớp' : 'Thư viện trống'} />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>Tên file</th><th>Dung lượng</th><th>Sửa lần cuối</th><th>Trạng thái</th><th /></tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.media_id}>
                    <td>
                      <strong>{item.filename}</strong>
                      <small className="muted mono" title={item.media_id}>{item.media_id.slice(0, 16)}…</small>
                    </td>
                    <td>{formatBytes(item.size_bytes)}</td>
                    <td>{item.modified_ns ? formatDateTime(new Date(item.modified_ns / 1e6).toISOString()) : '—'}</td>
                    <td>{item.available ? <Badge tone="ok">Sẵn sàng</Badge> : <Badge tone="danger">File đã mất</Badge>}</td>
                    <td className="right">
                      <ActionButton
                        variant="ghost"
                        icon={<Play size={14} />}
                        disabled={!item.available}
                        onAction={() => run(() => call('playback.play', { media_id: item.media_id }), `Đang phát ${item.filename} trên OBS`)}
                      >Phát thử</ActionButton>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {total > PAGE && (
          <div className="pager">
            <Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Trước</Button>
            <span className="muted">{offset + 1}–{Math.min(total, offset + PAGE)} / {total}</span>
            <Button disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Sau</Button>
          </div>
        )}
      </Card>
    </div>
  )
}
