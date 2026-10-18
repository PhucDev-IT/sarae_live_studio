import { useState, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from 'react'
import { Loader2 } from 'lucide-react'

export type Tone = 'ok' | 'warn' | 'danger' | 'info' | 'muted' | 'accent'

export function Badge({ tone = 'muted', children, dot = true }: { tone?: Tone; children: ReactNode; dot?: boolean }) {
  return <span className={`badge badge-${tone}`}>{dot && <i className="badge-dot" />}{children}</span>
}

export function Card({ title, icon, actions, children, className = '' }: {
  title?: ReactNode; icon?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string
}) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          <h3>{icon}{title}</h3>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      <div className="card-body">{children}</div>
    </section>
  )
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  icon?: ReactNode
  busy?: boolean
}

export function Button({ variant = 'secondary', icon, busy, children, disabled, ...rest }: ButtonProps) {
  return (
    <button className={`btn btn-${variant}`} disabled={disabled || busy} {...rest}>
      {busy ? <Loader2 className="spin" size={16} /> : icon}
      {children && <span>{children}</span>}
    </button>
  )
}

/** Button whose async handler disables it until done (prevents double submission). */
export function ActionButton({ onAction, ...props }: Omit<ButtonProps, 'onClick' | 'busy'> & { onAction: () => Promise<unknown> }) {
  const [busy, setBusy] = useState(false)
  return (
    <Button
      {...props}
      busy={busy}
      onClick={async () => {
        setBusy(true)
        try {
          await onAction()
        } finally {
          setBusy(false)
        }
      }}
    />
  )
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <small className="field-hint">{hint}</small>}
    </label>
  )
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input className="input" {...props} />
}

export function Toggle({ checked, onChange, label, disabled }: {
  checked: boolean; onChange: (value: boolean) => void; label: ReactNode; disabled?: boolean
}) {
  return (
    <label className={`toggle ${disabled ? 'is-disabled' : ''}`}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="toggle-track"><span className="toggle-thumb" /></span>
      <span>{label}</span>
    </label>
  )
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <strong className="stat-value">{value}</strong>
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  )
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      {icon}
      <strong>{title}</strong>
      {children && <p>{children}</p>}
    </div>
  )
}

export function ErrorLine({ error }: { error?: { code: string; message: string } | null }) {
  if (!error) return null
  return (
    <div className="error-line">
      <span>{error.message}</span>
      <code>{error.code}</code>
    </div>
  )
}

export function Progress({ value, indeterminate }: { value?: number; indeterminate?: boolean }) {
  return (
    <div className={`progress ${indeterminate ? 'is-indeterminate' : ''}`}>
      <div className="progress-bar" style={indeterminate ? undefined : { width: `${Math.min(100, Math.max(0, value ?? 0))}%` }} />
    </div>
  )
}

export function formatTime(iso?: string | null): string {
  if (!iso) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleTimeString('vi-VN')
}

export function formatDateTime(iso?: string | null): string {
  if (!iso) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('vi-VN')
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[unit]}`
}

export function formatDuration(ms?: number | null): string {
  if (ms == null || ms < 0) return '—'
  const total = Math.round(ms / 1000)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${s.toString().padStart(2, '0')}`
}
