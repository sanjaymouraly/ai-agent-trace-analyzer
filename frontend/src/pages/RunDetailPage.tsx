import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import clsx from 'clsx'
import { api, downloadFile } from '../lib/api'
import { fmtMs, fmtNumber, fmtUsd, humanize, prettyJson, shortId, timeAgo } from '../lib/format'
import type { Analysis, ExperimentRecord, RunDetail, Span } from '../lib/types'
import { useAsync } from '../lib/useAsync'
import { useToast } from '../components/Toast'
import {
  Badge,
  Callout,
  Card,
  EmptyState,
  ErrorState,
  EvidenceBadge,
  JsonBlock,
  MetricCard,
  Skeleton,
  StatusBadge,
} from '../components/ui'
import DependencyGraphView from '../components/graph/DependencyGraph'
import ExecutionTimeline from '../components/timeline/ExecutionTimeline'

type Tab = 'overview' | 'timeline' | 'graph' | 'rootcause' | 'findings' | 'review'

const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'overview', label: 'Overview' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'graph', label: 'Dependency graph' },
  { id: 'rootcause', label: 'Root-cause workbench' },
  { id: 'findings', label: 'Findings' },
  { id: 'review', label: 'Review & experiments' },
]

function SpanInspector({ span, onClose }: { span: Span; onClose: () => void }) {
  return (
    <div className="rounded-lg border border-line bg-surface-sunken p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold">{span.name}</span>
            <StatusBadge status={span.status} />
            {span.retry_attempt ? <Badge tone="warn">attempt {span.retry_attempt}</Badge> : null}
          </div>
          <div className="mono mt-1 text-ink-faint">
            {span.span_id} · {span.tool_name || humanize(span.action_type)} ·{' '}
            {span.duration_ms === null || span.duration_ms === undefined ? 'duration unavailable' : fmtMs(span.duration_ms)}
          </div>
        </div>
        <button type="button" className="btn btn-sm" onClick={onClose}>
          Close
        </button>
      </div>
      {span.exception && (
        <div className="mt-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-2xs text-danger">
          {span.exception}
        </div>
      )}
      <div className="mt-3 grid gap-3 lg:grid-cols-3">
        <div>
          <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-ink-faint">Inputs</div>
          <pre className="json-block">{prettyJson(span.inputs ?? null)}</pre>
        </div>
        <div>
          <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-ink-faint">Outputs</div>
          <pre className="json-block">{prettyJson(span.outputs ?? null)}</pre>
        </div>
        <div>
          <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-ink-faint">Attributes</div>
          <pre className="json-block">{prettyJson(span.attributes ?? {})}</pre>
        </div>
      </div>
    </div>
  )
}

function HypothesisCard({ hypothesis, onSelectSpan }: { hypothesis: Analysis['root_cause_hypotheses'][number]; onSelectSpan: (id: string) => void }) {
  return (
    <article className="rounded-lg border border-line bg-surface-raised p-4">
      <div className="flex flex-wrap items-center gap-2">
        <EvidenceBadge status={hypothesis.status} />
        <span className="text-2xs text-ink-muted">
          evidence strength {Math.round(hypothesis.confidence * 100)}%
        </span>
        <span className="chip ml-auto">{humanize(hypothesis.category)}</span>
      </div>
      <h3 className="mt-2 text-sm font-semibold">{hypothesis.title}</h3>
      {hypothesis.span_ids?.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {hypothesis.span_ids.map((spanId) => (
            <button
              key={spanId}
              type="button"
              onClick={() => onSelectSpan(spanId)}
              className="mono rounded border border-line bg-surface px-1.5 py-0.5 text-ink-faint transition hover:border-accent hover:text-accent"
              title={`Inspect span ${spanId}`}
            >
              {shortId(spanId)}
            </button>
          ))}
        </div>
      )}
      <div className="mt-3 space-y-2 text-2xs leading-relaxed">
        <div>
          <strong className="text-ink">Supporting evidence</strong>
          <ul className="mt-1 space-y-1 text-ink-muted">
            {hypothesis.supporting_evidence?.length ? (
              hypothesis.supporting_evidence.map((item, index) => <li key={index}>+ {item}</li>)
            ) : (
              <li>None recorded.</li>
            )}
          </ul>
        </div>
        {hypothesis.contradicting_evidence?.length > 0 && (
          <div>
            <strong className="text-warn">Contradicting evidence</strong>
            <ul className="mt-1 space-y-1 text-ink-muted">
              {hypothesis.contradicting_evidence.map((item, index) => (
                <li key={index}>− {item}</li>
              ))}
            </ul>
          </div>
        )}
        {hypothesis.missing_information && hypothesis.missing_information.length > 0 && (
          <div>
            <strong className="text-info">Missing information</strong>
            <ul className="mt-1 space-y-1 text-ink-muted">
              {hypothesis.missing_information.map((item, index) => (
                <li key={index}>? {item}</li>
              ))}
            </ul>
          </div>
        )}
        {hypothesis.limitations?.length > 0 && (
          <div>
            <strong className="text-ink-muted">Causal limitations</strong>
            <ul className="mt-1 space-y-1 text-ink-faint">
              {hypothesis.limitations.map((item, index) => (
                <li key={index}>— {item}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
      {hypothesis.suggested_experiment && (
        <div className="mt-3 rounded-md border border-line bg-surface-sunken px-3 py-2 text-2xs text-ink-muted">
          <strong className="text-ink">Suggested verification</strong>
          <div>{hypothesis.suggested_experiment}</div>
        </div>
      )}
    </article>
  )
}

function ExperimentCard({ experiment }: { experiment: ExperimentRecord }) {
  const result = experiment.result
  return (
    <div className="rounded-lg border border-line bg-surface-raised p-4">
      <div className="flex flex-wrap items-center gap-2">
        <EvidenceBadge status={experiment.status} />
        <span className="text-xs font-medium">{experiment.title}</span>
        <span className="ml-auto text-2xs text-ink-faint">{timeAgo(experiment.created_at)}</span>
      </div>
      {result?.scope && <p className="mt-1.5 text-2xs leading-relaxed text-ink-muted">{result.scope}</p>}
      {result?.steps?.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {result.steps.map((step, index) => (
            <li key={`${step.span_id}-${index}`} className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-2xs">
              <div className="flex flex-wrap items-center gap-2">
                <span className="mono text-ink-faint">{shortId(step.span_id)}</span>
                <span className="text-ink">{step.action}</span>
                <Badge tone={step.outcome === 'passed' ? 'ok' : step.outcome === 'failed' ? 'danger' : 'muted'}>
                  {step.outcome}
                </Badge>
                {step.adapter && <span className="chip">{step.adapter}</span>}
              </div>
              {step.reason && <div className="mt-1 text-ink-muted">{step.reason}</div>}
              {step.details !== undefined && (
                <pre className="mt-1 overflow-x-auto text-ink-faint">{prettyJson(step.details)}</pre>
              )}
            </li>
          ))}
        </ul>
      )}
      {result?.limitations?.length > 0 && (
        <ul className="mt-3 space-y-1 text-2xs text-ink-faint">
          {result.limitations.map((item, index) => (
            <li key={index}>— {item}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function RunDetailPage() {
  const { runId = '' } = useParams()
  const decodedRunId = decodeURIComponent(runId)
  const [tab, setTab] = useState<Tab>('overview')
  const [selectedSpanId, setSelectedSpanId] = useState<string | null>(null)
  const { push } = useToast()

  const detail = useAsync<RunDetail>(() => api(`/api/v1/runs/${encodeURIComponent(decodedRunId)}`), [decodedRunId])
  const analysis = useAsync<Analysis>(
    () => api(`/api/v1/runs/${encodeURIComponent(decodedRunId)}/analysis`),
    [decodedRunId],
  )

  const spans = useMemo(() => detail.data?.trace?.spans ?? [], [detail.data])
  const selectedSpan = useMemo(
    () => spans.find((span) => span.span_id === selectedSpanId) ?? null,
    [spans, selectedSpanId],
  )

  if (detail.error) return <ErrorState message={detail.error} onRetry={detail.reload} />
  if (detail.loading && !detail.data) return <Skeleton className="h-96" />
  if (!detail.data) return null

  const run = detail.data
  const result = analysis.data
  const earliest = result?.earliest_observed_error
  const propagation = result?.earliest_plausible_failure_propagation_point

  const exportReport = async (format: 'markdown' | 'json' | 'html' | 'pdf') => {
    try {
      await downloadFile(
        `/api/v1/runs/${encodeURIComponent(run.run_id)}/report?format=${format}`,
        `tracelens-${run.run_id}.${format === 'markdown' ? 'md' : format}`,
      )
      push(`Exported ${format.toUpperCase()} investigation report.`)
    } catch (error) {
      push(error instanceof Error ? error.message : 'Export failed', 'error')
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to="/runs" className="text-2xs text-ink-muted hover:text-accent">
            ← Back to trace explorer
          </Link>
          <h1 className="mt-1 truncate text-xl font-semibold tracking-tight">{run.task}</h1>
          <div className="mono mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-ink-faint">
            <span>{run.run_id}</span>
            <span>trace {run.trace_id}</span>
            <span>{timeAgo(run.started_at)}</span>
            {run.source_format && <span className="chip">source: {run.source_format}</span>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn btn-sm" onClick={() => void exportReport('markdown')}>
            ⇩ Markdown
          </button>
          <button type="button" className="btn btn-sm" onClick={() => void exportReport('html')}>
            HTML
          </button>
          <button type="button" className="btn btn-sm" onClick={() => void exportReport('pdf')}>
            PDF
          </button>
          <button type="button" className="btn btn-sm" onClick={() => void exportReport('json')}>
            JSON
          </button>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <MetricCard label="Run status" value={<StatusBadge status={run.status} />} note={run.evaluation_results?.passed === false ? 'evaluation failed' : 'recorded outcome'} icon="◫" />
        <MetricCard label="Spans" value={fmtNumber(run.span_count)} note={`${run.environment?.environment || 'unknown environment'}`} icon="⌗" />
        <MetricCard label="Duration" value={fmtMs(run.duration_ms)} note={`started ${run.started_at?.slice(0, 19).replace('T', ' ') || '—'}`} icon="◷" />
        <MetricCard label="Tokens" value={fmtNumber(run.token_total)} note={run.model || 'model not recorded'} icon="✧" />
        <MetricCard label="Recorded cost" value={fmtUsd(run.cost_usd)} note={run.provider || 'provider not recorded'} icon="¤" />
      </div>

      {earliest && (
        <Callout tone="danger" title="Earliest observed error">
          <span className="font-medium">{humanize(earliest.category)}</span> at span{' '}
          <button type="button" className="link mono" onClick={() => setSelectedSpanId(earliest.span_id ?? null)}>
            {shortId(earliest.span_id)}
          </button>{' '}
          ({earliest.timestamp ? new Date(earliest.timestamp).toLocaleTimeString() : 'run level'}). This marks the first
          explicitly recorded anomaly, not necessarily the cause of the outcome.
        </Callout>
      )}
      {propagation && (
        <Callout tone="warn" title="Earliest plausible failure-propagation point">
          Dependency-linked downstream symptoms begin at{' '}
          <span className="mono">{shortId(propagation.span_id)}</span>
          {propagation.affected_span_ids.length > 0 && (
            <>
              {' '}
              and affect {propagation.affected_span_ids.map((id) => shortId(id)).join(', ')}
            </>
          )}
          . {propagation.note}
        </Callout>
      )}
      {result?.earliest_observed_divergence && (
        <Callout tone="info" title="First observed divergence from the reference trajectory">
          {result.earliest_observed_divergence.note || result.earliest_observed_divergence.classification}
        </Callout>
      )}

      <div className="flex flex-wrap gap-1 border-b border-line" role="tablist" aria-label="Investigation views">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            onClick={() => setTab(item.id)}
            className={clsx(
              '-mb-px border-b-2 px-3 py-2 text-xs font-medium transition',
              tab === item.id
                ? 'border-accent text-accent'
                : 'border-transparent text-ink-muted hover:text-ink',
            )}
          >
            {item.label}
            {item.id === 'findings' && result?.findings?.length ? (
              <span className="ml-1.5 chip">{result.findings.length}</span>
            ) : null}
          </button>
        ))}
      </div>

      {tab === 'overview' && (
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="space-y-4 lg:col-span-2">
            <Card title="Execution summary" subtitle={result?.summary || 'Analysis pending…'}>
              {result?.missing_information?.length ? (
                <div className="mt-3">
                  <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">
                    Missing information to strengthen a conclusion
                  </div>
                  <ul className="mt-1.5 space-y-1 text-xs text-ink-muted">
                    {result.missing_information.map((item, index) => (
                      <li key={index}>• {item}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {result?.suggested_experiments?.length ? (
                <div className="mt-4">
                  <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">
                    Suggested controlled experiments
                  </div>
                  <ul className="mt-1.5 space-y-2">
                    {result.suggested_experiments.map((experiment, index) => (
                      <li key={index} className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-2xs text-ink-muted">
                        <strong className="text-ink">{experiment.title}</strong>
                        <div>{experiment.proposal}</div>
                        <div className="mt-1 text-ink-faint">{experiment.safety}</div>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </Card>

            <Card title="Actions" subtitle="Every recorded action in this run" bodyClassName="p-0">
              <div className="max-h-96 overflow-y-auto">
                <table className="table">
                  <thead className="sticky top-0 bg-surface-raised">
                    <tr>
                      <th>Action</th>
                      <th>Type</th>
                      <th>Status</th>
                      <th className="text-right">Duration</th>
                      <th>Span</th>
                    </tr>
                  </thead>
                  <tbody>
                    {spans.map((span) => (
                      <tr
                        key={span.span_id}
                        className="cursor-pointer hover:bg-surface-sunken/60"
                        onClick={() => setSelectedSpanId(span.span_id)}
                      >
                        <td>
                          <div className="font-medium text-ink">{span.name}</div>
                          {span.tool_name && <div className="text-2xs text-ink-faint">{span.tool_name}</div>}
                        </td>
                        <td className="text-ink-muted">{humanize(span.action_type)}</td>
                        <td>
                          <StatusBadge status={span.status} />
                        </td>
                        <td className="text-right font-mono text-ink-muted">{fmtMs(span.duration_ms ?? null)}</td>
                        <td className="mono text-ink-faint">{shortId(span.span_id)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>

          <div className="space-y-4">
            <Card title="Root-cause hypotheses" subtitle="Ranked by evidence strength — not causal proof">
              {result?.root_cause_hypotheses?.length ? (
                <div className="space-y-3">
                  {result.root_cause_hypotheses.slice(0, 3).map((hypothesis) => (
                    <HypothesisCard key={hypothesis.hypothesis_id} hypothesis={hypothesis} onSelectSpan={setSelectedSpanId} />
                  ))}
                  <button type="button" className="btn btn-sm w-full" onClick={() => setTab('rootcause')}>
                    Open the workbench →
                  </button>
                </div>
              ) : (
                <EmptyState
                  icon="✓"
                  title="No root-cause hypothesis"
                  description="The run succeeded, or the observed evidence does not support a candidate. This is an intentional abstention."
                />
              )}
            </Card>

            {run.import_warnings?.length ? (
              <Card title="Import warnings" subtitle="Mapping decisions recorded during ingestion">
                <ul className="space-y-1 text-2xs text-ink-muted">
                  {run.import_warnings.map((warning, index) => (
                    <li key={index}>• {warning}</li>
                  ))}
                </ul>
              </Card>
            ) : null}
          </div>
        </div>
      )}

      {tab === 'timeline' && (
        <div className="space-y-4">
          <Card
            title="Execution timeline"
            subtitle="Recorded span windows · overlapping bars indicate concurrency"
          >
            <ExecutionTimeline
              spans={spans}
              selectedSpanId={selectedSpanId}
              onSelect={setSelectedSpanId}
              criticalPath={result?.graph?.critical_path?.span_ids ?? []}
            />
          </Card>
          {selectedSpan && <SpanInspector span={selectedSpan} onClose={() => setSelectedSpanId(null)} />}
        </div>
      )}

      {tab === 'graph' && (
        <div className="space-y-4">
          <Card
            title="Dependency reconstruction"
            subtitle="Declared dependencies and tool-call references drive impact analysis"
          >
            {result ? (
              <DependencyGraphView graph={result.graph} selectedSpanId={selectedSpanId} onSelect={setSelectedSpanId} />
            ) : (
              <Skeleton className="h-96" />
            )}
          </Card>
          {selectedSpan && <SpanInspector span={selectedSpan} onClose={() => setSelectedSpanId(null)} />}
        </div>
      )}

      {tab === 'rootcause' && (
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="space-y-4 lg:col-span-2">
            {result?.root_cause_hypotheses?.length ? (
              result.root_cause_hypotheses.map((hypothesis) => (
                <HypothesisCard key={hypothesis.hypothesis_id} hypothesis={hypothesis} onSelectSpan={setSelectedSpanId} />
              ))
            ) : (
              <EmptyState
                icon="⌘"
                title="No root-cause hypothesis for this run"
                description="Either the run succeeded, or the available evidence does not localize a cause. TraceLens abstains rather than guessing."
              />
            )}
            {result?.evidence_policy && <Callout tone="info" title="Evidence policy">{result.evidence_policy}</Callout>}
          </div>
          <div className="space-y-4">
            <Card title="Evidence ledger" subtitle="How each conclusion is labelled">
              <ul className="space-y-2 text-2xs text-ink-muted">
                <li>
                  <EvidenceBadge status="Observed" /> <span className="ml-1">present in the imported record.</span>
                </li>
                <li>
                  <EvidenceBadge status="Inferred Hypothesis" /> <span className="ml-1">deterministic heuristic interpretation.</span>
                </li>
                <li>
                  <EvidenceBadge status="Experimentally Supported" />{' '}
                  <span className="ml-1">a controlled local test was executed and passed.</span>
                </li>
                <li>
                  <EvidenceBadge status="Experimentally Refuted" />{' '}
                  <span className="ml-1">a controlled local test was executed and failed.</span>
                </li>
                <li>
                  <EvidenceBadge status="Confirmed Under Specified Test Conditions" />{' '}
                  <span className="ml-1">confirmed for the recorded fixture and sandbox version only.</span>
                </li>
              </ul>
            </Card>
            <Card title="Impact analysis" subtitle="Forward impact from each observed failure">
              {result && Object.keys(result.impact).length ? (
                <ul className="space-y-2">
                  {Object.entries(result.impact).map(([spanId, affected]) => (
                    <li key={spanId} className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-2xs">
                      <button type="button" className="link mono" onClick={() => setSelectedSpanId(spanId)}>
                        {shortId(spanId)}
                      </button>
                      <div className="mt-1 text-ink-muted">affects {affected.length} downstream action(s)</div>
                      <div className="mono mt-0.5 text-ink-faint">{affected.map((id) => shortId(id)).join(', ')}</div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-ink-muted">No dependency-linked downstream impact was detected.</p>
              )}
            </Card>
          </div>
        </div>
      )}

      {tab === 'findings' && (
        <Card
          title="Rule-based findings"
          subtitle={`${result?.findings?.length ?? 0} finding(s) · rule set ${result?.rules_version ?? '—'}`}
          bodyClassName="p-0"
        >
          {result?.findings?.length ? (
            <div className="overflow-x-auto">
              <table className="table">
                <thead>
                  <tr>
                    <th>Category</th>
                    <th>Label</th>
                    <th>Rule</th>
                    <th>Spans</th>
                    <th className="text-right">Confidence</th>
                    <th>Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {result.findings.map((finding) => (
                    <tr key={finding.finding_id}>
                      <td className="font-medium">{humanize(finding.category)}</td>
                      <td>
                        <EvidenceBadge status={finding.status} />
                      </td>
                      <td className="mono text-ink-faint">{finding.rule_id}</td>
                      <td>
                        <div className="flex flex-wrap gap-1">
                          {finding.span_ids.map((spanId) => (
                            <button
                              key={spanId}
                              type="button"
                              className="mono rounded border border-line bg-surface px-1.5 py-0.5 text-ink-faint hover:border-accent hover:text-accent"
                              onClick={() => setSelectedSpanId(spanId)}
                            >
                              {shortId(spanId)}
                            </button>
                          ))}
                        </div>
                      </td>
                      <td className="text-right font-mono">{finding.confidence.toFixed(2)}</td>
                      <td className="max-w-md text-2xs text-ink-muted">
                        {finding.evidence?.map((item, index) => (
                          <div key={index}>
                            <span className="text-ink-faint">{item.field}:</span> {item.value}
                          </div>
                        ))}
                        {finding.suggested_investigation && (
                          <div className="mt-1 text-ink-faint">→ {finding.suggested_investigation}</div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-5">
              <EmptyState title="No deterministic rule matched" description="This does not prove the run was correct." />
            </div>
          )}
        </Card>
      )}

      {tab === 'review' && (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="space-y-4">
            <Card title="Controlled fixture experiments" subtitle="Only allow-listed deterministic local validators run">
              {run.experiments?.length ? (
                <div className="space-y-3">
                  {run.experiments.map((experiment) => (
                    <ExperimentCard key={experiment.id} experiment={experiment} />
                  ))}
                </div>
              ) : (
                <EmptyState
                  icon="⚗"
                  title="No experiment recorded yet"
                  description="Replace an upstream output with a recorded fixture and rerun the downstream deterministic checks."
                />
              )}
              <ExperimentRunner runId={run.run_id} spans={spans} onDone={detail.reload} />
            </Card>
          </div>
          <div className="space-y-4">
            <AnnotationPanel runId={run.run_id} annotations={run.annotations ?? []} onSaved={detail.reload} />
            <Card title="Run record" subtitle="Provenance and environment metadata">
              <JsonBlock
                label="Show run metadata"
                value={{
                  run_id: run.run_id,
                  trace_id: run.trace_id,
                  task_id: run.task_id,
                  source_format: run.source_format,
                  imported_at: run.imported_at,
                  environment: run.environment,
                  evaluation_results: run.evaluation_results,
                }}
              />
            </Card>
          </div>
        </div>
      )}
    </div>
  )
}

function ExperimentRunner({
  runId,
  spans,
  onDone,
}: {
  runId: string
  spans: Span[]
  onDone: () => void
}) {
  const candidates = useMemo(
    () => spans.filter((span) => span.status === 'error' || span.status === 'timeout' || span.action_type === 'tool'),
    [spans],
  )
  const [spanId, setSpanId] = useState(candidates[0]?.span_id ?? '')
  const [fixture, setFixture] = useState('{\n  "orders": []\n}')
  const [title, setTitle] = useState('Validate corrected output fixture')
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { push } = useToast()

  const run = async () => {
    setError(null)
    if (!confirm) {
      setError('Confirm that this isolated local fixture check has no side effects.')
      return
    }
    let parsed: unknown
    try {
      parsed = JSON.parse(fixture)
    } catch {
      setError('The fixture must be valid JSON.')
      return
    }
    setBusy(true)
    try {
      const result = await api<{ result: { status: string } }>('/api/v1/experiments/counterfactual', {
        method: 'POST',
        body: {
          run_id: runId,
          source_span_id: spanId,
          replacement_output: parsed,
          title,
          confirm_side_effect_free: true,
        },
      })
      push(`Fixture check: ${result.result?.status ?? 'completed'}`)
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-lg border border-line bg-surface-sunken p-4">
      <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">Run a safe fixture check</div>
      <p className="mt-1 text-2xs leading-relaxed text-ink-muted">
        No imported tool, network call, or external side effect is executed. Only allow-listed deterministic validators run
        against the recorded fixture.
      </p>
      <div className="mt-3 space-y-2">
        <label className="block text-2xs font-medium text-ink-muted">
          Source span
          <select className="field mt-1" value={spanId} onChange={(event) => setSpanId(event.target.value)}>
            {candidates.map((span) => (
              <option key={span.span_id} value={span.span_id}>
                {span.name} · {span.status} · {shortId(span.span_id)}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-2xs font-medium text-ink-muted">
          Replacement fixture (JSON)
          <textarea
            className="field mt-1 font-mono text-2xs"
            rows={5}
            value={fixture}
            onChange={(event) => setFixture(event.target.value)}
          />
        </label>
        <label className="block text-2xs font-medium text-ink-muted">
          Experiment name
          <input className="field mt-1" value={title} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <label className="flex items-start gap-2 text-2xs text-ink-muted">
          <input
            type="checkbox"
            checked={confirm}
            onChange={(event) => setConfirm(event.target.checked)}
            className="mt-0.5 accent-[rgb(var(--accent))]"
          />
          I approve this isolated local fixture check. No external actions will be called.
        </label>
        {error && <p className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-2xs text-danger">{error}</p>}
        <button type="button" className="btn btn-primary" onClick={() => void run()} disabled={busy || !spanId}>
          {busy ? 'Running local check…' : 'Run safe fixture check'}
        </button>
      </div>
    </div>
  )
}

function AnnotationPanel({
  runId,
  annotations,
  onSaved,
}: {
  runId: string
  annotations: Array<{ id: number; author: string; note: string; disposition?: string | null; created_at: string }>
  onSaved: () => void
}) {
  const [author, setAuthor] = useState('reviewer')
  const [note, setNote] = useState('')
  const [disposition, setDisposition] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { push } = useToast()

  const save = async () => {
    if (!note.trim()) return
    setBusy(true)
    setError(null)
    try {
      await api(`/api/v1/runs/${encodeURIComponent(runId)}/annotations`, {
        method: 'POST',
        body: { author, note, disposition: disposition || null },
      })
      setNote('')
      setDisposition('')
      push('Reviewer annotation saved')
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="Reviewer annotations" subtitle="Append-only notes; original evidence is never modified">
      <div className="space-y-2">
        <input className="field" value={author} onChange={(event) => setAuthor(event.target.value)} placeholder="Reviewer name" />
        <textarea
          className="field"
          rows={3}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Add an evidence-based note…"
          aria-label="Annotation note"
        />
        <input
          className="field"
          value={disposition}
          onChange={(event) => setDisposition(event.target.value)}
          placeholder="Disposition (optional): confirmed / needs review"
        />
        {error && <p className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-2xs text-danger">{error}</p>}
        <button type="button" className="btn btn-sm" onClick={() => void save()} disabled={busy || !note.trim()}>
          ＋ Add annotation
        </button>
      </div>
      <ul className="mt-4 space-y-2">
        {annotations.length ? (
          annotations.map((annotation) => (
            <li key={annotation.id} className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-2xs">
              <div className="flex items-center gap-2">
                <strong className="text-ink">{annotation.author}</strong>
                {annotation.disposition && <Badge tone="info">{annotation.disposition}</Badge>}
                <span className="ml-auto text-ink-faint">{timeAgo(annotation.created_at)}</span>
              </div>
              <p className="mt-1 leading-relaxed text-ink-muted">{annotation.note}</p>
            </li>
          ))
        ) : (
          <li className="text-2xs text-ink-faint">No annotations recorded yet.</li>
        )}
      </ul>
    </Card>
  )
}
