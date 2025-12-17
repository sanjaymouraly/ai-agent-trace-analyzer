import clsx from 'clsx'
import type { ReactNode } from 'react'
import { evidenceTone, humanize, statusTone } from '../../lib/format'

const toneClasses: Record<string, string> = {
  ok: 'border-ok/40 bg-ok/10 text-ok',
  danger: 'border-danger/40 bg-danger/10 text-danger',
  warn: 'border-warn/40 bg-warn/10 text-warn',
  info: 'border-info/40 bg-info/10 text-info',
  muted: 'border-line bg-surface text-ink-muted',
}

export function Badge({
  children,
  tone = 'muted',
  className,
  title,
}: {
  children: ReactNode
  tone?: 'ok' | 'danger' | 'warn' | 'info' | 'muted'
  className?: string
  title?: string
}) {
  return (
    <span
      title={title}
      className={clsx(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-2xs font-semibold',
        toneClasses[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}

export function StatusBadge({ status }: { status?: string | null }) {
  return <Badge tone={statusTone(status)}>{humanize(status || 'unknown')}</Badge>
}

export function EvidenceBadge({ status }: { status?: string | null }) {
  const label = status || 'Insufficient Evidence'
  return (
    <Badge tone={evidenceTone(label)} title={`Evidence label: ${label}`}>
      {label}
    </Badge>
  )
}

export function Card({
  title,
  subtitle,
  actions,
  children,
  className,
  bodyClassName,
  id,
}: {
  title?: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
  id?: string
}) {
  return (
    <section id={id} className={clsx('panel', className)}>
      {(title || actions) && (
        <header className="panel-head">
          <div className="min-w-0">
            {title && <h2 className="panel-title">{title}</h2>}
            {subtitle && <p className="panel-subtitle">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={clsx('p-5', bodyClassName)}>{children}</div>
    </section>
  )
}

export function EmptyState({
  icon = '≋',
  title,
  description,
  action,
}: {
  icon?: string
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-line px-6 py-12 text-center">
      <div className="text-2xl text-ink-faint" aria-hidden="true">
        {icon}
      </div>
      <div>
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        {description && <p className="mx-auto mt-1 max-w-md text-xs leading-relaxed text-ink-muted">{description}</p>}
      </div>
      {action}
    </div>
  )
}

export function Spinner({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-xs text-ink-muted" role="status" aria-live="polite">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-line border-t-accent" />
      {label}
    </div>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx('animate-pulse rounded-md bg-surface-sunken', className)} />
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-danger/40 bg-danger/5 px-6 py-10 text-center">
      <h3 className="text-sm font-semibold text-danger">Could not load this view</h3>
      <p className="max-w-lg text-xs text-ink-muted">{message}</p>
      {onRetry && (
        <button type="button" className="btn" onClick={onRetry}>
          ↻ Try again
        </button>
      )}
    </div>
  )
}

export function JsonBlock({ value, label }: { value: unknown; label?: string }) {
  return (
    <details className="group">
      <summary className="cursor-pointer list-none text-2xs font-medium text-ink-muted hover:text-ink">
        <span className="mr-1 inline-block transition group-open:rotate-90">▸</span>
        {label || 'Show payload'}
      </summary>
      <pre className="json-block mt-2">{JSON.stringify(value, null, 2)}</pre>
    </details>
  )
}

export function MetricCard({
  label,
  value,
  note,
  icon,
  tone = 'muted',
}: {
  label: string
  value: ReactNode
  note?: ReactNode
  icon?: string
  tone?: 'muted' | 'ok' | 'danger' | 'warn'
}) {
  const valueTone =
    tone === 'ok' ? 'text-ok' : tone === 'danger' ? 'text-danger' : tone === 'warn' ? 'text-warn' : 'text-ink'
  return (
    <article className="panel px-4 py-4">
      <div className="flex items-start justify-between gap-2">
        <span className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">{label}</span>
        {icon && (
          <span className="text-sm text-ink-faint" aria-hidden="true">
            {icon}
          </span>
        )}
      </div>
      <div className={clsx('mt-2 text-2xl font-semibold tracking-tight', valueTone)}>{value}</div>
      {note && <div className="mt-1 text-2xs leading-relaxed text-ink-muted">{note}</div>}
    </article>
  )
}

export function Callout({
  children,
  tone = 'info',
  title,
}: {
  children: ReactNode
  tone?: 'info' | 'warn' | 'danger' | 'ok'
  title?: ReactNode
}) {
  const tones: Record<string, string> = {
    info: 'border-info/40 bg-info/5 text-ink',
    warn: 'border-warn/40 bg-warn/5 text-ink',
    danger: 'border-danger/40 bg-danger/5 text-ink',
    ok: 'border-ok/40 bg-ok/5 text-ink',
  }
  return (
    <div className={clsx('flex gap-3 rounded-lg border px-4 py-3 text-xs leading-relaxed', tones[tone])}>
      <span aria-hidden="true">{tone === 'danger' ? '!' : tone === 'warn' ? '⚠' : tone === 'ok' ? '✓' : 'ⓘ'}</span>
      <div className="min-w-0">
        {title && <strong className="block font-semibold">{title}</strong>}
        <div>{children}</div>
      </div>
    </div>
  )
}
