import { useState } from 'react'
import { api } from '../lib/api'
import { fmtNumber, fmtUsd, shortId } from '../lib/format'
import type { LlmStatus, RunsResponse } from '../lib/types'
import { useAsync } from '../lib/useAsync'
import { useToast } from '../components/Toast'
import { Badge, Callout, Card, EmptyState, ErrorState, Skeleton } from '../components/ui'

interface AssistResponse {
  answer: string
  provider: string
  model: string
  evidence_span_ids: string[]
  citation_check: { valid_span_ids: string[]; unrecognized_ids: string[]; note: string }
  usage: { input_tokens: number | null; output_tokens: number | null; total_tokens: number | null }
  cost_estimate: { estimated_cost_usd: number | null; pricing_source: string; as_of?: string | null }
  limitations: string[]
}

export default function AssistantPage() {
  const [runId, setRunId] = useState('')
  const [question, setQuestion] = useState('')
  const [consent, setConsent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<AssistResponse | null>(null)
  const [estimate, setEstimate] = useState<{ estimated_cost_usd: number | null; pricing_source: string; estimate_basis: string } | null>(null)
  const { push } = useToast()

  const status = useAsync<LlmStatus>(() => api('/api/v1/llm/status'), [])
  const runs = useAsync<RunsResponse>(() => api('/api/v1/runs', { query: { limit: 100 } }), [])

  const ask = async () => {
    setError(null)
    if (!consent) {
      setError('Authorize the redacted evidence transfer for this request first.')
      return
    }
    setBusy(true)
    try {
      const response = await api<AssistResponse>('/api/v1/llm/assist', {
        method: 'POST',
        body: { run_id: runId, question, authorize_external_transfer: true },
      })
      setResult(response)
      setConsent(false)
      push('Advisory response received — verify every claim against the cited span IDs.')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const preview = async () => {
    setError(null)
    try {
      const response = await api<{ estimate: { estimated_cost_usd: number | null; pricing_source: string; estimate_basis: string } }>(
        '/api/v1/llm/estimate',
        { method: 'POST', body: { run_id: runId, question } },
      )
      setEstimate(response.estimate)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  if (status.error) return <ErrorState message={status.error} onRetry={status.reload} />

  const llm = status.data
  const ready = Boolean(llm?.ready)

  return (
    <div className="space-y-5">
      <div>
        <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">Optional assistance</div>
        <h1 className="mt-1 text-xl font-semibold tracking-tight">AI debugging assistant</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-ink-muted">
          Ask a question about a stored execution. A bounded, redacted evidence subset is sent to the configured
          provider. The response is advisory, cites span IDs, and never becomes a trace fact.
        </p>
      </div>

      {!llm ? (
        <Skeleton className="h-40" />
      ) : (
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="space-y-4 lg:col-span-2">
            <Card
              title="Investigation question"
              subtitle="Ask about the earliest error, a hypothesis, or the evidence that supports or contradicts it"
            >
              <div className="space-y-3">
                <label className="block text-2xs font-medium text-ink-muted">
                  Execution
                  <select className="field mt-1" value={runId} onChange={(event) => setRunId(event.target.value)}>
                    <option value="">Select a run…</option>
                    {(runs.data?.items ?? []).map((run) => (
                      <option key={run.run_id} value={run.run_id}>
                        {run.task} · {run.status} · {shortId(run.run_id)}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block text-2xs font-medium text-ink-muted">
                  Question
                  <textarea
                    className="field mt-1"
                    rows={4}
                    value={question}
                    onChange={(event) => setQuestion(event.target.value)}
                    placeholder="What evidence supports or contradicts the leading root-cause hypothesis?"
                    maxLength={1200}
                  />
                </label>
                <label className="flex items-start gap-2 text-2xs leading-relaxed text-ink-muted">
                  <input
                    type="checkbox"
                    checked={consent}
                    onChange={(event) => setConsent(event.target.checked)}
                    disabled={!ready}
                    className="mt-0.5 accent-[rgb(var(--accent))]"
                  />
                  I authorize sending the redacted evidence subset and my redacted question to{' '}
                  <strong className="text-ink">{llm.provider_label}</strong>. I understand the provider may process the
                  data and may charge my account.
                </label>
                {error && <p className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-2xs text-danger">{error}</p>}
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={() => void ask()}
                    disabled={!ready || busy || !runId || question.trim().length < 3}
                  >
                    {busy ? 'Contacting provider…' : 'Ask configured model'}
                  </button>
                  <button type="button" className="btn" onClick={() => void preview()} disabled={!ready || !runId}>
                    Estimate cost first
                  </button>
                </div>
                {!ready && (
                  <Callout tone="warn" title="AI assistance is not configured">
                    Configure a provider credential, base URL and model in Settings to enable optional assistance. All
                    deterministic analysis, comparison and root-cause investigation continue to work offline.
                  </Callout>
                )}
              </div>
            </Card>

            {estimate && (
              <Callout tone="info" title="Pre-flight cost estimate">
                <div>
                  Estimated cost: <strong>{fmtUsd(estimate.estimated_cost_usd)}</strong> — {estimate.pricing_source}
                </div>
                <div className="mt-1 text-2xs text-ink-muted">{estimate.estimate_basis}</div>
              </Callout>
            )}

            {result && (
              <Card
                title="Advisory response"
                subtitle={`${result.provider} / ${result.model}`}
                actions={<Badge tone="info">advisory only</Badge>}
              >
                <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-surface-sunken p-4 text-xs leading-relaxed text-ink">
                  {result.answer}
                </pre>
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <div className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-2xs">
                    <div className="font-semibold text-ink">Recognised citations</div>
                    <div className="mono mt-1 text-ink-muted">
                      {result.citation_check.valid_span_ids.length
                        ? result.citation_check.valid_span_ids.join(', ')
                        : 'none recognised'}
                    </div>
                    {result.citation_check.unrecognized_ids.length > 0 && (
                      <div className="mono mt-1 text-warn">unrecognised: {result.citation_check.unrecognized_ids.join(', ')}</div>
                    )}
                    <div className="mt-1 text-ink-faint">{result.citation_check.note}</div>
                  </div>
                  <div className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-2xs">
                    <div className="font-semibold text-ink">Usage & cost</div>
                    <div className="mt-1 text-ink-muted">
                      {fmtNumber(result.usage.input_tokens)} in / {fmtNumber(result.usage.output_tokens)} out tokens
                    </div>
                    <div className="text-ink-muted">Estimated cost {fmtUsd(result.cost_estimate.estimated_cost_usd)}</div>
                    <div className="text-ink-faint">{result.cost_estimate.pricing_source}</div>
                  </div>
                </div>
                <ul className="mt-3 space-y-1 text-2xs text-ink-faint">
                  {result.limitations.map((item, index) => (
                    <li key={index}>— {item}</li>
                  ))}
                </ul>
              </Card>
            )}
          </div>

          <div className="space-y-4">
            <Card title="Provider status" subtitle="Configuration is server-side; keys never reach the browser">
              {llm.providers.map((provider) => (
                <div key={provider.id} className="border-b border-line/60 py-2 last:border-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-ink">{provider.label}</span>
                    <Badge tone={provider.credential_present ? 'ok' : 'muted'}>
                      {provider.credential_present ? 'configured' : 'not configured'}
                    </Badge>
                  </div>
                  <p className="mt-0.5 text-2xs leading-relaxed text-ink-muted">{provider.setup_hint}</p>
                </div>
              ))}
            </Card>

            <Card title="Budget & limits" subtitle="Enforced server-side before any outbound call">
              <dl className="space-y-2 text-2xs">
                <div className="flex justify-between">
                  <dt className="text-ink-muted">Requests today</dt>
                  <dd className="font-mono text-ink">
                    {llm.calls_today} / {llm.daily_limit}
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-ink-muted">Requests per minute</dt>
                  <dd className="font-mono text-ink">{llm.per_minute_limit}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-ink-muted">Daily budget</dt>
                  <dd className="font-mono text-ink">{fmtUsd(llm.daily_budget_usd)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-ink-muted">Monthly budget</dt>
                  <dd className="font-mono text-ink">{fmtUsd(llm.monthly_budget_usd)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-ink-muted">Spent today</dt>
                  <dd className="font-mono text-ink">{fmtUsd(llm.cost_spent_today_usd)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-ink-muted">Max output tokens</dt>
                  <dd className="font-mono text-ink">{llm.max_completion_tokens}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-ink-muted">Timeout</dt>
                  <dd className="font-mono text-ink">{llm.timeout_seconds}s</dd>
                </div>
              </dl>
              <p className="mt-3 text-2xs leading-relaxed text-ink-faint">{llm.cost_visibility}</p>
            </Card>

            {!runs.data?.items.length && (
              <EmptyState title="No executions to ask about" description="Import traces first." />
            )}
          </div>
        </div>
      )}
    </div>
  )
}
