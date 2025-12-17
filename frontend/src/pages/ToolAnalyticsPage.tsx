import { Link } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../lib/api'
import { fmtMs, fmtNumber, humanize, shortId } from '../lib/format'
import type { ToolAnalytics } from '../lib/types'
import { useAsync } from '../lib/useAsync'
import { Badge, Card, EmptyState, ErrorState, MetricCard, Skeleton } from '../components/ui'

const COLORS = ['#f48080', '#f0b35c', '#7ab0f5', '#6ed6b2', '#c39bf0', '#8fd67a']

export default function ToolAnalyticsPage() {
  const { data, error, loading, reload } = useAsync<ToolAnalytics>(() => api('/api/v1/tool-analytics'), [])

  if (error) return <ErrorState message={error} onRetry={reload} />
  if (loading && !data) return <Skeleton className="h-96" />
  if (!data) return null

  const chartData = data.tools.slice(0, 12).map((tool) => ({
    name: tool.tool.length > 16 ? `${tool.tool.slice(0, 16)}…` : tool.tool,
    failures: tool.errors + tool.timeouts,
    calls: tool.calls,
  }))

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">Tool analytics</div>
          <h1 className="mt-1 text-xl font-semibold tracking-tight">Tool failure analytics</h1>
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-ink-muted">
            Error types, repeated failures, affected tools, latency outliers and recovery success — aggregated from every
            recorded tool and retrieval action.
          </p>
        </div>
        <button type="button" className="btn" onClick={reload}>
          ↻ Refresh
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Tool calls" value={fmtNumber(data.total_tool_calls)} note="Recorded tool/retrieval actions" icon="⚒" />
        <MetricCard
          label="Failed calls"
          value={fmtNumber(data.total_failures)}
          note="Errors and timeouts"
          icon="!"
          tone={data.total_failures ? 'danger' : 'ok'}
        />
        <MetricCard
          label="Recovered by retry"
          value={fmtNumber(data.recovered_calls)}
          note="A later attempt of the same action succeeded"
          icon="↻"
          tone="ok"
        />
        <MetricCard
          label="Distinct tools"
          value={fmtNumber(data.tools.length)}
          note={`${Object.keys(data.error_types).length} error type(s)`}
          icon="⌁"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Failures per tool" subtitle="Errors and timeouts by tool name" bodyClassName="p-3">
          {chartData.length ? (
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 5" stroke="rgb(var(--line))" vertical={false} />
                  <XAxis dataKey="name" tick={{ fontSize: 10, fill: 'rgb(var(--ink-faint))' }} stroke="rgb(var(--line))" />
                  <YAxis tick={{ fontSize: 10, fill: 'rgb(var(--ink-faint))' }} stroke="rgb(var(--line))" width={36} allowDecimals={false} />
                  <Tooltip
                    contentStyle={{
                      background: 'rgb(var(--surface-raised))',
                      border: '1px solid rgb(var(--line))',
                      borderRadius: 8,
                      fontSize: 11,
                      color: 'rgb(var(--ink))',
                    }}
                  />
                  <Bar dataKey="failures" name="Failures" radius={[4, 4, 0, 0]}>
                    {chartData.map((_, index) => (
                      <Cell key={index} fill={COLORS[index % COLORS.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <EmptyState title="No tool activity yet" description="Import traces that record tool and retrieval actions." />
          )}
        </Card>

        <Card title="Error types" subtitle="Classified from the recorded exception text">
          {Object.keys(data.error_types).length ? (
            <ul className="space-y-2">
              {Object.entries(data.error_types).map(([type, count]) => (
                <li key={type} className="flex items-center justify-between gap-2 text-xs">
                  <span className="text-ink-muted">{humanize(type)}</span>
                  <span className="font-mono text-ink">{count}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-ink-muted">No tool errors recorded.</p>
          )}
        </Card>
      </div>

      <Card title="Per-tool breakdown" subtitle="Failure rate, latency and recovery" bodyClassName="p-0">
        {data.tools.length ? (
          <div className="overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>Tool</th>
                  <th className="text-right">Calls</th>
                  <th className="text-right">Errors</th>
                  <th className="text-right">Timeouts</th>
                  <th className="text-right">Recovered</th>
                  <th className="text-right">Failure rate</th>
                  <th className="text-right">Median</th>
                  <th className="text-right">p95</th>
                </tr>
              </thead>
              <tbody>
                {data.tools.map((tool) => (
                  <tr key={tool.tool}>
                    <td className="font-medium">{tool.tool}</td>
                    <td className="text-right font-mono text-ink-muted">{tool.calls}</td>
                    <td className="text-right font-mono text-danger">{tool.errors}</td>
                    <td className="text-right font-mono text-warn">{tool.timeouts}</td>
                    <td className="text-right font-mono text-ok">{tool.recovered}</td>
                    <td className="text-right font-mono">{(tool.failure_rate * 100).toFixed(1)}%</td>
                    <td className="text-right font-mono text-ink-muted">{fmtMs(tool.median_duration_ms)}</td>
                    <td className="text-right font-mono text-ink-muted">{fmtMs(tool.p95_duration_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="p-5">
            <EmptyState title="No tool statistics" description="No tool or retrieval actions were recorded in this workspace." />
          </div>
        )}
      </Card>

      {data.latency_outliers.length > 0 && (
        <Card title="Latency outliers" subtitle="Recorded actions slower than 30 seconds" bodyClassName="p-0">
          <div className="overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Tool</th>
                  <th>Status</th>
                  <th className="text-right">Duration</th>
                </tr>
              </thead>
              <tbody>
                {data.latency_outliers.map((outlier) => (
                  <tr key={`${outlier.run_id}-${outlier.span_id}`}>
                    <td>
                      <Link to={`/runs/${encodeURIComponent(outlier.run_id)}`} className="link mono">
                        {shortId(outlier.run_id)}
                      </Link>
                    </td>
                    <td>{outlier.tool}</td>
                    <td>
                      <Badge tone={outlier.status === 'ok' ? 'ok' : 'danger'}>{outlier.status}</Badge>
                    </td>
                    <td className="text-right font-mono text-ink-muted">{fmtMs(outlier.duration_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}
