import { Link } from 'react-router-dom'
import {
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from '../lib/api'
import { fmtMs, fmtNumber, fmtUsd, humanize, shortId, timeAgo } from '../lib/format'
import type { Overview as OverviewData, RunSummary } from '../lib/types'
import { useAsync } from '../lib/useAsync'
import { Card, EmptyState, ErrorState, MetricCard, Skeleton, StatusBadge } from '../components/ui'

const CATEGORY_COLORS = [
  '#6ed6b2',
  '#f0b35c',
  '#f48080',
  '#7ab0f5',
  '#c39bf0',
  '#8fd67a',
  '#e58fc7',
  '#93a4b8',
]

function RecentRunsTable({ runs }: { runs: RunSummary[] }) {
  if (!runs.length) {
    return (
      <EmptyState
        icon="≋"
        title="No executions yet"
        description="Import a JSON or JSONL trace, or load the deterministic example workflows to explore the debugger."
      />
    )
  }
  return (
    <div className="overflow-x-auto">
      <table className="table">
        <thead>
          <tr>
            <th>Execution</th>
            <th>Status</th>
            <th>Agent / provider</th>
            <th className="text-right">Latency</th>
            <th className="text-right">Tokens</th>
            <th className="text-right">Cost</th>
            <th>Started</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
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
                {run.agent_id || '—'}
                {run.provider && <span className="chip ml-2">{run.provider.split(',')[0]}</span>}
              </td>
              <td className="text-right font-mono text-ink-muted">{fmtMs(run.duration_ms)}</td>
              <td className="text-right font-mono text-ink-muted">{fmtNumber(run.token_total)}</td>
              <td className="text-right font-mono text-ink-muted">{fmtUsd(run.cost_usd)}</td>
              <td className="text-ink-muted">{timeAgo(run.started_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function OverviewPage() {
  const { data, error, loading, reload } = useAsync<OverviewData>(() => api('/api/v1/overview'), [])

  if (error) return <ErrorState message={error} onRetry={reload} />
  if (loading && !data) {
    return (
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {[0, 1, 2, 3].map((index) => (
            <Skeleton key={index} className="h-28" />
          ))}
        </div>
        <Skeleton className="h-72" />
      </div>
    )
  }
  if (!data) return null

  const categories = Object.entries(data.failure_categories).sort((a, b) => b[1] - a[1])
  const chartData = data.latency_series.slice(0, 20).reverse()
  const tokenData = data.token_series.slice(0, 20).reverse()

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">Observability · local</div>
          <h1 className="mt-1 text-xl font-semibold tracking-tight">Execution overview</h1>
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-ink-muted">
            Trace-backed visibility across agent tasks, tools and model calls. Every number is derived from imported
            execution records.
          </p>
        </div>
        <div className="flex gap-2">
          <button type="button" className="btn" onClick={reload}>
            ↻ Refresh
          </button>
          <Link to="/runs" className="btn btn-primary">
            Open trace explorer
          </Link>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <MetricCard label="Total executions" value={fmtNumber(data.total_runs)} note={`${data.failure_categories_runs_analyzed} analysed`} icon="◫" />
        <MetricCard
          label="Success rate"
          value={data.success_rate === null ? '—' : `${data.success_rate.toFixed(1)}%`}
          note={`${data.successful_runs} succeeded · ${data.failed_runs} failed`}
          icon="↗"
          tone={data.success_rate === null ? 'muted' : data.success_rate >= 70 ? 'ok' : 'warn'}
        />
        <MetricCard
          label="Avg. latency"
          value={fmtMs(data.avg_duration_ms)}
          note={`p95 ${fmtMs(data.p95_duration_ms)}`}
          icon="◷"
        />
        <MetricCard label="Token volume" value={fmtNumber(data.total_tokens)} note="Recorded usage" icon="✧" />
        <MetricCard label="Estimated cost" value={fmtUsd(data.total_cost_usd)} note="Trace-recorded cost" icon="¤" />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card
          className="xl:col-span-2"
          title="Latency · recent executions"
          subtitle="Recorded duration per run, oldest to newest"
          bodyClassName="p-3"
        >
          {chartData.length ? (
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 5" stroke="rgb(var(--line))" vertical={false} />
                  <XAxis
                    dataKey="task"
                    tick={{ fontSize: 10, fill: 'rgb(var(--ink-faint))' }}
                    stroke="rgb(var(--line))"
                    tickFormatter={(value: string) => (value.length > 16 ? `${value.slice(0, 16)}…` : value)}
                  />
                  <YAxis
                    tick={{ fontSize: 10, fill: 'rgb(var(--ink-faint))' }}
                    stroke="rgb(var(--line))"
                    width={56}
                    tickFormatter={(value: number) => fmtMs(value)}
                  />
                  <Tooltip
                    contentStyle={{
                      background: 'rgb(var(--surface-raised))',
                      border: '1px solid rgb(var(--line))',
                      borderRadius: 8,
                      fontSize: 11,
                      color: 'rgb(var(--ink))',
                    }}
                    formatter={(value) => [fmtMs(Number(value)), 'duration']}
                    labelFormatter={(label) => String(label)}
                  />
                  <Legend wrapperStyle={{ fontSize: 11, color: 'rgb(var(--ink-muted))' }} />
                  <Line
                    type="monotone"
                    dataKey="duration_ms"
                    name="Duration"
                    stroke="rgb(var(--accent))"
                    strokeWidth={2}
                    dot={{ r: 3, fill: 'rgb(var(--accent))' }}
                    activeDot={{ r: 5 }}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <EmptyState title="No duration samples yet" description="Import a trace to populate the latency trend." />
          )}
        </Card>

        <Card title="Failure categories" subtitle="Rule-classified findings across analysed runs">
          {categories.length ? (
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={categories.map(([name, value]) => ({ name: humanize(name), value }))}
                    dataKey="value"
                    nameKey="name"
                    innerRadius={48}
                    outerRadius={80}
                    paddingAngle={2}
                    stroke="rgb(var(--surface-raised))"
                  >
                    {categories.map((_, index) => (
                      <Cell key={index} fill={CATEGORY_COLORS[index % CATEGORY_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{
                      background: 'rgb(var(--surface-raised))',
                      border: '1px solid rgb(var(--line))',
                      borderRadius: 8,
                      fontSize: 11,
                      color: 'rgb(var(--ink))',
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: 10, color: 'rgb(var(--ink-muted))' }} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <EmptyState
              title="No recorded failure signals"
              description="A successful tool response is not automatically treated as correct, and no rule matched these runs."
            />
          )}
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card
          className="xl:col-span-2"
          title="Recent executions"
          subtitle="Newest trace records — select a run to investigate"
          bodyClassName="p-0"
          actions={
            <Link to="/runs" className="btn btn-sm">
              Open explorer →
            </Link>
          }
        >
          <RecentRunsTable runs={data.recent_runs} />
        </Card>

        <div className="space-y-4">
          <Card title="Token usage" subtitle="Recorded tokens per recent run" bodyClassName="p-3">
            {tokenData.length ? (
              <div className="h-48 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={tokenData} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 5" stroke="rgb(var(--line))" vertical={false} />
                    <XAxis dataKey="run_id" tick={{ fontSize: 9, fill: 'rgb(var(--ink-faint))' }} stroke="rgb(var(--line))" tickFormatter={(v: string) => shortId(v)} />
                    <YAxis tick={{ fontSize: 10, fill: 'rgb(var(--ink-faint))' }} stroke="rgb(var(--line))" width={48} />
                    <Tooltip
                      contentStyle={{
                        background: 'rgb(var(--surface-raised))',
                        border: '1px solid rgb(var(--line))',
                        borderRadius: 8,
                        fontSize: 11,
                        color: 'rgb(var(--ink))',
                      }}
                    />
                    <Line type="monotone" dataKey="tokens" stroke="rgb(var(--info))" strokeWidth={2} dot={{ r: 2.5 }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <EmptyState title="No token data" description="Import traces that record model token usage." />
            )}
          </Card>

          <Card title="Providers observed" subtitle="Distinct model providers across stored runs">
            {Object.keys(data.provider_counts).length ? (
              <ul className="space-y-2">
                {Object.entries(data.provider_counts).map(([provider, count]) => (
                  <li key={provider} className="flex items-center justify-between text-xs">
                    <span className="truncate text-ink-muted">{provider}</span>
                    <span className="font-mono text-ink">{count}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-ink-muted">No provider metadata recorded yet.</p>
            )}
          </Card>
        </div>
      </div>

      <div className="rounded-xl border border-line bg-surface-raised px-5 py-4 text-xs leading-relaxed text-ink-muted">
        <strong className="text-ink">Evidence over explanation.</strong> Observed failures are separated from deterministic
        hypotheses. The dashboard never claims causality from timestamps, tool success, or model narration alone.
      </div>
    </div>
  )
}
