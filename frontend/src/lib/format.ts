/** Presentation helpers. */

export const fmtNumber = (value: number | null | undefined, digits = 0): string => {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—'
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits })
}

export const fmtMs = (value: number | null | undefined): string => {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—'
  const ms = Number(value)
  if (ms < 1000) return `${ms.toFixed(ms < 10 ? 2 : 0)} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

export const fmtUsd = (value: number | null | undefined): string => {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—'
  const num = Number(value)
  if (num === 0) return '$0.00'
  if (num < 0.01) return `$${num.toFixed(6)}`
  return `$${num.toFixed(2)}`
}

export const shortId = (id?: string | null): string => {
  const value = String(id || '')
  if (!value) return '—'
  return value.length > 19 ? `${value.slice(0, 9)}…${value.slice(-7)}` : value
}

export const humanize = (value?: string | null): string =>
  String(value || '')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())

export const titleCase = (value?: string | null): string => {
  const text = String(value || '').replace(/[_-]+/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

export const prettyJson = (value: unknown): string => {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

export const timeAgo = (iso?: string | null): string => {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  const seconds = Math.max(0, (Date.now() - date.getTime()) / 1000)
  if (seconds < 60) return `${Math.floor(seconds)}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

export const formatDateTime = (iso?: string | null): string => {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString()
}

export const formatDurationLabel = (ms: number | null | undefined): string => fmtMs(ms)

export const statusTone = (status?: string | null): 'ok' | 'danger' | 'warn' | 'info' | 'muted' => {
  switch ((status || '').toLowerCase()) {
    case 'success':
    case 'ok':
      return 'ok'
    case 'failure':
    case 'error':
    case 'timeout':
      return 'danger'
    case 'partial':
    case 'running':
      return 'warn'
    case 'cancelled':
      return 'info'
    default:
      return 'muted'
  }
}

export const evidenceTone = (status?: string | null): 'ok' | 'danger' | 'warn' | 'info' | 'muted' => {
  switch (status) {
    case 'Observed':
      return 'info'
    case 'Inferred Hypothesis':
      return 'warn'
    case 'Experimentally Supported':
    case 'Confirmed Under Specified Test Conditions':
      return 'ok'
    case 'Experimentally Refuted':
      return 'danger'
    default:
      return 'muted'
  }
}

export const actionIcon = (type?: string | null): string => {
  switch (type) {
    case 'planning':
      return '◇'
    case 'agent':
      return '◉'
    case 'model':
      return '✧'
    case 'tool':
      return '⚒'
    case 'retrieval':
      return '⌕'
    case 'delegation':
      return '↗'
    case 'evaluation':
      return '✓'
    case 'state':
      return '▦'
    default:
      return '·'
  }
}

export const pct = (value: number | null | undefined, digits = 0): string =>
  value === null || value === undefined ? '—' : `${(Number(value) * 100).toFixed(digits)}%`
