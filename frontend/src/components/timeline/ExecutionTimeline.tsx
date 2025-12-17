import { useMemo } from 'react'
import clsx from 'clsx'
import type { Span } from '../../lib/types'
import { actionIcon, fmtMs, humanize } from '../../lib/format'

interface TimelineProps {
  spans: Span[]
  selectedSpanId: string | null
  onSelect: (spanId: string) => void
  criticalPath?: string[]
}

export default function ExecutionTimeline({ spans, selectedSpanId, onSelect, criticalPath = [] }: TimelineProps) {
  const { min, max } = useMemo(() => {
    const times: number[] = []
    spans.forEach((span) => {
      const start = new Date(span.start_time).getTime()
      const end = span.end_time ? new Date(span.end_time).getTime() : start
      if (Number.isFinite(start)) times.push(start)
      if (Number.isFinite(end)) times.push(end)
    })
    if (!times.length) return { min: 0, max: 1 }
    const low = Math.min(...times)
    const high = Math.max(...times)
    return { min: low, max: high > low ? high : low + 1 }
  }, [spans])

  const critical = new Set(criticalPath)
  const total = max - min

  if (!spans.length) {
    return (
      <div className="flex h-40 items-center justify-center rounded-lg border border-dashed border-line text-xs text-ink-muted">
        This run has no recorded spans.
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-2xs text-ink-faint">
        <span>{new Date(min).toLocaleTimeString()}</span>
        <span>{(total / 1000).toFixed(2)}s recorded window</span>
        <span>{new Date(max).toLocaleTimeString()}</span>
      </div>
      <div className="space-y-1">
        {spans.map((span) => {
          const start = new Date(span.start_time).getTime()
          const end = span.end_time ? new Date(span.end_time).getTime() : start
          const left = ((start - min) / total) * 100
          const width = Math.max(0.8, ((end - start) / total) * 100)
          const failed = span.status === 'error' || span.status === 'timeout'
          const isCritical = critical.has(span.span_id)
          return (
            <div key={span.span_id} className="flex items-center gap-2">
              <div
                className="flex w-56 shrink-0 items-center gap-1.5 truncate text-2xs text-ink-muted"
                style={{ paddingLeft: span.parent_span_id ? 14 : 0 }}
                title={`${span.name} (${span.span_id})`}
              >
                <span className="text-ink-faint" aria-hidden="true">
                  {actionIcon(span.action_type)}
                </span>
                <span className="truncate">{span.name}</span>
              </div>
              <div className="relative h-6 flex-1 rounded bg-surface-sunken">
                <button
                  type="button"
                  onClick={() => onSelect(span.span_id)}
                  title={`${span.name} · ${span.status} · ${fmtMs(span.duration_ms ?? null)}`}
                  aria-label={`Select ${span.name}`}
                  className={clsx(
                    'absolute top-1 h-4 rounded-sm border transition',
                    failed
                      ? 'border-danger/60 bg-danger/35'
                      : span.action_type === 'model'
                        ? 'border-info/50 bg-info/25'
                        : span.action_type === 'retrieval'
                          ? 'border-accent/50 bg-accent/25'
                          : 'border-ok/40 bg-ok/25',
                    isCritical && 'ring-1 ring-warn',
                    selectedSpanId === span.span_id && 'ring-2 ring-accent',
                  )}
                  style={{ left: `${left}%`, width: `${Math.min(width, 100 - left)}%` }}
                >
                  {width > 12 && (
                    <span className="block truncate px-1.5 text-left text-[10px] leading-4 text-ink">
                      {span.tool_name || humanize(span.action_type)}
                    </span>
                  )}
                </button>
              </div>
              <div className="w-20 shrink-0 text-right font-mono text-2xs text-ink-faint">
                {fmtMs(span.duration_ms ?? null)}
              </div>
            </div>
          )
        })}
      </div>
      <p className="text-2xs leading-relaxed text-ink-faint">
        Bar width encodes the recorded start/end timestamps. Overlapping bars indicate concurrent execution. Nesting is
        structural; it is not treated as a data dependency.
      </p>
    </div>
  )
}
