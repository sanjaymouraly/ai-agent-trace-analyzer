import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import { api } from '../lib/api'
import { fmtMs, fmtNumber, fmtUsd, humanize, prettyJson, shortId } from '../lib/format'
import type { ComparisonResult, RunsResponse } from '../lib/types'
import { useAsync } from '../lib/useAsync'
import { Badge, Callout, Card, EmptyState, ErrorState, MetricCard, Skeleton, StatusBadge } from '../components/ui'

function DiffList({ differences }: { differences: Array<{ field: string; left: unknown; right: unknown; delta_ms?: number }> }) {
  if (!differences?.length) return null
  return (
    <ul className="space-y-1">
      {differences.map((diff, index) => (
        <li key={`${diff.field}-${index}`} className="text-2xs text-ink-muted">
          <span className="text-ink">{humanize(diff.field)}</span>:{' '}
          <code className="mono text-danger">{prettyJson(diff.left)}</code> →{' '}
          <code className="mono text-ok">{prettyJson(diff.right)}</code>
          {diff.delta_ms !== undefined && <span className="ml-1 text-ink-faint">({diff.delta_ms > 0 ? '+' : ''}{diff.delta_ms} ms)</span>}
        </li>
      ))}
    </ul>
  )
}

export default function ComparePage() {
  const [baseline, setBaseline] = useState('')
  const [candidate, setCandidate] = useState('')
  const [result, setResult] = useState<ComparisonResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const runs = useAsync<RunsResponse>(() => api('/api/v1/runs', { query: { limit: 200 } }), [])

  useEffect(() => {
    if (runs.data?.items.length && !baseline) {
      const items = runs.data.items
      // Prefer a task that has both a successful and a failed run: that is the
      // "same task, different outcome" comparison an investigator wants first.
      const byTask = new Map<string, typeof items>()
      items.forEach((run) => {
        const key = run.task_id || run.task
        byTask.set(key, [...(byTask.get(key) ?? []), run])
      })
      const mixed = [...byTask.values()].find(
        (group) => group.some((run) => run.status === 'success') && group.some((run) => run.status === 'failure'),
      )
      const baselineRun =
        mixed?.find((run) => run.status === 'success') ?? items.find((run) => run.status === 'success') ?? items[0]
      const candidateRun =
        mixed?.find((run) => run.status === 'failure' && run.run_id !== baselineRun.run_id) ??
        items.find((run) => run.status === 'failure' && run.run_id !== baselineRun.run_id) ??
        items.find((run) => run.run_id !== baselineRun.run_id) ??
        items[0]
      setBaseline(baselineRun.run_id)
      setCandidate(candidateRun.run_id)
    }
  }, [runs.data, baseline])

  const options = useMemo(
    () =>
      (runs.data?.items ?? []).map((run) => (
        <option key={run.run_id} value={run.run_id}>
          {run.task} · {run.status} · {shortId(run.run_id)}
        </option>
      )),
    [runs.data],
  )

  const compare = async () => {
    if (!baseline || !candidate || baseline === candidate) {
      setError('Choose two different executions.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const response = await api<ComparisonResult>('/api/v1/compare', {
        method: 'POST',
        body: { run_ids: [baseline, candidate] },
      })
      setResult(response)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  if (runs.error) return <ErrorState message={runs.error} onRetry={runs.reload} />

  const pair = result?.pairwise?.[0]

  return (
    <div className="space-y-5">
      <div>
        <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">Trajectory analysis</div>
        <h1 className="mt-1 text-xl font-semibold tracking-tight">Compare executions</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-ink-muted">
          Deterministic structural alignment of recorded actions, arguments, outcomes and cost. Different action orderings
          are not labelled incorrect without dependency or outcome evidence.
        </p>
      </div>

      <Card bodyClassName="p-4">
        <div className="grid gap-3 md:grid-cols-2">
          <label className="flex flex-col gap-1 text-2xs font-medium text-ink-muted">
            Baseline execution
            <select
              id="compare-baseline"
              className="field"
              value={baseline}
              onChange={(event) => setBaseline(event.target.value)}
            >
              <option value="">Select a run…</option>
              {options}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-2xs font-medium text-ink-muted">
            Compare against
            <select
              id="compare-candidate"
              className="field"
              value={candidate}
              onChange={(event) => setCandidate(event.target.value)}
            >
              <option value="">Select a run…</option>
              {options}
            </select>
          </label>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => void compare()}
            disabled={busy || !baseline || !candidate || baseline === candidate}
          >
            {busy ? 'Aligning…' : '⇄ Align trajectories'}
          </button>
          <span className="text-2xs text-ink-faint">
            The baseline is an alignment anchor, not a gold path. Task equivalence is not assumed.
          </span>
        </div>
        {error && <p className="mt-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-2xs text-danger">{error}</p>}
      </Card>

      {runs.loading && !runs.data && <Skeleton className="h-64" />}

      {result && pair && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard label="Baseline status" value={<StatusBadge status={pair.metrics.left.status} />} note={pair.metrics.left.task} icon="◫" />
            <MetricCard label="Compared status" value={<StatusBadge status={pair.metrics.right.status} />} note={pair.metrics.right.task} icon="⇄" />
            <MetricCard
              label="Latency delta"
              value={
                pair.metrics.duration_delta_ms === null
                  ? '—'
                  : `${pair.metrics.duration_delta_ms > 0 ? '+' : ''}${fmtMs(pair.metrics.duration_delta_ms)}`
              }
              note="Compared − baseline"
              icon="◷"
            />
            <MetricCard
              label="Token delta"
              value={pair.metrics.token_delta === null ? '—' : `${pair.metrics.token_delta > 0 ? '+' : ''}${fmtNumber(pair.metrics.token_delta)}`}
              note={`Cost delta ${fmtUsd(pair.metrics.cost_delta_usd)}`}
              icon="✧"
            />
          </div>

          {pair.first_divergence ? (
            <div className="rounded-xl border border-warn/40 bg-warn/5 px-5 py-4">
              <div className="text-2xs font-semibold uppercase tracking-wider text-warn">
                First observed divergence · {pair.first_divergence.classification || pair.first_divergence.kind}
              </div>
              <p className="mt-1 text-sm text-ink">{pair.first_divergence.note}</p>
              <div className="mono mt-1.5 text-2xs text-ink-muted">
                baseline {shortId(pair.first_divergence.left_span_id)} · compared {shortId(pair.first_divergence.right_span_id)}
              </div>
            </div>
          ) : (
            <Callout tone="ok" title="No structural divergence found">
              Exact action identity and recorded values matched across the alignment. This does not guarantee an equivalent
              outcome.
            </Callout>
          )}

          <Card
            title="Aligned actions"
            subtitle={`${pair.aligned_actions.length} rows · retry occurrences preserved · ${pair.alignment_method}`}
            bodyClassName="p-0"
            actions={<Badge tone={pair.topology_changed ? 'warn' : 'muted'}>{pair.topology_changed ? 'Hierarchy differs' : 'Topology checked'}</Badge>}
          >
            <div className="overflow-x-auto">
              <table className="table">
                <thead>
                  <tr>
                    <th>Baseline</th>
                    <th />
                    <th>Compared</th>
                    <th>Differences</th>
                  </tr>
                </thead>
                <tbody>
                  {pair.aligned_actions.map((action, index) => {
                    const left = action.left
                    const right = action.right
                    return (
                      <tr key={`${action.left_span_id ?? 'x'}-${action.right_span_id ?? 'y'}-${index}`} className="align-top">
                        <td>
                          {left ? (
                            <>
                              <Link
                                to={`/runs/${encodeURIComponent(action.left_run_id)}`}
                                className="font-medium text-ink hover:text-accent"
                              >
                                {left.name}
                              </Link>
                              <div className="text-2xs text-ink-faint">
                                {humanize(left.action_type)} · {left.status} · {fmtMs(left.duration_ms ?? null)}
                              </div>
                              <div className="mono text-2xs text-ink-faint">{shortId(left.span_id)}</div>
                            </>
                          ) : (
                            <span className="text-2xs text-ink-faint">— action absent —</span>
                          )}
                        </td>
                        <td className="text-center">
                          <span
                            className={clsx(
                              'inline-flex h-6 w-6 items-center justify-center rounded-full border text-xs',
                              action.kind === 'matched'
                                ? 'border-ok/40 text-ok'
                                : action.kind === 'changed'
                                  ? 'border-warn/50 text-warn'
                                  : action.kind === 'added_in_right'
                                    ? 'border-info/50 text-info'
                                    : 'border-danger/50 text-danger',
                            )}
                            aria-label={action.kind}
                          >
                            {action.kind === 'matched' ? '↔' : action.kind === 'changed' ? '≠' : action.kind === 'added_in_right' ? '+' : '−'}
                          </span>
                        </td>
                        <td>
                          {right ? (
                            <>
                              <Link
                                to={`/runs/${encodeURIComponent(action.right_run_id)}`}
                                className="font-medium text-ink hover:text-accent"
                              >
                                {right.name}
                              </Link>
                              <div className="text-2xs text-ink-faint">
                                {humanize(right.action_type)} · {right.status} · {fmtMs(right.duration_ms ?? null)}
                              </div>
                              <div className="mono text-2xs text-ink-faint">{shortId(right.span_id)}</div>
                            </>
                          ) : (
                            <span className="text-2xs text-ink-faint">— action absent —</span>
                          )}
                        </td>
                        <td className="max-w-md">
                          <DiffList differences={action.differences} />
                          {!action.differences?.length && <span className="text-2xs text-ink-faint">identical</span>}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <div className="border-t border-line px-5 py-3 text-2xs text-ink-muted">{pair.divergence_note}</div>
          </Card>
        </>
      )}

      {!result && !runs.loading && (
        <EmptyState
          icon="⇄"
          title="Choose two executions"
          description="Select a baseline and a comparison run, then align their recorded action sequences."
        />
      )}
    </div>
  )
}
