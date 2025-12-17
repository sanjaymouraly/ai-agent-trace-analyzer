import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import clsx from 'clsx'
import { api, getToken, setToken } from '../lib/api'
import { useTheme } from '../lib/theme'
import { useToast } from './Toast'

interface NavEntry {
  to: string
  label: string
  icon: string
  group: 'workspace' | 'development'
}

const NAV: NavEntry[] = [
  { to: '/overview', label: 'Overview', icon: '◫', group: 'workspace' },
  { to: '/runs', label: 'Trace explorer', icon: '≋', group: 'workspace' },
  { to: '/tools', label: 'Tool failure analytics', icon: '⌁', group: 'workspace' },
  { to: '/compare', label: 'Compare trajectories', icon: '⇄', group: 'workspace' },
  { to: '/assistant', label: 'AI debugging assistant', icon: '✧', group: 'workspace' },
  { to: '/settings', label: 'Settings', icon: '⚙', group: 'development' },
  { to: '/audit', label: 'Audit log', icon: '▤', group: 'development' },
]

const PAGE_TITLES: Record<string, string> = {
  '/overview': 'Overview',
  '/runs': 'Trace explorer',
  '/tools': 'Tool failure analytics',
  '/compare': 'Compare trajectories',
  '/assistant': 'AI debugging assistant',
  '/settings': 'Settings',
  '/audit': 'Audit log',
}

function HealthPill() {
  const [state, setState] = useState<'checking' | 'online' | 'offline'>('checking')
  const [label, setLabel] = useState('Connecting')

  const check = async () => {
    try {
      const response = await api<{ status: string; version: string }>('/api/v1/health')
      setState('online')
      setLabel(`API v${response.version}`)
    } catch {
      setState('offline')
      setLabel('API offline')
    }
  }

  useEffect(() => {
    void check()
    const timer = window.setInterval(() => void check(), 30000)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <div className="hidden items-center gap-2 text-2xs text-ink-muted sm:flex">
      <span
        className={clsx(
          'h-2 w-2 rounded-full',
          state === 'online' ? 'bg-ok' : state === 'offline' ? 'bg-danger' : 'bg-warn',
        )}
        aria-hidden="true"
      />
      {label}
    </div>
  )
}

function TokenDialog({ onClose }: { onClose: () => void }) {
  const [value, setValue] = useState(getToken())
  const ref = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    ref.current?.showModal()
  }, [])

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      className="m-auto w-[min(26rem,calc(100vw-2rem))] rounded-xl border border-line bg-surface-raised p-0 text-ink shadow-2xl backdrop:bg-black/50"
    >
      <div className="border-b border-line px-5 py-4">
        <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">API access</div>
        <h2 className="mt-1 text-base font-semibold">Connect to the API</h2>
      </div>
      <form
        className="space-y-4 px-5 py-4"
        onSubmit={(event) => {
          event.preventDefault()
          setToken(value.trim())
          onClose()
        }}
      >
        <p className="text-xs leading-relaxed text-ink-muted">
          If <code className="mono">APP_AUTH_TOKEN</code> is configured on the server, paste it here. The token is kept
          in this browser tab only and is never written to disk.
        </p>
        <label className="block text-xs font-medium" htmlFor="token-input">
          Bearer token
        </label>
        <input
          id="token-input"
          className="field"
          type="password"
          autoComplete="off"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="Local API token"
        />
        <div className="flex justify-end gap-2">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary">
            Save token
          </button>
        </div>
      </form>
    </dialog>
  )
}

function ImportDialog({ onClose, onImported }: { onClose: () => void; onImported: () => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { push } = useToast()
  const ref = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    ref.current?.showModal()
  }, [])

  const submit = async () => {
    if (!file) {
      setError('Choose a .json or .jsonl trace file first.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const body = new FormData()
      body.append('file', file)
      const result = await api<{ accepted: number; inserted: string[]; updated: string[] }>(
        '/api/v1/ingest/file',
        { method: 'POST', body },
      )
      push(`Imported ${result.accepted} run(s): ${result.inserted.length} new, ${result.updated.length} updated.`)
      onImported()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      className="m-auto w-[min(32rem,calc(100vw-2rem))] rounded-xl border border-line bg-surface-raised p-0 text-ink shadow-2xl backdrop:bg-black/50"
    >
      <div className="border-b border-line px-5 py-4">
        <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">Ingestion</div>
        <h2 className="mt-1 text-base font-semibold">Import execution traces</h2>
      </div>
      <div className="space-y-4 px-5 py-4">
        <p className="text-xs leading-relaxed text-ink-muted">
          Upload a canonical JSON/JSONL trace, an OTLP JSON export, or a LangChain/LangGraph run export. Imported
          content is treated as data and is never executed.
        </p>
        <input
          type="file"
          accept=".json,.jsonl,application/json"
          aria-label="Trace file"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          className="block w-full text-xs text-ink-muted file:mr-3 file:rounded-md file:border-0 file:bg-surface-sunken file:px-3 file:py-1.5 file:text-xs file:text-ink"
        />
        {error && <p className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">{error}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" onClick={() => void submit()} disabled={busy}>
            {busy ? 'Importing…' : 'Import traces'}
          </button>
        </div>
      </div>
    </dialog>
  )
}

export default function AppShell() {
  const { theme, toggle } = useTheme()
  const location = useLocation()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [tokenDialog, setTokenDialog] = useState(false)
  const [importDialog, setImportDialog] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    setSidebarOpen(false)
  }, [location.pathname])

  const pageTitle =
    PAGE_TITLES[location.pathname] ||
    (location.pathname.startsWith('/runs/') ? 'Execution investigation' : 'TraceLens')

  return (
    <div className="flex h-full min-h-screen bg-surface">
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/50 lg:hidden"
          onClick={() => setSidebarOpen(false)}
          aria-hidden="true"
        />
      )}
      <aside
        className={clsx(
          'fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-line bg-surface-raised transition-transform lg:static lg:translate-x-0',
          sidebarOpen ? 'translate-x-0' : '-translate-x-full',
        )}
        aria-label="Primary navigation"
      >
        <div className="flex items-center gap-3 border-b border-line px-5 py-4">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent/15 text-accent" aria-hidden="true">
            <svg viewBox="0 0 36 36" className="h-5 w-5" fill="currentColor">
              <path d="M8 6h20v6H8zM8 15h11v6H8zM8 24h20v6H8z" />
            </svg>
          </span>
          <div className="min-w-0">
            <div className="text-sm font-semibold tracking-tight">TraceLens</div>
            <div className="text-2xs uppercase tracking-wider text-ink-faint">Agent observability</div>
          </div>
        </div>

        <div className="border-b border-line px-5 py-3">
          <div className="flex items-center gap-2 text-xs text-ink-muted">
            <span className="h-2 w-2 rounded-full bg-ok" aria-hidden="true" />
            <span>Local workspace</span>
            <span className="ml-auto chip">single user</span>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto px-3 py-4">
          <div className="mb-2 px-2 text-2xs font-semibold uppercase tracking-wider text-ink-faint">Workspace</div>
          {NAV.filter((entry) => entry.group === 'workspace').map((entry) => (
            <NavLink
              key={entry.to}
              to={entry.to}
              className={({ isActive }) =>
                clsx(
                  'mb-0.5 flex items-center gap-2.5 rounded-lg px-3 py-2 text-xs font-medium transition',
                  isActive ? 'bg-accent/12 text-accent' : 'text-ink-muted hover:bg-surface-sunken hover:text-ink',
                )
              }
            >
              <span className="w-4 text-center" aria-hidden="true">
                {entry.icon}
              </span>
              {entry.label}
            </NavLink>
          ))}
          <div className="mb-2 mt-5 px-2 text-2xs font-semibold uppercase tracking-wider text-ink-faint">Development</div>
          {NAV.filter((entry) => entry.group === 'development').map((entry) => (
            <NavLink
              key={entry.to}
              to={entry.to}
              className={({ isActive }) =>
                clsx(
                  'mb-0.5 flex items-center gap-2.5 rounded-lg px-3 py-2 text-xs font-medium transition',
                  isActive ? 'bg-accent/12 text-accent' : 'text-ink-muted hover:bg-surface-sunken hover:text-ink',
                )
              }
            >
              <span className="w-4 text-center" aria-hidden="true">
                {entry.icon}
              </span>
              {entry.label}
            </NavLink>
          ))}
          <a
            href="/docs"
            target="_blank"
            rel="noreferrer"
            className="mb-0.5 flex items-center gap-2.5 rounded-lg px-3 py-2 text-xs font-medium text-ink-muted transition hover:bg-surface-sunken hover:text-ink"
          >
            <span className="w-4 text-center" aria-hidden="true">
              ▤
            </span>
            API reference
            <span className="ml-auto text-2xs">↗</span>
          </a>
        </nav>

        <div className="border-t border-line p-4">
          <div className="rounded-lg border border-line bg-surface-sunken px-3 py-2.5">
            <div className="flex items-center gap-2 text-2xs font-semibold text-ink">
              <span className="h-1.5 w-1.5 rounded-full bg-ok" aria-hidden="true" />
              Offline analysis
            </div>
            <p className="mt-0.5 text-2xs text-ink-muted">Deterministic rules engine · always available</p>
          </div>
          <button
            type="button"
            onClick={() => setTokenDialog(true)}
            className="mt-3 flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-2xs text-ink-muted transition hover:bg-surface-sunken"
          >
            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-surface-sunken text-2xs font-semibold text-ink">
              TL
            </span>
            <span className="flex-1">Local investigator</span>
            <span aria-hidden="true">•••</span>
          </button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex items-center gap-3 border-b border-line bg-surface/95 px-4 py-3 backdrop-blur lg:px-6">
          <button
            type="button"
            className="btn btn-ghost btn-sm lg:hidden"
            onClick={() => setSidebarOpen((open) => !open)}
            aria-label="Toggle navigation"
            aria-expanded={sidebarOpen}
          >
            ☰
          </button>
          <div className="flex min-w-0 items-center gap-2 text-xs text-ink-muted">
            <span className="hidden sm:inline">TraceLens</span>
            <span className="hidden sm:inline text-ink-faint">/</span>
            <strong className="truncate text-ink">{pageTitle}</strong>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <HealthPill />
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={toggle}
              title="Toggle light/dark theme"
              aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
            >
              {theme === 'dark' ? '☀' : '☾'}
            </button>
            <button type="button" className="btn btn-sm" onClick={() => setImportDialog(true)}>
              <span aria-hidden="true">＋</span> Import traces
            </button>
          </div>
        </header>

        <main className="min-w-0 flex-1 px-4 py-5 lg:px-6 lg:py-6" key={reloadKey}>
          <Outlet context={{ reload: () => setReloadKey((value) => value + 1) }} />
        </main>

        <footer className="border-t border-line px-4 py-3 text-2xs text-ink-faint lg:px-6">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>TraceLens · evidence first, not assertion.</span>
            <span>Inferred hypotheses are not causal proof.</span>
          </div>
        </footer>
      </div>

      {tokenDialog && <TokenDialog onClose={() => setTokenDialog(false)} />}
      {importDialog && (
        <ImportDialog
          onClose={() => setImportDialog(false)}
          onImported={() => setReloadKey((value) => value + 1)}
        />
      )}
    </div>
  )
}
