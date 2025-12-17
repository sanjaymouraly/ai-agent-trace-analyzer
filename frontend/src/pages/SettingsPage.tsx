import { useState } from 'react'
import { api } from '../lib/api'
import { fmtUsd } from '../lib/format'
import type { AppSettings, LlmStatus } from '../lib/types'
import { useAsync } from '../lib/useAsync'
import { useToast } from '../components/Toast'
import { Badge, Callout, Card, ErrorState, Skeleton } from '../components/ui'

export default function SettingsPage() {
  const { push } = useToast()
  const settings = useAsync<AppSettings>(() => api('/api/v1/settings'), [])
  const llm = useAsync<LlmStatus>(() => api('/api/v1/llm/status'), [])
  const [provider, setProvider] = useState('')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [discovered, setDiscovered] = useState<string[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const current = llm.data
  const effectiveProvider = provider || current?.provider || 'disabled'
  const effectiveModel = model || current?.model || ''

  const runAction = async (name: string, fn: () => Promise<void>) => {
    setBusy(name)
    setError(null)
    try {
      await fn()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const saveSelection = () =>
    runAction('selection', async () => {
      await api('/api/v1/llm/config', { method: 'PUT', body: { provider: effectiveProvider, model: effectiveModel } })
      push('Provider selection saved')
      llm.reload()
      settings.reload()
    })

  const saveCredential = () =>
    runAction('credential', async () => {
      await api('/api/v1/llm/credentials', {
        method: 'POST',
        body: {
          provider: effectiveProvider,
          api_key: apiKey || null,
          base_url: baseUrl || null,
          model: model || null,
        },
      })
      setApiKey('')
      setBaseUrl('')
      push('Credential stored (encrypted). It is never returned by the API.')
      llm.reload()
    })

  const clearCredential = () =>
    runAction('credential', async () => {
      await api(`/api/v1/llm/credentials/${effectiveProvider}`, { method: 'DELETE' })
      push('Stored credential removed')
      llm.reload()
    })

  const discover = () =>
    runAction('discover', async () => {
      const response = await api<{ models: string[] }>('/api/v1/llm/models')
      setDiscovered(response.models)
      if (!model && response.models.length) setModel(response.models[0])
      push(`Discovered ${response.models.length} model(s)`)
    })

  const testConnection = () =>
    runAction('test', async () => {
      const response = await api<{ ok: boolean; model_count: number }>('/api/v1/llm/test-connection', { method: 'POST' })
      push(`Connection OK — ${response.model_count} model(s) available`)
    })

  const toggleRedaction = (enabled: boolean) =>
    runAction('redaction', async () => {
      await api('/api/v1/settings/redaction', {
        method: 'PUT',
        body: { enabled, confirm_sensitive: !enabled },
      })
      push(enabled ? 'Payload redaction enabled' : 'Payload redaction disabled — sensitive fields may be visible')
      settings.reload()
    })

  if (settings.error) return <ErrorState message={settings.error} onRetry={settings.reload} />
  if (!settings.data) return <Skeleton className="h-96" />

  const app = settings.data
  const providerOption = current?.providers.find((item) => item.id === effectiveProvider)

  return (
    <div className="space-y-5">
      <div>
        <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">Configuration</div>
        <h1 className="mt-1 text-xl font-semibold tracking-tight">Settings</h1>
        <p className="mt-1 max-w-2xl text-xs leading-relaxed text-ink-muted">
          Integrations, provider configuration, redaction controls, data retention and connection testing. Secrets stay on
          the server.
        </p>
      </div>

      {error && <Callout tone="danger" title="Action failed">{error}</Callout>}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Deployment" subtitle="Runtime mode and storage">
          <dl className="space-y-2 text-2xs">
            {[
              ['Application', `${app.app_name} v${app.version}`],
              ['Environment', app.environment],
              ['Mode', app.mode],
              ['Authentication', app.authentication_enabled ? 'Bearer token required' : 'local single-user mode'],
              ['Workspace', app.workspace_id],
              ['Database', app.storage.backend],
              ['Background queue', app.storage.redis_queue ? 'Redis' : 'in-process fallback'],
              ['Max upload', `${(app.limits.max_upload_bytes / 1024 / 1024).toFixed(1)} MB`],
              ['Max spans / run', String(app.limits.max_spans_per_run)],
              ['Rate limit', `${app.limits.rate_limit_per_minute} req/min`],
              ['Retention', app.retention],
            ].map(([label, value]) => (
              <div key={label} className="flex flex-wrap justify-between gap-2 border-b border-line/50 pb-1.5 last:border-0">
                <dt className="text-ink-muted">{label}</dt>
                <dd className="text-right text-ink">{value}</dd>
              </div>
            ))}
          </dl>
        </Card>

        <Card title="Privacy & redaction" subtitle="Control what payload fields the API returns">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-xs font-medium text-ink">Redact sensitive fields</div>
              <p className="mt-0.5 text-2xs leading-relaxed text-ink-muted">
                Passwords, tokens, API keys, cookies and private keys are replaced with [REDACTED] in API responses and
                exports. This is best-effort pattern redaction, not a data-loss-prevention system.
              </p>
            </div>
            <label className="flex items-center gap-2 text-2xs text-ink-muted">
              <input
                type="checkbox"
                checked={app.redaction_enabled}
                onChange={(event) => void toggleRedaction(event.target.checked)}
                className="accent-[rgb(var(--accent))]"
              />
              {app.redaction_enabled ? 'Enabled' : 'Disabled'}
            </label>
          </div>
          {app.redaction_enabled ? null : (
            <Callout tone="danger" title="Redaction disabled">
              Sensitive trace payloads may now appear in API responses and exported reports.
            </Callout>
          )}
        </Card>
      </div>

      <Card
        title="AI provider"
        subtitle="Optional. Deterministic analysis, comparison and root-cause investigation never depend on it."
        actions={<Badge tone={current?.ready ? 'ok' : 'muted'}>{current?.ready ? 'ready' : 'not configured'}</Badge>}
      >
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="space-y-3">
            <label className="block text-2xs font-medium text-ink-muted">
              Provider
              <select
                className="field mt-1"
                value={effectiveProvider}
                onChange={(event) => {
                  setProvider(event.target.value)
                  setModel('')
                  setDiscovered([])
                }}
              >
                {(current?.providers ?? []).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                    {item.credential_present ? ' — configured' : ''}
                  </option>
                ))}
              </select>
            </label>
            {providerOption && <p className="text-2xs leading-relaxed text-ink-muted">{providerOption.setup_hint}</p>}

            <label className="block text-2xs font-medium text-ink-muted">
              Model
              <input
                className="field mt-1"
                value={effectiveModel}
                onChange={(event) => setModel(event.target.value)}
                placeholder="e.g. gpt-4.1-mini"
                list="model-options"
              />
            </label>
            <datalist id="model-options">
              {discovered.map((item) => (
                <option key={item} value={item} />
              ))}
            </datalist>

            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn btn-sm" onClick={() => void saveSelection()} disabled={busy === 'selection'}>
                {busy === 'selection' ? 'Saving…' : 'Save selection'}
              </button>
              <button type="button" className="btn btn-sm" onClick={() => void discover()} disabled={busy === 'discover'}>
                {busy === 'discover' ? 'Discovering…' : 'Discover models'}
              </button>
              <button type="button" className="btn btn-sm" onClick={() => void testConnection()} disabled={busy === 'test'}>
                {busy === 'test' ? 'Testing…' : 'Test connection'}
              </button>
            </div>

            {discovered.length > 0 && (
              <div>
                <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">Discovered models</div>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {discovered.slice(0, 24).map((item) => (
                    <button
                      key={item}
                      type="button"
                      onClick={() => setModel(item)}
                      className="chip transition hover:border-accent hover:text-accent"
                    >
                      {item}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="space-y-3">
            <div className="rounded-lg border border-line bg-surface-sunken p-3 text-2xs leading-relaxed text-ink-muted">
              <strong className="text-ink">Connect a provider</strong>
              <p className="mt-1">
                Credentials can come from server-side environment variables (recommended for deployments) or be entered
                here. Values entered here are encrypted with AES-GCM before they are stored and are write-only: the API
                never returns them.
              </p>
            </div>
            <label className="block text-2xs font-medium text-ink-muted">
              API key
              <input
                className="field mt-1"
                type="password"
                autoComplete="off"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder="sk-… (stored encrypted)"
              />
            </label>
            <label className="block text-2xs font-medium text-ink-muted">
              Base URL (custom OpenAI-compatible endpoints)
              <input
                className="field mt-1"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder="https://api.example.com/v1"
              />
            </label>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="btn btn-sm btn-primary"
                onClick={() => void saveCredential()}
                disabled={busy === 'credential' || (!apiKey && !baseUrl && !model)}
              >
                {busy === 'credential' ? 'Storing…' : 'Store credential'}
              </button>
              <button type="button" className="btn btn-sm btn-danger" onClick={() => void clearCredential()} disabled={busy === 'credential'}>
                Remove stored credential
              </button>
            </div>
            {current && (
              <div className="rounded-lg border border-line bg-surface-sunken px-3 py-2 text-2xs text-ink-muted">
                <div className="flex justify-between">
                  <span>Credential source</span>
                  <span className="text-ink">{current.credential_source}</span>
                </div>
                <div className="flex justify-between">
                  <span>Selected model</span>
                  <span className="text-ink">{current.model || '—'}</span>
                </div>
                <div className="flex justify-between">
                  <span>Cost spent today</span>
                  <span className="text-ink">{fmtUsd(current.cost_spent_today_usd)}</span>
                </div>
              </div>
            )}
          </div>
        </div>
      </Card>

      {current && (
        <Card title="Reference pricing" subtitle="Used for cost estimates and budget enforcement">
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-lg border border-line bg-surface-sunken px-3 py-2 text-2xs">
              <div className="font-semibold text-ink">Retrieved</div>
              <div className="text-ink-muted">{current.pricing.as_of}</div>
            </div>
            <div className="rounded-lg border border-line bg-surface-sunken px-3 py-2 text-2xs">
              <div className="font-semibold text-ink">Sources</div>
              {Object.entries(current.pricing.sources).map(([name, url]) => (
                <div key={name} className="truncate text-ink-muted" title={url}>
                  {name}: {url}
                </div>
              ))}
            </div>
            <div className="rounded-lg border border-line bg-surface-sunken px-3 py-2 text-2xs">
              <div className="font-semibold text-ink">Overrides</div>
              <div className="text-ink-muted">
                {current.pricing.override_env} · {current.pricing.pricing_file_env}
              </div>
              <div className="mt-1 text-ink-faint">{current.pricing.note}</div>
            </div>
          </div>
        </Card>
      )}
    </div>
  )
}
