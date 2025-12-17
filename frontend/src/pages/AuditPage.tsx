import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { formatDateTime, humanize, shortId } from '../lib/format'
import type { AuditEvent } from '../lib/types'
import { useAsync } from '../lib/useAsync'
import { Badge, Card, EmptyState, ErrorState, Skeleton } from '../components/ui'

export default function AuditPage() {
  const { data, error, loading, reload } = useAsync<{ items: AuditEvent[] }>(() => api('/api/v1/audit', { query: { limit: 200 } }), [])

  if (error) return <ErrorState message={error} onRetry={reload} />
  if (loading && !data) return <Skeleton className="h-96" />

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">Operations</div>
          <h1 className="mt-1 text-xl font-semibold tracking-tight">Audit log</h1>
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-ink-muted">
            Imports, re-ingestion, analysis, annotations, experiments, AI calls and configuration changes. Trace payloads
            are never written to the log.
          </p>
        </div>
        <button type="button" className="btn" onClick={reload}>
          ↻ Refresh
        </button>
      </div>

      <Card title="Recorded events" subtitle={`${data?.items.length ?? 0} event(s), newest first`} bodyClassName="p-0">
        {data?.items.length ? (
          <div className="overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Event</th>
                  <th>Actor</th>
                  <th>Run</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((event) => (
                  <tr key={event.id}>
                    <td className="whitespace-nowrap font-mono text-ink-muted">{formatDateTime(event.created_at)}</td>
                    <td>
                      <Badge tone={event.event_type.startsWith('llm') ? 'info' : event.event_type.includes('deleted') ? 'danger' : 'muted'}>
                        {humanize(event.event_type)}
                      </Badge>
                    </td>
                    <td className="text-ink-muted">{event.actor}</td>
                    <td>
                      {event.run_id ? (
                        <Link to={`/runs/${encodeURIComponent(event.run_id)}`} className="link mono">
                          {shortId(event.run_id)}
                        </Link>
                      ) : (
                        <span className="text-ink-faint">—</span>
                      )}
                    </td>
                    <td className="max-w-md">
                      <code className="mono break-all text-ink-faint">{JSON.stringify(event.detail)}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="p-5">
            <EmptyState icon="▤" title="No audit events yet" description="Imports, changes and AI calls are recorded here." />
          </div>
        )}
      </Card>
    </div>
  )
}
