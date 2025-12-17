import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { fmtMs, fmtNumber, fmtUsd, shortId, timeAgo } from '../lib/format'
import type { RunStatus, RunsResponse } from '../lib/types'
import { useAsync } from '../lib/useAsync'
import { Card, EmptyState, ErrorState, Skeleton, StatusBadge } from '../components/ui'

const STATUSES: RunStatus[] = ['success', 'failure', 'partial', 'running', 'unknown']
const PAGE_SIZE = 25

export default function RunsPage() {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [provider, setProvider] = useState('')
  const [model, setModel] = useState('')
  const [agent, setAgent] = useState('')
  const [minDuration, setMinDuration] = useState('')
  const [maxDuration, setMaxDuration] = useState('')
  const [offset, setOffset] = useState(0)
  const [applied, setApplied] = useState({
    query: '',
    status: '',
    provider: '',
    model: '',
    agent: '',
    minDuration: '',
    maxDuration: '',
  })

  const params = useMemo(
    () => ({
      query: applied.query,
      status: applied.status,
      provider: applied.provider,
      model: applied.model,
      agent: applied.agent,
      min_duration_ms: applied.minDuration ? Number(applied.minDuration) : undefined,
      max_duration_ms: applied.maxDuration ? Number(applied.maxDuration) : undefined,
      limit: PAGE_SIZE,
      offset,
    }),
    [applied, offset],
  )

  const { data, error, loading, reload } = useAsync<RunsResponse>(() => api('/api/v1/runs', { query: params }), [params])

  const apply = () => {
    setOffset(0)
    setApplied({ query, status, provider, model, agent, minDuration, maxDuration })
  }

  const reset = () => {
    setQuery('')
    setStatus('')
    setProvider('')
    setModel('')
    setAgent('')
    setMinDuration('')
    setMaxDuration('')
    setApplied({ query: '', status: '', provider: '', model: '', agent: '', minDuration: '', maxDuration: '' })
    setOffset(0)
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">Trace index</div>
          <h1 className="mt-1 text-xl font-semibold tracking-tight">Trace explorer</h1>
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-ink-muted">
            Search and filter stored executions by task, identifier, provider, model, agent, status and duration.
          </p>
        </div>
        <button type="button" className="btn" onClick={reload}>
          ↻ Refresh
        </button>
      </div>

      <Card bodyClassName="p-4">
        <form
          className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7"
          onSubmit={(event) => {
            event.preventDefault()
            apply()
          }}
        >
          <label className="flex flex-col gap-1 text-2xs font-medium text-ink-muted">
            Search
            <input
              className="field"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="task, run id, trace id…"
              aria-label="Search executions"
            />
          </label>
          <label className="flex flex-col gap-1 text-2xs font-medium text-ink-muted">
            Status
            <select className="field" value={status} onChange={(event) => setStatus(event.target.value)} aria-label="Filter by status">
              <option value="">All statuses</option>
              {STATUSES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-2xs font-medium text-ink-muted">
            Provider
            <input className="field" value={provider} onChange={(event) => setProvider(event.target.value)} placeholder="openai…" />
          </label>
          <label className="flex flex-col gap-1 text-2xs font-medium text-ink-muted">
            Model
            <input className="field" value={model} onChange={(event) => setModel(event.target.value)} placeholder="gpt-…" />
          </label>
          <label className="flex flex-col gap-1 text-2xs font-medium text-ink-muted">
            Agent
            <input className="field" value={agent} onChange={(event) => setAgent(event.target.value)} placeholder="agent id…" />
          </label>
          <label className="flex flex-col gap-1 text-2xs font-medium text-ink-muted">
            Min duration (ms)
            <input
              className="field"
              type="number"
              min={0}
              value={minDuration}
              onChange={(event) => setMinDuration(event.target.value)}
              placeholder="0"
            />
          </label>
          <label className="flex flex-col gap-1 text-2xs font-medium text-ink-muted">
            Max duration (ms)
            <input
              className="field"
              type="number"
              min={0}
              value={maxDuration}
              onChange={(event) => setMaxDuration(event.target.value)}
              placeholder="∞"
            />
          </label>
          <div className="flex items-end gap-2 sm:col-span-2 lg:col-span-4 xl:col-span-7">
            <button type="submit" className="btn btn-primary">
              Apply filters
            </button>
            <button type="button" className="btn" onClick={reset}>
              Clear
            </button>
          </div>
        </form>
      </Card>

      <Card
        title="Stored executions"
        subtitle={data ? `${data.total} matching run(s) · newest first` : 'Loading…'}
        bodyClassName="p-0"
      >
        {error ? (
          <div className="p-5">
            <ErrorState message={error} onRetry={reload} />
          </div>
        ) : loading && !data ? (
          <div className="space-y-2 p-5">
            {[0, 1, 2, 3, 4].map((index) => (
              <Skeleton key={index} className="h-10" />
            ))}
          </div>
        ) : !data || data.items.length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon="≋"
              title="No matching executions"
              description="Adjust the filters, import a trace, or seed the deterministic sample workflows from the overview page."
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>Execution</th>
                  <th>Status</th>
                  <th>Agent / provider / model</th>
                  <th className="text-right">Spans</th>
                  <th className="text-right">Latency</th>
                  <th className="text-right">Tokens</th>
                  <th className="text-right">Cost</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((run) => (
                  <tr key={run.run_id} className="hover:bg-surface-sunken/60">
                    <td>
                      <Link to={`/runs/${encodeURIComponent(run.run_id)}`} className="block">
                        <span className="font-medium text-ink hover:text-accent">{run.task}</span>
                        <span className="mono ml-2 text-ink-faint">{shortId(run.run_id)}</span>
                      </Link>
                    </td>
                    <td>
                      <StatusBadge status={run.status} />
                    </td>
                    <td className="text-ink-muted">
                      <div>{run.agent_id || '—'}</div>
                      <div className="text-2xs text-ink-faint">
                        {run.provider || 'unknown provider'} · {run.model || 'unknown model'}
                      </div>
                    </td>
                    <td className="text-right font-mono text-ink-muted">{fmtNumber(run.span_count)}</td>
                    <td className="text-right font-mono text-ink-muted">{fmtMs(run.duration_ms)}</td>
                    <td className="text-right font-mono text-ink-muted">{fmtNumber(run.token_total)}</td>
                    <td className="text-right font-mono text-ink-muted">{fmtUsd(run.cost_usd)}</td>
                    <td className="text-ink-muted" title={run.started_at}>
                      {timeAgo(run.started_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="flex items-center justify-between gap-3 border-t border-line px-4 py-3 text-2xs text-ink-muted">
          <span>
            {data && data.total > 0
              ? `Showing ${offset + 1}–${Math.min(offset + data.items.length, data.total)} of ${data.total}`
              : 'No matching executions'}
          </span>
          <div className="flex gap-2">
            <button type="button" className="btn btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              ← Previous
            </button>
            <button
              type="button"
              className="btn btn-sm"
              disabled={!data || offset + data.items.length >= data.total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next →
            </button>
          </div>
        </div>
      </Card>
    </div>
  )
}
